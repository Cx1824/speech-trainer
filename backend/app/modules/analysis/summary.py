"""句子级分析结果的会话内汇总工具。"""

from __future__ import annotations

from typing import Any


def aggregate_sentence_analyses(items: list[dict[str, Any]]) -> dict[str, Any]:
    """把句子级声学结果汇总为可持久化的整轮事实。

    连续值按有效声音片段时长加权；停顿数按片段求和。没有句子结果时
    返回空字典，由调用方使用整轮兜底分析。
    """
    if not items:
        return {}

    def weighted_mean(key: str) -> float | None:
        weighted = 0.0
        total_weight = 0.0
        for item in items:
            value = item.get(key)
            if not isinstance(value, (int, float)):
                continue
            duration = item.get("speech_duration_sec")
            weight = (
                float(duration)
                if isinstance(duration, (int, float)) and duration > 0
                else 1.0
            )
            weighted += float(value) * weight
            total_weight += weight
        return round(weighted / total_weight, 4) if total_weight > 0 else None

    result: dict[str, Any] = {
        "voice_signal": any(item.get("voice_signal") is True for item in items),
        "calibrated": any(
            item.get("voice_signal") is True and item.get("calibrated") is True
            for item in items
        ),
    }

    durations = [
        float(item["speech_duration_sec"])
        for item in items
        if isinstance(item.get("speech_duration_sec"), (int, float))
        and item["speech_duration_sec"] > 0
    ]
    if durations:
        result["speech_duration_sec"] = round(sum(durations), 2)

    pause_counts = [
        int(item["pause_count"])
        for item in items
        if isinstance(item.get("pause_count"), (int, float))
        and item["pause_count"] >= 0
    ]
    if pause_counts:
        result["pause_count"] = sum(pause_counts)

    hesitation_counts = [
        int(item["hesitation_count"])
        for item in items
        if isinstance(item.get("hesitation_count"), (int, float))
        and item["hesitation_count"] >= 0
    ]
    if hesitation_counts:
        result["hesitation_count"] = sum(hesitation_counts)

    for key in ("tension_score", "confidence_score", "pitch_jitter"):
        value = weighted_mean(key)
        if value is not None:
            result[key] = value

    pause_duration = weighted_mean("avg_pause_duration")
    if pause_duration is not None:
        result["avg_pause_duration"] = pause_duration

    return result
