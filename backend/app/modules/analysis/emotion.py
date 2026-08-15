"""情绪分析融合层。

文本信号（口癖 + 模糊词）+ 语音信号（紧张度）融合 → 自信度 + 紧张度。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.analysis.text_rules import AnalysisResult
from app.modules.analysis.voice_features import VoiceFeatures, compute_tension


@dataclass
class EmotionSnapshot:
    tension_score: float       # 0-100，越高越紧张
    tension_level: str         # 平稳/偏紧/高度紧张
    confidence_score: float    # 0-100，越高越自信
    confidence_level: str      # 强/适中/偏弱


# 模糊词（自我怀疑类）
HEDGING_WORDS = {"可能", "应该", "好像", "也许", "大概", "不太", "不一定"}


def analyze_emotion(text_res: AnalysisResult, voice_feats: VoiceFeatures | None) -> EmotionSnapshot:
    """文本 + 语音双信号融合。"""
    # 1. 紧张度：语音为主，文本为辅
    if voice_feats and voice_feats.duration_sec > 0:
        tension = compute_tension(voice_feats)
    else:
        tension = 40.0

    # 文本模糊词过多 → 紧张度上调（hedge_hits 已从 filler_hits 分离）
    hedging_count = sum(h["count"] for h in text_res.hedge_hits)
    # 不自信表述同样计入
    hedging_count += sum(h["count"] for h in text_res.uncertain_hits)
    if hedging_count >= 3:
        tension += 10
    tension = max(0.0, min(100.0, tension))

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
    # 语音紧张拉低自信（间接）
    if tension > 70:
        confidence -= 10
    confidence = max(0.0, min(100.0, confidence))

    return EmotionSnapshot(
        tension_score=round(tension, 1),
        tension_level=_level(tension, [40, 70], ["平稳", "偏紧", "高度紧张"]),
        confidence_score=round(confidence, 1),
        confidence_level=_level(100 - confidence, [40, 70], ["强", "适中", "偏弱"]),
    )


def _level(value: float, thresholds: list[float], labels: list[str]) -> str:
    if value < thresholds[0]:
        return labels[0]
    if value < thresholds[1]:
        return labels[1]
    return labels[2]
