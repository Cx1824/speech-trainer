from __future__ import annotations

import numpy as np

from evals.eval_web_audio import _boundary_silence


def _pcm(*segments: tuple[float, bool]) -> bytes:
    chunks = []
    for duration_sec, voiced in segments:
        size = int(16000 * duration_sec)
        value = 12000 if voiced else 0
        chunks.append(np.full(size, value, dtype=np.int16))
    return np.concatenate(chunks).tobytes()


def test_boundary_silence_excludes_clip_edges_from_pause_counts() -> None:
    pcm = _pcm((1.0, False), (0.5, True), (0.3, False), (0.5, True), (1.0, False))

    result = _boundary_silence(pcm)

    assert result == {
        "leading_sec": 1.0,
        "trailing_sec": 1.0,
        "active_span_sec": 1.3,
        "interior_long_pauses": 0,
        "interior_short_pauses": 1,
    }


def test_boundary_silence_handles_silent_clip() -> None:
    result = _boundary_silence(_pcm((1.0, False)))

    assert result["leading_sec"] == 1.0
    assert result["active_span_sec"] == 0.0
    assert result["interior_long_pauses"] == 0
