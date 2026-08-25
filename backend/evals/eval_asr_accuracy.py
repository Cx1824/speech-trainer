"""Compare local sherpa-onnx recognizers on the same labelled audio set.

This evaluator deliberately lives outside the production provider layer. A model is
eligible for integration only after it improves the same manifest without trading
away unacceptable latency.

Example::

    .venv/bin/python evals/eval_asr_accuracy.py \
      --online-model /path/to/streaming-model \
      --sense-voice-model /path/to/sense-voice-model \
      --zipformer-ctc-model /path/to/zipformer-ctc-model \
      --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import sherpa_onnx

from app.providers.asr.sherpa_local import SherpaOnnxRealtimeSession
from app.schemas import ProviderConfigIn


def normalize_text(text: str) -> str:
    """Use the same punctuation-insensitive CER convention for every model."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    return "".join(re.findall(r"[0-9a-z\u3400-\u9fff]", normalized))


def edit_distance(reference: str, hypothesis: str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, reference_char in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_char in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (reference_char != hypothesis_char),
                )
            )
        previous = current
    return previous[-1]


def read_wav(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError(f"expected mono 16-bit WAV: {path}")
        sample_rate = source.getframerate()
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    return sample_rate, samples.astype(np.float32) / 32768.0


@dataclass(frozen=True)
class ModelResult:
    name: str
    text: str
    elapsed_sec: float


class OfflineModel:
    def __init__(self, name: str, recognizer: sherpa_onnx.OfflineRecognizer) -> None:
        self.name = name
        self.recognizer = recognizer

    async def transcribe(self, path: Path) -> ModelResult:
        sample_rate, samples = read_wav(path)
        return await self.transcribe_audio(sample_rate, samples)

    async def transcribe_audio(
        self, sample_rate: int, samples: np.ndarray
    ) -> ModelResult:
        started = time.perf_counter()
        stream = self.recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples)
        self.recognizer.decode_stream(stream)
        elapsed = time.perf_counter() - started
        return ModelResult(self.name, stream.result.text, elapsed)


class OnlineModel:
    name = "streaming_zipformer"

    def __init__(self, model_dir: Path) -> None:
        self.config = ProviderConfigIn(
            provider="sherpa_onnx",
            base_url=str(model_dir.resolve()),
        )

    async def transcribe(self, path: Path) -> ModelResult:
        sample_rate, samples = read_wav(path)
        return await self.transcribe_audio(sample_rate, samples)

    async def transcribe_audio(
        self, sample_rate: int, samples: np.ndarray
    ) -> ModelResult:
        if sample_rate != 16_000:
            raise ValueError("online baseline expects 16 kHz audio")
        pcm = np.clip(samples * 32768.0, -32768, 32767).astype("<i2").tobytes()
        finals: list[str] = []
        session = SherpaOnnxRealtimeSession(self.config)
        session.on_final = finals.append
        started = time.perf_counter()
        await session.start()
        try:
            for offset in range(0, len(pcm), 3200):
                await session.push_audio(pcm[offset : offset + 3200])
            await session.finish()
        finally:
            await session.close()
        elapsed = time.perf_counter() - started
        return ModelResult(self.name, "".join(finals), elapsed)


def _sense_voice(model_dir: Path, *, use_itn: bool) -> OfflineModel:
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model_dir / "model.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=4,
        language="zh",
        use_itn=use_itn,
    )
    suffix = "itn" if use_itn else "plain"
    return OfflineModel(f"sense_voice_{suffix}", recognizer)


def _zipformer_ctc(model_dir: Path) -> OfflineModel:
    recognizer = sherpa_onnx.OfflineRecognizer.from_zipformer_ctc(
        model=str(model_dir / "model.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        num_threads=4,
    )
    return OfflineModel("offline_zipformer_ctc", recognizer)


async def evaluate(args: argparse.Namespace) -> dict:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest["samples"])
    if args.include_controls:
        items.extend(manifest.get("control_samples", []))
    models: list[OnlineModel | OfflineModel] = []
    if args.online_model:
        models.append(OnlineModel(args.online_model))
    if args.sense_voice_model:
        models.extend(
            [
                _sense_voice(args.sense_voice_model, use_itn=False),
                _sense_voice(args.sense_voice_model, use_itn=True),
            ]
        )
    if args.zipformer_ctc_model:
        models.append(_zipformer_ctc(args.zipformer_ctc_model))
    if not models:
        raise ValueError("provide at least one model path")

    rows: list[dict] = []
    totals = {
        model.name: {"edits": 0, "reference_chars": 0, "elapsed_sec": 0.0}
        for model in models
    }
    for item in items:
        path = manifest_path.parent / item["file"]
        reference = normalize_text(item["text"])
        row = {
            "id": item["id"],
            "duration_sec": item["duration_sec"],
            "reference": item["text"],
            "models": {},
        }
        for model in models:
            result = await model.transcribe(path)
            hypothesis = normalize_text(result.text)
            edits = edit_distance(reference, hypothesis)
            row["models"][model.name] = {
                "text": result.text,
                "edits": edits,
                "cer": round(edits / max(1, len(reference)), 4),
                "elapsed_sec": round(result.elapsed_sec, 4),
            }
            total = totals[model.name]
            total["edits"] += edits
            total["reference_chars"] += len(reference)
            total["elapsed_sec"] += result.elapsed_sec
        rows.append(row)

    audio_sec = sum(float(item["duration_sec"]) for item in items)
    summary = {}
    for name, total in totals.items():
        summary[name] = {
            "cer": round(total["edits"] / total["reference_chars"], 4),
            "edits": total["edits"],
            "reference_chars": total["reference_chars"],
            "elapsed_sec": round(total["elapsed_sec"], 3),
            "rtf": round(total["elapsed_sec"] / audio_sec, 4),
        }
    return {"audio_sec": round(audio_sec, 2), "summary": summary, "samples": rows}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("web_audio_manifest.json"),
    )
    parser.add_argument("--online-model", type=Path)
    parser.add_argument("--sense-voice-model", type=Path)
    parser.add_argument("--zipformer-ctc-model", type=Path)
    parser.add_argument("--include-controls", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(evaluate(args))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for name, metrics in result["summary"].items():
        print(
            f"{name:<24} CER={metrics['cer']:.2%} "
            f"RTF={metrics['rtf']:.3f} elapsed={metrics['elapsed_sec']:.1f}s"
        )


if __name__ == "__main__":
    main()
