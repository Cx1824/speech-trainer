"""表达信号融合层 2.0。

文本信号（口癖 + 模糊词）+ 语音信号（去趋势颤抖 / 语速 / 停顿 / 能量）融合
→ 表达明确度 + 相对个人基线的声音与节奏变化。

``tension_*`` / ``confidence_*`` 是旧 WebSocket 和存量数据兼容字段，
不具有心理测量含义。前端与报告只展示可观察训练语言。
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
    tension_score: float       # 旧字段：0-100 表达信号偏离代理值
    tension_level: str         # 旧字段：偏离程度分档
    confidence_score: float    # 旧字段：0-100 文本明确度代理值
    confidence_level: str      # 旧字段：明确度分档
    # 2.0：各声学信号贡献明细（jitter/speech_rate/pause/energy + 比值）
    factors: dict = field(default_factory=dict)
    # 2.0：是否使用了个人基线（False=人群默认值兜底）
    calibrated: bool = False


class EmotionSmoother:
    """会话级表达波动值平滑器（EMA）。

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


# 模糊或保留式措辞
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

    # 1. 旧 tension 字段：声学变化为主，保留式措辞为辅
    if voice_feats and voice_feats.duration_sec > 0:
        tension_raw, factors = compute_tension_v2(voice_feats, baseline, speech_rate)
    else:
        tension_raw, factors = 40.0, {}

    # 保留式措辞过多时提高表达波动代理值（连续：每个 +4，上限 12）
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

    # 2. 旧 confidence 字段：文本明确度代理值
    confidence = 70.0
    # 模糊词降低明确度
    confidence -= min(40.0, hedging_count * 8)
    # 高权重口癖降低明确度
    high_filler_count = sum(
        h["count"] for h in text_res.filler_hits if h["weight"] >= 3
    )
    confidence -= min(20.0, high_filler_count * 4)
    # 只有定稿中的紧邻连续重复才降低明确度；普通主题词复用是正常
    # 语篇衔接，保留为客观事实但不再触发强提醒。
    if text_res.consecutive_repetition_hits:
        confidence -= min(10.0, len(text_res.consecutive_repetition_hits) * 3)
    # 明显的表达波动对明确度做轻量修正
    if tension > 60:
        confidence -= min(12.0, (tension - 60) / 10 * 3)
    confidence = max(0.0, min(100.0, confidence))

    return EmotionSnapshot(
        tension_score=round(tension, 1),
        tension_level=_level(tension, [40, 70], ["接近平时", "有所波动", "波动明显"]),
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
