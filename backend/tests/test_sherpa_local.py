from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.providers.asr.realtime import create_realtime_session
from app.providers.asr.sherpa_local import (
    SherpaOnnxRealtimeSession,
    _RecognizerBundle,
    _format_hotwords,
    default_model_root,
    missing_finalizer_model_files,
    missing_model_files,
)
from app.schemas import ProviderConfigIn


class _FakeStream:
    def __init__(self) -> None:
        self.finished = False
        self.samples = 0

    def accept_waveform(self, _sample_rate: int, samples: np.ndarray) -> None:
        self.samples += len(samples)

    def input_finished(self) -> None:
        self.finished = True


class _FakeRecognizer:
    def __init__(self) -> None:
        self.results = ["这是", "这是测试"]
        self.index = 0
        self.reset_count = 0

    def is_ready(self, _stream: _FakeStream) -> bool:
        return self.index < len(self.results)

    def decode_stream(self, _stream: _FakeStream) -> None:
        self.index += 1

    def get_result(self, _stream: _FakeStream) -> str:
        return self.results[max(0, self.index - 1)] if self.index else ""

    def is_endpoint(self, _stream: _FakeStream) -> bool:
        return self.index == len(self.results)

    def reset(self, _stream: _FakeStream) -> None:
        self.reset_count += 1


class _FakeOfflineStream:
    def __init__(self) -> None:
        self.samples = 0
        self.result = SimpleNamespace(text="")

    def accept_waveform(self, _sample_rate: int, samples: np.ndarray) -> None:
        self.samples += len(samples)


class _FakeOfflineRecognizer:
    def create_stream(self) -> _FakeOfflineStream:
        return _FakeOfflineStream()

    def decode_stream(self, stream: _FakeOfflineStream) -> None:
        stream.result.text = "这是精校后的20个结果。"


def test_hotwords_are_deduplicated_and_tokenized() -> None:
    assert _format_hotwords(["三一", "树根互联", "三一", "React.js"]) == (
        "三 一/树 根 互 联/R e a c t j s"
    )


def test_out_of_vocabulary_hotword_is_skipped_without_disabling_valid_terms() -> None:
    tokens = {"三", "一", "树", "根"}
    assert _format_hotwords(["三一朗", "树根"], tokens) == "树 根"


def test_default_model_is_kept_outside_repository(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "speech-models"
    monkeypatch.setenv("SPEECH_TRAINER_MODEL_DIR", str(target))
    assert default_model_root() == target


def test_missing_model_files_reports_required_artifacts(tmp_path: Path) -> None:
    missing = missing_model_files(tmp_path)
    assert set(missing) == {
        "tokens.txt",
        "encoder.int8.onnx",
        "decoder.onnx",
        "joiner.int8.onnx",
    }


def test_missing_finalizer_files_reports_required_artifacts(tmp_path: Path) -> None:
    assert set(missing_finalizer_model_files(tmp_path)) == {
        "tokens.txt",
        "model.int8.onnx",
    }


def test_factory_keeps_local_and_cloud_as_separate_options() -> None:
    local = create_realtime_session(ProviderConfigIn(provider="sherpa_onnx"))
    cloud = create_realtime_session(
        ProviderConfigIn(provider="dashscope", api_key="test-key")
    )

    assert isinstance(local, SherpaOnnxRealtimeSession)
    assert cloud.__class__.__name__ == "RealtimeASRSession"


@pytest.mark.asyncio
async def test_local_session_emits_partial_then_final_on_endpoint() -> None:
    config = ProviderConfigIn(provider="sherpa_onnx")
    session = SherpaOnnxRealtimeSession(config)
    recognizer = _FakeRecognizer()
    stream = _FakeStream()
    session._bundle = _RecognizerBundle(recognizer=recognizer, lock=__import__("threading").RLock())
    session._stream = stream
    session._started = True

    partials: list[str] = []
    finals: list[str] = []
    session.on_partial = partials.append
    session.on_final = finals.append

    await session.push_audio(bytes(3200))

    assert partials == ["这是"]
    assert finals == ["这是测试。"]
    assert recognizer.reset_count == 1


def test_local_session_uses_offline_text_for_final_result() -> None:
    session = SherpaOnnxRealtimeSession(ProviderConfigIn(provider="sherpa_onnx"))
    session._finalizer_bundle = _RecognizerBundle(
        recognizer=_FakeOfflineRecognizer(),
        lock=__import__("threading").RLock(),
    )
    session._utterance_chunks = [np.ones(16000, dtype=np.float32)]

    assert session._refine_final_sync("这是流式的二十个结果") == "这是精校后的20个结果。"


@pytest.mark.asyncio
async def test_manual_flush_forces_final_without_closing_session() -> None:
    session = SherpaOnnxRealtimeSession(ProviderConfigIn(provider="sherpa_onnx"))
    recognizer = _FakeRecognizer()
    session._bundle = _RecognizerBundle(
        recognizer=recognizer,
        lock=__import__("threading").RLock(),
    )
    session._stream = _FakeStream()
    session._started = True
    finals: list[str] = []
    session.on_final = finals.append

    await session.flush_utterance()

    assert finals == ["这是测试。"]
    assert session._input_finished is False
    assert session._closed is False
