"""情绪分析融合层 2.0。

文本信号（口癖 + 模糊词）+ 语音信号（去趋势颤抖 / 语速 / 停顿 / 能量）融合
→ 自信度 + 紧张度。

核心变化（相对 1.0）：
- 紧张度走 compute_tension_v2（连续打分 + 个人基线 + 语速信号）
- session_smoother 做句间 EMA 平滑，消除单句抖动导致的分数跳变
- EmotionSnapshot 携带 factors 明细（前端可解释"为什么紧张"）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.analysis.text_rules import AnalysisResult
from app.modules.analysis.voice_features import (
    VoiceBaseline,
    VoiceFeatures,
    compute_tension_v2,
)


@dataclass
class EmotionSnapshot:
    tension_score: float       # 0-100，越高越紧张
    tension_level: str         # 平稳/偏紧/高度紧张
    confidence_score: float    # 0-100，越高越自信
    confidence_level: str      # 强/适中/偏弱
    # 2.0：各声学信号贡献明细（jitter/speech_rate/pause/energy + 比值）
    factors: dict = field(default_factory=dict)
    # 2.0：是否使用了个人基线（False=人群默认值兜底）
    calibrated: bool = False


class EmotionSmoother:
    """会话级紧张度平滑器（EMA）。

    单句紧张度噪声大（短句停顿少、帧数少），直接展示会来回跳。
    alpha=0.45：新句占 45%，历史占 55%——既跟得上真实变化，又不抖。
    """

    def __init__(self, alpha: float = 0.45) -> None:
        self.alpha = alpha
        self._value: float | None = None

    def update(self, raw: float) -> float:
        if self._value is None:
            self._value = raw
        else:
            self._value = self.alpha * raw + (1 - self.alpha) * self._value
        return self._value

    def reset(self) -> None:
        self._value = None


# 模糊词（自我怀疑类）
HEDGING_WORDS = {"可能", "应该", "好像", "也许", "大概", "不太", "不一定"}


def analyze_emotion(
    text_res: AnalysisResult,
    voice_feats: VoiceFeatures | None,
    baseline: VoiceBaseline | None = None,
    speech_rate: float | None = None,
    smoother: EmotionSmoother | None = None,
) -> EmotionSnapshot:
    """文本 + 语音双信号融合（2.0）。

    baseline：个人校准基线；None 时用人群默认值（calibrated=False）
    speech_rate：字/秒（ASR 定稿字数 ÷ 本句音频时长）；None 则跳过语速信号
    smoother：会话级平滑器；传入则输出平滑后的分数
    """
    calibrated = baseline is not None and baseline.is_valid()

    # 1. 紧张度：声学为主（v2 连续打分 + 基线偏离），文本为辅
    if voice_feats and voice_feats.duration_sec > 0:
        tension_raw, factors = compute_tension_v2(voice_feats, baseline, speech_rate)
    else:
        tension_raw, factors = 40.0, {}

    # 文本模糊词过多 → 紧张度上调（连续：每个 +4，上限 12）
    hedging_count = sum(h["count"] for h in text_res.hedge_hits)
    hedging_count += sum(h["count"] for h in text_res.uncertain_hits)
    if hedging_count > 0:
        factors["hedge"] = min(12.0, hedging_count * 4.0)
        tension_raw += factors["hedge"]
    tension_raw = max(0.0, min(100.0, tension_raw))

    # 句间平滑
    if smoother is not None:
        tension = smoother.update(tension_raw)
    else:
        tension = tension_raw

    # 2. 自信度：文本为主，语音为辅
    confidence = 70.0
    # 模糊词拉低自信
    confidence -= min(40.0, hedging_count * 8)
    # 高权重口癖拉低自信
    high_filler_count = sum(
        h["count"] for h in text_res.filler_hits if h["weight"] >= 3
    )
    confidence -= min(20.0, high_filler_count * 4)
    # 重复词拉低自信
    if text_res.repeated_words:
        confidence -= min(10.0, len(text_res.repeated_words) * 3)
    # 语音紧张拉低自信（连续：超过 60 后每 10 点扣 3，上限 12）
    if tension > 60:
        confidence -= min(12.0, (tension - 60) / 10 * 3)
    confidence = max(0.0, min(100.0, confidence))

    return EmotionSnapshot(
        tension_score=round(tension, 1),
        tension_level=_level(tension, [40, 70], ["平稳", "偏紧", "高度紧张"]),
        confidence_score=round(confidence, 1),
        confidence_level=_level(100 - confidence, [40, 70], ["强", "适中", "偏弱"]),
        factors={k: (round(v, 1) if isinstance(v, float) else v) for k, v in factors.items()},
        calibrated=calibrated,
    )


def _level(value: float, thresholds: list[float], labels: list[str]) -> str:
    if value < thresholds[0]:
        return labels[0]
    if value < thresholds[1]:
        return labels[1]
    return labels[2]
