"""共享表达信号评分器。

本模块只把可观察事实转换为确定性分数，不包含任何场景判断。场景评价轴、
权重和语义口径由 ``ScenarioPack.evaluation`` 声明。
"""

from __future__ import annotations

from typing import Any, Iterable

from app.modules.scenarios.base import ScoreGate


def _ramp(x: float, lo: float, hi: float) -> float:
    """连续分段：x<=lo→0，x>=hi→1，中间线性过渡。"""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def score_fluency(
    filler_weighted: float,
    total_chars: int,
    repetition_rate: float,
) -> float | None:
    """流利度：明确口癖密度 + 紧邻用词重复率，从 100 扣分。

    filler_weighted：Σ(词条权重 × 出现次数)；total_chars：汉字数。
    """
    if total_chars <= 0:
        return None

    # 每百字加权口癖命中：<0.5 健康，≥6 严重
    density = filler_weighted / max(total_chars, 1) * 100
    filler_penalty = 40.0 * _ramp(density, 0.5, 6.0)
    # 紧邻重说事件率：<1% 不扣分，≥8% 达到当前最大扣分。
    # 该连续带是 v3 的保守初始口径，需随扩大的真人标注集版本化校准。
    rep_penalty = 30.0 * _ramp(repetition_rate, 0.01, 0.08)
    return _clamp(100.0 - filler_penalty - rep_penalty)


def score_continuity(
    filler_weighted: float,
    total_chars: int,
    repetition_rate: float,
    *,
    break_events: Iterable[dict[str, Any]] = (),
) -> float | None:
    """表达连贯性：口癖、紧邻重复和可核对的局部表达断裂。

    同一次断裂可能同时带有重复和停顿证据，按 ``event_id`` 去重后只贡献
    一次影响。已恢复的纠正仍扣分，但轻于未恢复的连续重启。
    """
    base = score_fluency(filler_weighted, total_chars, repetition_rate)
    filler_only = score_fluency(filler_weighted, total_chars, 0.0)
    repetition_only = score_fluency(0.0, total_chars, repetition_rate)
    if base is None or filler_only is None or repetition_only is None:
        return None

    seen: set[str] = set()
    weighted_events = 0.0
    default_weights = {
        "self_correction": 0.6,
        "fragmented_clause": 0.8,
        "unfinished_clause": 0.7,
        "restart": 1.0,
    }
    for index, event in enumerate(break_events):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or f"event-{index}")
        if event_id in seen:
            continue
        seen.add(event_id)
        raw_weight = event.get("weight")
        weight = (
            float(raw_weight)
            if isinstance(raw_weight, (int, float)) and not isinstance(raw_weight, bool)
            else default_weights.get(str(event.get("kind")), 0.8)
        )
        if event.get("recovered") is False:
            weight *= 1.4
        weighted_events += max(0.0, weight)

    if weighted_events <= 0:
        return base

    density = weighted_events / max(total_chars, 100) * 100
    if density <= 0.25:
        break_penalty = 6.0 * density / 0.25
    elif density <= 1.0:
        break_penalty = 6.0 + 10.0 * (density - 0.25) / 0.75
    elif density <= 2.0:
        break_penalty = 16.0 + 18.0 * (density - 1.0)
    else:
        break_penalty = 34.0 + 11.0 * _ramp(density, 2.0, 3.5)
    # 断裂事件已经可能包含紧邻重复的佐证。两者取较强影响而不是相加，
    # 避免同一次重启被 aggregate repetition_rate 再扣一遍。
    repetition_penalty = 100.0 - repetition_only
    return _clamp(filler_only - max(repetition_penalty, break_penalty))


def score_pacing(
    rate_cpm: float | None,
    *,
    pause_rate: float | None = None,
    long_pause_rate: float | None = None,
    break_events: Iterable[dict[str, Any]] = (),
) -> float | None:
    """语速与节奏控制：平均语速 + 有文本断裂佐证的停顿密度。

    ASR 自动标点不能证明说话人“一口气说了长句”，因此文本长句
    不单独进入音频节奏分；停顿也只有在文本同时出现局部断裂时才影响
    节奏，避免把正常修辞停顿当作卡顿。
    """
    if rate_cpm is None or rate_cpm <= 0:
        return None
    if rate_cpm > 220:
        rate_penalty = 30.0 * _ramp(rate_cpm, 220, 320)
    elif rate_cpm < 130:
        rate_penalty = 30.0 * _ramp(130 - rate_cpm, 0, 80)
    else:
        rate_penalty = 0.0
    unique_break_ids = {
        str(event.get("event_id") or index)
        for index, event in enumerate(break_events)
        if isinstance(event, dict)
    }
    rhythm_penalty = 0.0
    if unique_break_ids:
        if pause_rate is not None and pause_rate >= 0:
            rhythm_penalty += 8.0 * _ramp(pause_rate, 5.0, 12.0)
        if long_pause_rate is not None and long_pause_rate >= 0:
            rhythm_penalty += 12.0 * _ramp(long_pause_rate, 6.0, 18.0)
    return _clamp(100.0 - rate_penalty - rhythm_penalty)


def score_voice(avg_jitter: float | None) -> float | None:
    """声音稳定：基频抖动均值越低越稳。无语音信号返回 None（缺省轴）。

    管线噪声底 0.045（合成集实测平稳语音）→ 满分；≥0.12 显著不稳。
    """
    if avg_jitter is None or avg_jitter <= 0:
        return None
    return _clamp(100.0 - 50.0 * _ramp(avg_jitter, 0.045, 0.12))


def score_delivery_stability(stability: float | None) -> float | None:
    """表达稳定性：透传共享分析层的训练信号，不解释为心理状态。"""
    if stability is None:
        return None
    return _clamp(float(stability))


def compose_total(
    axis_scores: dict[str, float | None],
    weights: dict[str, int],
) -> float | None:
    """总分：有分轴按权重归一化；全部缺失时返回 ``None``。"""
    total_w = 0.0
    weighted = 0.0
    for key, w in weights.items():
        s = axis_scores.get(key)
        if s is None:
            continue
        weighted += s * w
        total_w += w
    if total_w <= 0:
        return None
    return round(weighted / total_w, 1)


def score_coverage(
    axis_scores: dict[str, float | None],
    weights: dict[str, int],
) -> float:
    """返回已有有效分数覆盖的评价权重比例（0-1）。"""
    possible = sum(max(0, weight) for weight in weights.values())
    if possible <= 0:
        return 0.0
    available = sum(
        max(0, weight)
        for key, weight in weights.items()
        if axis_scores.get(key) is not None
    )
    return round(available / possible, 3)


def apply_score_gates(
    total_score: float | None,
    axis_scores: dict[str, float | None],
    gates: Iterable[ScoreGate],
) -> tuple[float | None, list[dict[str, Any]]]:
    """应用场景声明的关键任务约束，不包含任何场景分支。

    只有已取得分数且严格低于阈值的轴会触发约束。返回值同时保留结构化
    触发原因，使报告能够解释综合分为何不能被其他高分轴完全补偿。
    """
    if total_score is None:
        return None, []

    final_score = float(total_score)
    triggered: list[dict[str, Any]] = []
    for gate in gates:
        axis_score = axis_scores.get(gate.axis_key)
        if axis_score is None or axis_score >= gate.below:
            continue
        triggered.append(
            {
                "axis_key": gate.axis_key,
                "axis_score": round(float(axis_score), 1),
                "below": float(gate.below),
                "max_overall": float(gate.max_overall),
                "reason": gate.reason,
            }
        )
        final_score = min(final_score, float(gate.max_overall))

    return round(final_score, 1), triggered
