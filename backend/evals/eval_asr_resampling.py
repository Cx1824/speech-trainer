"""Measure ASR loss introduced by the browser worklet's 48 kHz -> 16 kHz path.

The synthetic high-frequency-noise case is a diagnostic stress test, not an
estimate of typical microphone conditions. It isolates aliasing above the target
8 kHz Nyquist frequency so the current blockwise interpolation can be compared
with a filtered polyphase resampler.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.eval_asr_accuracy import (
    OfflineModel,
    OnlineModel,
    _sense_voice,
    edit_distance,
    normalize_text,
    read_wav,
)


TARGET_SAMPLE_RATE = 16_000
CAPTURE_SAMPLE_RATE = 48_000
WORKLET_BLOCK_SIZE = 2048


def current_worklet_resample(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Reproduce frontend/public/worklets/recorder.js block for block."""
    ratio = sample_rate / TARGET_SAMPLE_RATE
    blocks: list[np.ndarray] = []
    for offset in range(0, len(samples) - WORKLET_BLOCK_SIZE + 1, WORKLET_BLOCK_SIZE):
        block = samples[offset : offset + WORKLET_BLOCK_SIZE]
        output_length = math.floor(len(block) / ratio)
        positions = np.arange(output_length, dtype=np.float64) * ratio
        indexes = np.floor(positions).astype(np.int64)
        fractions = positions - indexes
        following = np.minimum(indexes + 1, len(block) - 1)
        output = block[indexes] * (1.0 - fractions) + block[following] * fractions
        blocks.append(output.astype(np.float32))
    return np.concatenate(blocks) if blocks else np.empty(0, dtype=np.float32)


def filtered_resample(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    return resample_poly(samples, TARGET_SAMPLE_RATE, sample_rate).astype(np.float32)


def to_capture_rate(samples: np.ndarray) -> np.ndarray:
    return resample_poly(samples, CAPTURE_SAMPLE_RATE, TARGET_SAMPLE_RATE).astype(
        np.float32
    )


def add_high_frequency_noise(
    samples: np.ndarray, *, signal_rms: float, seed: int, snr_db: float = 20.0
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(len(samples)).astype(np.float32)
    bandpass = butter(
        6,
        [9_000, 14_000],
        btype="bandpass",
        fs=CAPTURE_SAMPLE_RATE,
        output="sos",
    )
    noise = sosfilt(bandpass, white).astype(np.float32)
    noise_rms = float(np.sqrt(np.mean(noise * noise)))
    target_rms = signal_rms / (10 ** (snr_db / 20))
    if noise_rms:
        noise *= target_rms / noise_rms
    return np.clip(samples + noise, -1.0, 1.0)


def variants(samples: np.ndarray, *, seed: int) -> dict[str, np.ndarray]:
    capture = to_capture_rate(samples)
    signal_rms = float(np.sqrt(np.mean(capture * capture)))
    noisy_capture = add_high_frequency_noise(
        capture,
        signal_rms=signal_rms,
        seed=seed,
    )
    return {
        "direct_16k": samples,
        "current_worklet_clean": current_worklet_resample(
            capture, CAPTURE_SAMPLE_RATE
        ),
        "filtered_resampler_clean": filtered_resample(capture, CAPTURE_SAMPLE_RATE),
        "current_worklet_hf_noise_20db": current_worklet_resample(
            noisy_capture, CAPTURE_SAMPLE_RATE
        ),
        "filtered_resampler_hf_noise_20db": filtered_resample(
            noisy_capture, CAPTURE_SAMPLE_RATE
        ),
    }


async def evaluate(args: argparse.Namespace) -> dict:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest["samples"])
    models: list[OnlineModel | OfflineModel] = [OnlineModel(args.online_model)]
    if args.sense_voice_model:
        models.append(_sense_voice(args.sense_voice_model, use_itn=True))

    totals: dict[str, dict[str, dict[str, float | int]]] = {}
    rows: list[dict] = []
    for item in items:
        source = manifest_path.parent / item["file"]
        sample_rate, clean = read_wav(source)
        if sample_rate != TARGET_SAMPLE_RATE:
            raise ValueError(f"expected 16 kHz evaluation audio: {source}")
        reference = normalize_text(item["text"])
        audio_variants = variants(clean, seed=int(item["id"]))
        row = {"id": item["id"], "models": {}}
        for model in models:
            model_row = {}
            for variant, audio in audio_variants.items():
                result = await model.transcribe_audio(TARGET_SAMPLE_RATE, audio)
                edits = edit_distance(reference, normalize_text(result.text))
                model_row[variant] = {"text": result.text, "edits": edits}
                total = totals.setdefault(model.name, {}).setdefault(
                    variant,
                    {"edits": 0, "reference_chars": 0, "elapsed_sec": 0.0},
                )
                total["edits"] += edits
                total["reference_chars"] += len(reference)
                total["elapsed_sec"] += result.elapsed_sec
            row["models"][model.name] = model_row
        rows.append(row)

    audio_sec = sum(float(item["duration_sec"]) for item in items)
    summary = {}
    for model, model_totals in totals.items():
        summary[model] = {}
        for variant, total in model_totals.items():
            summary[model][variant] = {
                "cer": round(total["edits"] / total["reference_chars"], 4),
                "edits": total["edits"],
                "elapsed_sec": round(float(total["elapsed_sec"]), 3),
                "rtf": round(float(total["elapsed_sec"]) / audio_sec, 4),
            }
    return {"audio_sec": round(audio_sec, 2), "summary": summary, "samples": rows}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("web_audio_manifest.json"),
    )
    parser.add_argument("--online-model", type=Path, required=True)
    parser.add_argument("--sense-voice-model", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(evaluate(args))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    for model, variants_summary in result["summary"].items():
        print(model)
        for variant, metrics in variants_summary.items():
            print(
                f"  {variant:<38} CER={metrics['cer']:.2%} "
                f"RTF={metrics['rtf']:.3f}"
            )


if __name__ == "__main__":
    main()
