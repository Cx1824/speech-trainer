"""Evaluate license-clear web audio with the production acoustic extractor.

This evaluator reports observable signals only. It intentionally does not emit
the historical tension score because public recordings have no personal
calibration baseline or psychological ground truth.

Usage (from ``backend``)::

    .venv/bin/python evals/eval_web_audio.py
    .venv/bin/python evals/eval_web_audio.py --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.analysis.pcm_features import (
    FRAME_LEN,
    FRAME_MS,
    PcmFeatureBuffer,
    _energy_silence_mask,
)
from app.modules.analysis.text_rules import compute_speech_rate, rate_speech_rate
from app.modules.analysis.voice_features import extract_features
from evals.eval_listen import to_wav16k, wav_bytes_to_pcm


def _boundary_silence(pcm: bytes) -> dict[str, float | int]:
    """Measure boundary and interior silence using the production threshold."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
    samples = samples[: len(samples) // FRAME_LEN * FRAME_LEN]
    if not len(samples):
        return {
            "leading_sec": 0.0,
            "trailing_sec": 0.0,
            "active_span_sec": 0.0,
            "interior_long_pauses": 0,
            "interior_short_pauses": 0,
        }

    frames = samples.reshape(-1, FRAME_LEN)
    energy = np.sum(frames * frames, axis=1)
    peak = float(np.max(energy))
    if peak <= 0:
        return {
            "leading_sec": len(frames) * FRAME_MS / 1000,
            "trailing_sec": 0.0,
            "active_span_sec": 0.0,
            "interior_long_pauses": 0,
            "interior_short_pauses": 0,
        }

    silent = _energy_silence_mask(energy)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, is_silent in enumerate(silent):
        if is_silent and start is None:
            start = index
        elif not is_silent and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(silent)))

    leading_frames = next((end for start, end in runs if start == 0), 0)
    trailing_frames = next(
        (end - start for start, end in runs if end == len(silent)), 0
    )
    interior = [
        (end - start) * FRAME_MS / 1000
        for start, end in runs
        if start > 0 and end < len(silent)
    ]
    active_frames = max(0, len(silent) - leading_frames - trailing_frames)
    return {
        "leading_sec": round(leading_frames * FRAME_MS / 1000, 2),
        "trailing_sec": round(trailing_frames * FRAME_MS / 1000, 2),
        "active_span_sec": round(active_frames * FRAME_MS / 1000, 2),
        "interior_long_pauses": sum(value >= 0.5 for value in interior),
        "interior_short_pauses": sum(0.2 <= value < 0.5 for value in interior),
    }


def analyze_sample(eval_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    source = eval_root / item["file"]
    if not source.exists():
        raise FileNotFoundError(f"missing audio: {source}")

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != item["sha256"]:
        raise ValueError(f"checksum mismatch: {source}")

    wav = to_wav16k(source)
    pcm = wav_bytes_to_pcm(wav)
    buffer = PcmFeatureBuffer()
    for offset in range(0, len(pcm), 6400):
        buffer.push(pcm[offset : offset + 6400])
    features = buffer.flush()
    reference = extract_features(source.read_bytes(), "wav")
    boundary = _boundary_silence(pcm)

    total_rate = compute_speech_rate(item["text"], features.duration_sec)
    active_span = float(boundary["active_span_sec"])
    han_count = len(re.findall(r"[\u4e00-\u9fa5]", item["text"]))
    active_rate = round(han_count / active_span * 60, 1) if active_span else 0.0
    energy_cv = (
        features.energy_std / features.energy_mean
        if features.energy_mean > 0
        else 0.0
    )

    return {
        "id": item["id"],
        "gender": item["gender"],
        "duration_sec": round(features.duration_sec, 2),
        "speech_rate_cpm": total_rate,
        "speech_rate_level": rate_speech_rate(total_rate),
        "boundary_trimmed_rate_cpm": active_rate,
        "boundary_trimmed_rate_level": rate_speech_rate(active_rate),
        "long_pauses": features.pause_count,
        "short_pauses": features.hesitation_count,
        **boundary,
        "voiced_ratio": round(features.voiced_ratio, 3),
        "pitch_hz": round(features.pitch_mean, 1),
        "reference_pitch_hz": round(reference.pitch_mean, 1),
        "pitch_jitter": round(features.pitch_jitter, 4),
        "reference_pitch_jitter": round(reference.pitch_jitter, 4),
        "energy_cv": round(energy_cv, 2),
    }


def _print_table(rows: list[dict[str, Any]]) -> None:
    print(
        "id   sec    rate/level       trim-rate/level  pauses  f0/ref     jitter/ref"
    )
    for row in rows:
        print(
            f"{row['id']:<4} {row['duration_sec']:>5.2f}  "
            f"{row['speech_rate_cpm']:>5.1f}/{row['speech_rate_level']:<4}  "
            f"{row['boundary_trimmed_rate_cpm']:>5.1f}/"
            f"{row['boundary_trimmed_rate_level']:<4}  "
            f"{row['long_pauses']:>2}/{row['interior_long_pauses']:<2}  "
            f"{row['pitch_hz']:>5.1f}/{row['reference_pitch_hz']:<5.1f}  "
            f"{row['pitch_jitter']:.4f}/{row['reference_pitch_jitter']:.4f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("web_audio_manifest.json"),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--include-controls",
        action="store_true",
        help="also evaluate same-text control samples collected during human review",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = list(manifest["samples"])
    if args.include_controls:
        items.extend(manifest.get("control_samples", []))
    rows = [analyze_sample(manifest_path.parent, item) for item in items]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        _print_table(rows)


if __name__ == "__main__":
    main()
