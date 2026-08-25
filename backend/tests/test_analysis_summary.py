"""句子级分析汇总与报告数据状态测试。"""

from __future__ import annotations

import json

from app.modules.analysis import aggregate_sentence_analyses
from app.modules.report.generator import _aggregate_speech_duration, _sample_state


class _Dialogue:
    def __init__(self, role: str, analysis: dict | str) -> None:
        self.role = role
        self.analysis_json = (
            json.dumps(analysis, ensure_ascii=False)
            if isinstance(analysis, dict)
            else analysis
        )


def test_sentence_analysis_uses_duration_weighting() -> None:
    summary = aggregate_sentence_analyses(
        [
            {
                "voice_signal": True,
                "calibrated": True,
                "speech_duration_sec": 2.0,
                "tension_score": 20.0,
                "pitch_jitter": 0.04,
                "pause_count": 1,
                "hesitation_count": 2,
            },
            {
                "voice_signal": True,
                "calibrated": True,
                "speech_duration_sec": 6.0,
                "tension_score": 60.0,
                "pitch_jitter": 0.08,
                "pause_count": 2,
                "hesitation_count": 3,
            },
        ]
    )

    assert summary["speech_duration_sec"] == 8.0
    assert summary["tension_score"] == 50.0
    assert summary["pitch_jitter"] == 0.07
    assert summary["pause_count"] == 3
    assert summary["hesitation_count"] == 5
    assert summary["calibrated"] is True


def test_sentence_analysis_does_not_claim_calibration_without_voice() -> None:
    summary = aggregate_sentence_analyses(
        [{"voice_signal": False, "calibrated": True, "tension_score": 40.0}]
    )
    assert summary["voice_signal"] is False
    assert summary["calibrated"] is False


def test_report_sums_only_valid_user_speech_duration() -> None:
    rows = [
        _Dialogue("user", {"speech_duration_sec": 2.4}),
        _Dialogue("ai", {"speech_duration_sec": 99}),
        _Dialogue("user", {"speech_duration_sec": 3.1}),
        _Dialogue("user", "{broken"),
    ]
    assert _aggregate_speech_duration(rows) == 5.5


def test_report_sample_state_is_fact_based() -> None:
    assert _sample_state(0, None, False) == "insufficient"
    assert _sample_state(80, None, False) == "text_only"
    assert _sample_state(80, 30.0, False) == "voice_uncalibrated"
    assert _sample_state(80, 30.0, True) == "voice_calibrated"
