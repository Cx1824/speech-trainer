"""面向用户的声音与节奏参考结论。

本模块把已有事实转换为低风险、可解释的训练参考，不参与任何评分。
三个维度保持独立，只描述可观察的声音起伏、表达流畅和节奏变化。
"""

from __future__ import annotations

from typing import Any

from app.modules.analysis.voice_features import DEFAULT_BASELINE

VOICE_REFERENCE_VERSION = "voice-reference-v2"


def build_voice_reference(
    *,
    voice_signal: bool,
    pitch_jitter: float | None,
    speech_duration: float | None,
    total_chars: int,
    speech_rate: float,
    speech_rate_level: str,
    filler_total: int,
    repetition_rate: float,
    continuity_score: float | None,
    pacing_score: float | None,
    expression_break_count: int = 0,
) -> dict[str, Any]:
    """生成声音起伏、表达流畅和节奏变化的非评分参考结论。

    声音起伏使用声学管线已有的通用噪声底，不读取个人参考录音，因而也
    可用于来源不明或网络测试音频。所有结论只描述训练信号，不推断心理状态。
    """
    if not voice_signal or pitch_jitter is None or pitch_jitter <= 0:
        return {
            "version": VOICE_REFERENCE_VERSION,
            "available": False,
            "summary": "声音数据不足，暂时无法给出声音与节奏参考。",
            "confidence": "较低",
            "confidence_note": "没有取得足够的声音信息。",
            "dimensions": [],
            "basis": [],
            "is_scored": False,
        }

    variation_value, variation_detail, variation_severity = _variation(pitch_jitter)
    fluency_value, fluency_detail, _ = _fluency(continuity_score)
    pacing_value, pacing_detail = _pacing(pacing_score, speech_rate_level)

    confidence = (
        "中等"
        if speech_duration is not None and speech_duration >= 30 and total_chars >= 100
        else "较低"
    )
    confidence_note = (
        "录音信息较充足，但结论仍建议结合实际回听。"
        if confidence == "中等"
        else "可用录音较短或识别文本较少，结论仅作初步参考。"
    )

    summary = _summary(
        variation_value=variation_value,
        fluency_value=fluency_value,
        pacing_value=pacing_value,
        speech_rate_level=speech_rate_level,
    )
    basis = [
        (
            "声音高低的快速变化：较多"
            if variation_severity >= 2
            else "声音高低的快速变化：较少"
            if variation_severity == 0
            else "声音高低的快速变化：适中"
        ),
        (
            f"语速：{speech_rate_level}（{speech_rate:.1f} 字/分钟）"
            if speech_rate > 0
            else "语速：数据不足"
        ),
        f"口癖与重复：{filler_total} 次口癖，{repetition_rate:.1%} 紧邻重复",
        f"局部表达断裂：{expression_break_count} 处",
    ]

    return {
        "version": VOICE_REFERENCE_VERSION,
        "available": True,
        "summary": summary,
        "confidence": confidence,
        "confidence_note": confidence_note,
        "dimensions": [
            {
                "key": "variation",
                "label": "声音起伏",
                "value": variation_value,
                "detail": variation_detail,
            },
            {
                "key": "fluency",
                "label": "表达流畅",
                "value": fluency_value,
                "detail": fluency_detail,
            },
            {
                "key": "pacing",
                "label": "节奏变化",
                "value": pacing_value,
                "detail": pacing_detail,
            },
        ],
        "basis": basis,
        "is_scored": False,
    }


def _variation(pitch_jitter: float) -> tuple[str, str, int]:
    floor = float(DEFAULT_BASELINE["pitch_jitter"])
    if pitch_jitter <= floor:
        return "较平稳", "没有检测到明显的快速声音波动。", 0
    if pitch_jitter <= floor * 1.8:
        return "有一些起伏", "检测到一些快速声音变化，可能来自自然语调或强调。", 1
    return "起伏较明显", "快速声音变化较多，也可能来自强调、说话风格或录音环境。", 2


def _fluency(continuity_score: float | None) -> tuple[str, str, int]:
    if continuity_score is None:
        return "信息不足", "没有足够文本判断口癖、重复和句子断裂情况。", 0
    if continuity_score >= 90:
        return "整体流畅", "口癖、紧邻重复和句子断裂较少，没有形成明显卡顿。", 0
    if continuity_score >= 75:
        return "偶有卡顿", "出现了一些口癖、紧邻重复或句子断裂，但没有持续影响表达。", 1
    return "卡顿较明显", "口癖、紧邻重复或句子断裂较多，已经影响表达连贯。", 2


def _pacing(
    pacing_score: float | None,
    speech_rate_level: str,
) -> tuple[str, str]:
    if pacing_score is None:
        return "信息不足", "没有足够的发言时长或停顿信息判断节奏变化。"
    if pacing_score >= 85:
        return "较自然", f"语速{speech_rate_level}，正文停顿没有形成明显节奏问题。"
    if pacing_score >= 75:
        return "偶有波动", f"语速{speech_rate_level}，部分停顿或速度变化值得回听。"
    return "波动较多", f"语速{speech_rate_level}，停顿或速度变化已经较多。"


def _summary(
    *,
    variation_value: str,
    fluency_value: str,
    pacing_value: str,
    speech_rate_level: str,
) -> str:
    pace = (
        f"，语速{speech_rate_level}"
        if speech_rate_level in {"偏快", "偏慢"}
        else ""
    )
    return f"声音{variation_value}{pace}，表达{fluency_value}，节奏{pacing_value}。"
