"""本地 sherpa-onnx 流式语音识别。

模型权重不随仓库分发。默认从用户数据目录读取，安装脚本负责从
sherpa-onnx 官方 GitHub Release 下载并校验文件摘要。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable

import numpy as np

from app.core.exceptions import ProviderError
from app.providers.base import BaseASRProvider, registry
from app.schemas import ProviderConfigIn

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
DEFAULT_MODEL = "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"
MODEL_ARCHIVE = f"{DEFAULT_MODEL}.tar.bz2"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"asr-models/{MODEL_ARCHIVE}"
)
MODEL_SHA256 = "5a2832047ea1f97dd0dc595b816c230c4bafad65cfc0341fa57517cadc50afd0"
MODEL_FILES = {
    "tokens": "tokens.txt",
    "encoder": "encoder.int8.onnx",
    "decoder": "decoder.onnx",
    "joiner": "joiner.int8.onnx",
}
FINALIZER_MODEL = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
FINALIZER_ARCHIVE = f"{FINALIZER_MODEL}.tar.bz2"
FINALIZER_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"asr-models/{FINALIZER_ARCHIVE}"
)
FINALIZER_SHA256 = "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e"
FINALIZER_FILES = {
    "tokens": "tokens.txt",
    "model": "model.int8.onnx",
}


def default_model_root() -> Path:
    """返回跨平台的用户级模型目录，不把大模型写入 Git 工作区。"""
    override = os.getenv("SPEECH_TRAINER_MODEL_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SpeechTrainer" / "models"
    if os.name == "nt":
        base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "SpeechTrainer" / "models"
    base = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "speech-trainer" / "models"


def resolve_model_dir(config: ProviderConfigIn | None = None) -> Path:
    """解析模型目录；本地 Provider 的 base_url 可作为高级路径覆盖。"""
    if config and config.base_url.strip():
        value = config.base_url.strip()
        if value.startswith(("http://", "https://", "ws://", "wss://")):
            raise ProviderError("本地语音识别的模型位置必须是本机目录")
        return Path(value).expanduser()
    model = (config.model.strip() if config else "") or DEFAULT_MODEL
    if model != DEFAULT_MODEL:
        raise ProviderError("当前版本尚未适配所选本地语音模型")
    return default_model_root() / model


def resolve_finalizer_model_dir(config: ProviderConfigIn | None = None) -> Path:
    """精校模型与流式模型放在同一用户级模型根目录。"""
    if config and config.base_url.strip():
        return resolve_model_dir(config).parent / FINALIZER_MODEL
    return default_model_root() / FINALIZER_MODEL


def missing_model_files(model_dir: Path) -> list[str]:
    return [filename for filename in MODEL_FILES.values() if not (model_dir / filename).is_file()]


def missing_finalizer_model_files(model_dir: Path) -> list[str]:
    return [
        filename
        for filename in FINALIZER_FILES.values()
        if not (model_dir / filename).is_file()
    ]


def local_model_ready(config: ProviderConfigIn | None = None) -> bool:
    try:
        return not missing_model_files(resolve_model_dir(config))
    except ProviderError:
        return False


def local_finalizer_ready(config: ProviderConfigIn | None = None) -> bool:
    try:
        return not missing_finalizer_model_files(resolve_finalizer_model_dir(config))
    except ProviderError:
        return False


def _format_hotwords(words: list[str], tokens: set[str] | None = None) -> str | None:
    """把真实词语转换为 cjkchar transducer 所需的逐 token 格式。"""
    phrases: list[str] = []
    seen: set[str] = set()
    for raw in words:
        compact = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", raw).strip()
        if not compact or compact in seen or len(compact) > 24:
            continue
        seen.add(compact)
        encoded = list(compact)
        if tokens is not None and any(token not in tokens for token in encoded):
            logger.warning("本地 ASR 跳过无法编码的热词：%s", compact)
            continue
        phrases.append(" ".join(encoded))
        if len(phrases) >= 64:
            break
    return "/".join(phrases) or None


@dataclass
class _RecognizerBundle:
    recognizer: object
    lock: threading.RLock


_recognizer_cache: dict[tuple[str, ...], _RecognizerBundle] = {}
_recognizer_cache_lock = threading.Lock()
_finalizer_cache: dict[tuple[str, ...], _RecognizerBundle] = {}
_finalizer_cache_lock = threading.Lock()


def _build_recognizer(model_dir: Path) -> _RecognizerBundle:
    missing = missing_model_files(model_dir)
    if missing:
        raise ProviderError("本地语音模型尚未安装，请先完成本地模型安装")
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise ProviderError("本地语音识别组件尚未安装") from exc

    paths = tuple(str((model_dir / MODEL_FILES[key]).resolve()) for key in MODEL_FILES)
    with _recognizer_cache_lock:
        cached = _recognizer_cache.get(paths)
        if cached is not None:
            return cached
        try:
            recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=paths[0],
                encoder=paths[1],
                decoder=paths[2],
                joiner=paths[3],
                num_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
                sample_rate=SAMPLE_RATE,
                enable_endpoint_detection=True,
                rule1_min_trailing_silence=2.4,
                rule2_min_trailing_silence=0.8,
                rule3_min_utterance_length=20.0,
                decoding_method="modified_beam_search",
                max_active_paths=4,
                hotwords_score=1.5,
                modeling_unit="cjkchar",
                provider="cpu",
            )
        except Exception as exc:
            raise ProviderError(f"本地语音模型加载失败：{exc}") from exc
        bundle = _RecognizerBundle(recognizer=recognizer, lock=threading.RLock())
        _recognizer_cache[paths] = bundle
        return bundle


def _build_finalizer(model_dir: Path) -> _RecognizerBundle:
    missing = missing_finalizer_model_files(model_dir)
    if missing:
        raise ProviderError("本地字幕精校模型尚未安装")
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise ProviderError("本地语音识别组件尚未安装") from exc

    paths = tuple(
        str((model_dir / FINALIZER_FILES[key]).resolve())
        for key in ("model", "tokens")
    )
    with _finalizer_cache_lock:
        cached = _finalizer_cache.get(paths)
        if cached is not None:
            return cached
        try:
            recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=paths[0],
                tokens=paths[1],
                num_threads=max(1, min(4, (os.cpu_count() or 2) // 2)),
                sample_rate=SAMPLE_RATE,
                language="zh",
                use_itn=True,
                provider="cpu",
            )
        except Exception as exc:
            raise ProviderError(f"本地字幕精校模型加载失败：{exc}") from exc
        bundle = _RecognizerBundle(recognizer=recognizer, lock=threading.RLock())
        _finalizer_cache[paths] = bundle
        return bundle


def _terminal_text(text: str) -> str:
    text = text.strip()
    if text and text[-1] not in "。！？!?；;":
        return f"{text}。"
    return text


class SherpaOnnxRealtimeSession:
    """与阿里云会话保持同一生命周期接口的本地流式识别会话。"""

    def __init__(self, config: ProviderConfigIn, hotwords: list[str] | None = None) -> None:
        self.config = config
        self.model_dir = resolve_model_dir(config)
        self.finalizer_model_dir = resolve_finalizer_model_dir(config)
        self.hotwords = hotwords or []
        self.on_partial: Callable[[str], None] = lambda _text: None
        self.on_final: Callable[[str], None] = lambda _text: None
        self.on_error: Callable[[str], None] = lambda _text: None
        self._bundle: _RecognizerBundle | None = None
        self._finalizer_bundle: _RecognizerBundle | None = None
        self._stream: object | None = None
        self._utterance_chunks: list[np.ndarray] = []
        self._last_partial = ""
        self._started = False
        self._closed = False
        self._input_finished = False
        self._operation_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._started:
            return
        try:
            self._bundle, self._stream, self._finalizer_bundle = await asyncio.to_thread(
                self._start_sync
            )
        except Exception as exc:
            error = exc if isinstance(exc, ProviderError) else ProviderError(str(exc))
            self._run_callback(self.on_error, str(error), "error")
            raise error
        self._started = True

    def _start_sync(
        self,
    ) -> tuple[_RecognizerBundle, object, _RecognizerBundle | None]:
        bundle = _build_recognizer(self.model_dir)
        token_lines = (self.model_dir / MODEL_FILES["tokens"]).read_text(
            encoding="utf-8"
        ).splitlines()
        tokens = {line.rsplit(" ", 1)[0] for line in token_lines if " " in line}
        hotwords = _format_hotwords(self.hotwords, tokens)
        with bundle.lock:
            stream = bundle.recognizer.create_stream(hotwords)
        finalizer = None
        if not missing_finalizer_model_files(self.finalizer_model_dir):
            try:
                finalizer = _build_finalizer(self.finalizer_model_dir)
            except ProviderError:
                logger.exception("本地字幕精校不可用，回退到流式定稿")
        return bundle, stream, finalizer

    async def push_audio(self, pcm: bytes) -> None:
        if not pcm or not self._started or self._closed or self._input_finished:
            return
        if len(pcm) % 2:
            pcm = pcm[:-1]
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        async with self._operation_lock:
            try:
                events = await asyncio.to_thread(self._decode_sync, samples, False)
            except Exception as exc:
                logger.exception("本地实时语音识别失败")
                self._run_callback(self.on_error, f"本地语音识别失败：{exc}", "error")
                return
        self._emit(events)

    async def finish(self) -> None:
        if not self._started or self._closed or self._input_finished:
            return
        self._input_finished = True
        tail = np.zeros(SAMPLE_RATE, dtype=np.float32)
        async with self._operation_lock:
            try:
                events = await asyncio.to_thread(self._decode_sync, tail, True)
            except Exception as exc:
                logger.exception("本地语音识别收尾失败")
                self._run_callback(self.on_error, f"本地语音识别收尾失败：{exc}", "error")
                return
        self._emit(events)

    async def flush_utterance(self) -> None:
        """在手动提交时用静音强制定稿，但保持会话可继续收音。"""
        if not self._started or self._closed or self._input_finished:
            return
        tail = np.zeros(int(SAMPLE_RATE * 0.9), dtype=np.float32)
        async with self._operation_lock:
            try:
                events = await asyncio.to_thread(
                    self._decode_sync,
                    tail,
                    False,
                    True,
                )
            except Exception as exc:
                logger.exception("本地语音识别手动定稿失败")
                self._run_callback(self.on_error, f"本地语音识别定稿失败：{exc}", "error")
                return
        self._emit(events)

    def _decode_sync(
        self,
        samples: np.ndarray,
        finish: bool,
        force_finalize: bool = False,
    ) -> list[tuple[str, str]]:
        assert self._bundle is not None and self._stream is not None
        recognizer = self._bundle.recognizer
        stream = self._stream
        events: list[tuple[str, str]] = []
        endpoint_reached = False
        if samples.size:
            self._utterance_chunks.append(samples.copy())
        with self._bundle.lock:
            stream.accept_waveform(SAMPLE_RATE, samples)
            if finish:
                stream.input_finished()
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
                text = recognizer.get_result(stream).strip()
                if recognizer.is_endpoint(stream):
                    endpoint_reached = True
                    if text:
                        events.append(("final", self._refine_final_sync(text)))
                    self._utterance_chunks.clear()
                    recognizer.reset(stream)
                    self._last_partial = ""
                elif text and text != self._last_partial:
                    self._last_partial = text
                    events.append(("partial", text))

            if (finish or force_finalize) and not endpoint_reached:
                text = recognizer.get_result(stream).strip()
                if text:
                    events.append(("final", self._refine_final_sync(text)))
                    self._last_partial = ""
                self._utterance_chunks.clear()
                if force_finalize:
                    recognizer.reset(stream)
        return events

    def _refine_final_sync(self, online_text: str) -> str:
        fallback = _terminal_text(online_text)
        if self._finalizer_bundle is None or not self._utterance_chunks:
            return fallback
        samples = np.concatenate(self._utterance_chunks)
        try:
            with self._finalizer_bundle.lock:
                stream = self._finalizer_bundle.recognizer.create_stream()
                stream.accept_waveform(SAMPLE_RATE, samples)
                self._finalizer_bundle.recognizer.decode_stream(stream)
                refined = stream.result.text.strip()
            return _terminal_text(refined) if refined else fallback
        except Exception:
            logger.exception("本地字幕精校失败，使用流式识别结果")
            return fallback

    async def close(self) -> None:
        self._closed = True
        self._stream = None
        self._utterance_chunks.clear()

    def _emit(self, events: list[tuple[str, str]]) -> None:
        for kind, text in events:
            callback = self.on_final if kind == "final" else self.on_partial
            self._run_callback(callback, text, kind)

    @staticmethod
    def _run_callback(callback: Callable[[str], None], value: str, kind: str) -> None:
        try:
            callback(value)
        except Exception:
            logger.exception("本地 ASR %s callback failed", kind)


class SherpaOnnxASR(BaseASRProvider):
    """Provider 注册入口；实时页面使用 SherpaOnnxRealtimeSession。"""

    name = "sherpa_onnx"

    async def health_check(self) -> bool:
        try:
            await asyncio.to_thread(_build_recognizer, resolve_model_dir(self.config))
            return True
        except ProviderError:
            return False

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        finals: list[str] = []
        session = SherpaOnnxRealtimeSession(self.config)
        session.on_final = finals.append
        await session.start()
        try:
            async for chunk in audio_stream:
                await session.push_audio(chunk)
            await session.finish()
        finally:
            await session.close()
        yield "".join(finals)


registry.register(SherpaOnnxASR)
