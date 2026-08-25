from app.api.v1.voice_ws import _rolling_turn_rate


def test_live_rate_waits_for_a_meaningful_sample() -> None:
    assert _rolling_turn_rate(0, 0.0, 8, 1.2) is None


def test_live_rate_accumulates_final_and_partial_speech() -> None:
    rate = _rolling_turn_rate(
        completed_chars=24,
        completed_speech_sec=6.0,
        partial_chars=12,
        current_speech_sec=3.0,
    )
    assert rate == 4.0


def test_live_rate_ignores_negative_inputs() -> None:
    assert _rolling_turn_rate(-5, -1.0, 12, 3.0) == 4.0
