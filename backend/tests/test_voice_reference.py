"""声音表现参考结论回归测试。"""

from app.modules.report.voice_reference import build_voice_reference


def _reference(**overrides):
    values = {
        "voice_signal": True,
        "pitch_jitter": 0.0941,
        "speech_duration": 470.43,
        "total_chars": 2081,
        "speech_rate": 265.4,
        "speech_rate_level": "偏快",
        "filler_total": 3,
        "repetition_rate": 0.0067,
        "continuity_score": 100.0,
        "pacing_score": 86.4,
    }
    values.update(overrides)
    return build_voice_reference(**values)


def test_high_variation_is_reported_without_psychological_inference() -> None:
    result = _reference()

    assert result["available"] is True
    assert result["is_scored"] is False
    assert result["confidence"] == "中等"
    assert [item["value"] for item in result["dimensions"]] == [
        "起伏较明显",
        "整体流畅",
        "较自然",
    ]
    assert "紧张" not in str(result)
    assert "节奏较自然" in result["summary"]


def test_moderate_pacing_change_is_reported_directly() -> None:
    result = _reference(
        continuity_score=80,
        pacing_score=82,
        filler_total=8,
        repetition_rate=0.035,
    )

    assert result["dimensions"][2]["value"] == "偶有波动"


def test_strong_pacing_change_is_reported_directly() -> None:
    result = _reference(
        continuity_score=60,
        pacing_score=70,
        filler_total=15,
        repetition_rate=0.08,
    )

    assert result["dimensions"][2]["value"] == "波动较多"


def test_missing_voice_signal_returns_unavailable_reference() -> None:
    result = _reference(voice_signal=False)

    assert result["available"] is False
    assert result["dimensions"] == []
    assert result["is_scored"] is False


def test_short_sample_never_claims_medium_confidence() -> None:
    result = _reference(speech_duration=8, total_chars=40)

    assert result["confidence"] == "较低"


def test_general_reference_does_not_claim_personal_baseline() -> None:
    result = _reference()
    rendered = str(result)

    assert "个人基线" not in rendered
    assert "参考录音" not in rendered
