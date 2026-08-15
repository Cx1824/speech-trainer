"""语音情绪特征提取。

基于音频片段计算紧张度相关指标。
真实场景需要连续音频流，MVP 阶段简化为按段分析。
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VoiceFeatures:
    """单段音频的关键特征。"""

    duration_sec: float = 0.0
    speech_rate_estimate: float = 0.0       # 用 WPM 估算
    pitch_mean: float = 0.0                 # Hz
    pitch_std: float = 0.0
    pitch_jitter: float = 0.0               # 抖动率
    energy_mean: float = 0.0
    energy_std: float = 0.0
    pause_count: int = 0
    avg_pause_duration: float = 0.0
    raw: dict = field(default_factory=dict)


def extract_features(audio_bytes: bytes, fmt: str = "webm") -> VoiceFeatures:
    """从音频二进制提取紧张度相关特征。

    用 librosa 计算基频、能量、停顿。失败时返回空特征（不抛异常）。
    """
    feats = VoiceFeatures()
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(io.BytesIO(audio_bytes), sr=None, mono=True)
        feats.duration_sec = float(len(y) / sr)

        # 基频（pitch）
        try:
            f0, voiced_flag, _ = librosa.pyin(
                y, fmin=80, fmax=400, sr=sr
            )
            f0_voiced = f0[~np.isnan(f0)] if f0 is not None else np.array([])
            if len(f0_voiced) > 0:
                feats.pitch_mean = float(np.mean(f0_voiced))
                feats.pitch_std = float(np.std(f0_voiced))
                # jitter：相邻 pitch 周期差异的平均
                if len(f0_voiced) > 1:
                    diffs = np.abs(np.diff(f0_voiced))
                    feats.pitch_jitter = float(np.mean(diffs) / (feats.pitch_mean + 1e-6))
        except Exception as e:
            logger.debug("pitch 提取失败：%s", e)

        # 短时能量
        hop = 512
        energy = np.array(
            [np.sum(np.abs(y[i:i + hop]) ** 2) for i in range(0, len(y), hop)]
        )
        if len(energy) > 0:
            feats.energy_mean = float(np.mean(energy))
            feats.energy_std = float(np.std(energy))

        # 停顿（基于能量阈值）
        if len(energy) > 0:
            threshold = np.max(energy) * 0.05
            silent = energy < threshold
            # 找连续静音段
            pauses = []
            cur = 0
            for s in silent:
                if s:
                    cur += 1
                elif cur > 0:
                    pauses.append(cur)
                    cur = 0
            if cur > 0:
                pauses.append(cur)
            # 仅统计 > 0.5s 的停顿
            min_frames = int(0.5 * sr / hop)
            valid_pauses = [p for p in pauses if p >= min_frames]
            feats.pause_count = len(valid_pauses)
            if valid_pauses:
                feats.avg_pause_duration = float(
                    np.mean(valid_pauses) * hop / sr
                )

        feats.raw = {
            "sr": int(sr),
            "samples": int(len(y)),
        }
    except Exception as e:
        logger.warning("音频特征提取失败：%s", e)
    return feats


def compute_tension(feats: VoiceFeatures) -> float:
    """根据特征估算紧张度评分（0-100）。

    启发式规则：
    - 抖动越大越紧张
    - 能量起伏越大越紧张
    - 停顿次数过多/过少都倾向紧张
    """
    score = 30.0  # 基线
    # pitch jitter（正常范围 0.005-0.03）
    if feats.pitch_jitter > 0:
        if feats.pitch_jitter > 0.05:
            score += 25
        elif feats.pitch_jitter > 0.02:
            score += 15
        else:
            score += 5

    # 能量起伏
    if feats.energy_mean > 0 and feats.energy_std / (feats.energy_mean + 1e-6) > 1.5:
        score += 15

    # 停顿：过密或过稀都倾向紧张
    if feats.duration_sec > 0:
        pause_rate = feats.pause_count / feats.duration_sec * 60
        if pause_rate > 10 or pause_rate < 1:
            score += 10

    return max(0.0, min(100.0, score))


# ---------------------------------------------------------------------------
# 情绪判定 2.0：个人基线 + 连续打分
# ---------------------------------------------------------------------------

# 人群默认基线（校准前的兜底；数值来自常人朗读/对话的典型范围）
DEFAULT_BASELINE = {
    "pitch_jitter": 0.020,      # 快速颤抖率（去趋势后），正常 0.005-0.035
    "speech_rate": 4.2,         # 字/秒，正常 3.5-5.0
    "pause_rate": 3.0,          # 每分钟 >0.5s 停顿次数，正常 2-6
    "pitch_mean": 0.0,          # 个人音域中心（Hz），0=未知不参与
}


@dataclass
class VoiceBaseline:
    """个人声学基线（校准段落朗读得到）。"""

    pitch_jitter: float = DEFAULT_BASELINE["pitch_jitter"]
    speech_rate: float = DEFAULT_BASELINE["speech_rate"]
    pause_rate: float = DEFAULT_BASELINE["pause_rate"]
    pitch_mean: float = DEFAULT_BASELINE["pitch_mean"]
    sample_sec: float = 0.0     # 校准音频总时长（不足 10s 不可信）
    created_at: str = ""        # ISO 时间戳

    def is_valid(self) -> bool:
        return self.sample_sec >= 10.0

    def to_dict(self) -> dict:
        return {
            "pitch_jitter": round(self.pitch_jitter, 5),
            "speech_rate": round(self.speech_rate, 3),
            "pause_rate": round(self.pause_rate, 3),
            "pitch_mean": round(self.pitch_mean, 1),
            "sample_sec": round(self.sample_sec, 2),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "VoiceBaseline":
        if not d:
            return cls()
        return cls(
            pitch_jitter=float(d.get("pitch_jitter", DEFAULT_BASELINE["pitch_jitter"])),
            speech_rate=float(d.get("speech_rate", DEFAULT_BASELINE["speech_rate"])),
            pause_rate=float(d.get("pause_rate", DEFAULT_BASELINE["pause_rate"])),
            pitch_mean=float(d.get("pitch_mean", 0.0)),
            sample_sec=float(d.get("sample_sec", 0.0)),
            created_at=str(d.get("created_at", "")),
        )


def _ramp(x: float, lo: float, hi: float) -> float:
    """连续分段函数：x<=lo→0，x>=hi→1，中间线性过渡。替代跳档加分。"""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


def compute_tension_v2(
    feats: VoiceFeatures,
    baseline: VoiceBaseline | None = None,
    speech_rate: float | None = None,
) -> tuple[float, dict]:
    """连续打分版紧张度（0-100）+ 明细。

    与旧版差异：
    - 连续 _ramp 替代跳档加分（分数有梯度）
    - 有基线时按"偏离个人常态"打分（U 形），无基线用人群默认
    - 语速（字/秒，ASR 定稿文字÷音频时长）作为独立信号接入
    - 能量用变异系数（CV=std/mean），对麦克风增益天然归一化

    返回 (score, detail)：detail 是各信号贡献明细，前端可展示"为什么"。
    """
    bl = baseline or VoiceBaseline()
    detail: dict[str, float] = {}

    # ---- ① 快速颤抖（pitch jitter，去趋势后）----
    # 偏离基线 1.5 倍开始升分，3 倍封顶
    j_ratio = feats.pitch_jitter / max(bl.pitch_jitter, 1e-4)
    j_score = 35.0 * _ramp(j_ratio, 1.5, 3.0)
    detail["jitter"] = round(j_score, 1)

    # ---- ② 语速偏离（字/秒，U 形：过快过慢都紧张）----
    # 基线 ±20% 舒适区；过快侧 1.6 倍基线封顶（语速飙升=强紧张信号）
    s_score = 0.0
    if speech_rate and speech_rate > 0:
        s_ratio = speech_rate / max(bl.speech_rate, 0.5)
        lo, hi = 0.8, 1.2
        if s_ratio > hi:
            s_score = 25.0 * _ramp(s_ratio, hi, 1.6)
        elif s_ratio < lo:
            s_score = 18.0 * _ramp(lo - s_ratio, 0.0, 0.45)
        detail["speech_rate"] = round(s_score, 1)
        detail["speech_rate_ratio"] = round(s_ratio, 2)

    # ---- ③ 停顿密度偏离（每分钟停顿数，U 形）----
    # 基线 ±50% 舒适（停顿本身个体差异大）；短句天然停顿少，需要最小区间保护
    p_score = 0.0
    if feats.duration_sec >= 3.0:  # <3s 的短句不评停顿（样本不足必误判）
        prate = feats.pause_count / feats.duration_sec * 60
        p_ratio = prate / max(bl.pause_rate, 0.5)
        if p_ratio > 1.5:
            p_score = 18.0 * _ramp(p_ratio, 1.5, 4.0)
        elif p_ratio < 1 / 1.5:
            # 停顿过少（语流过密不换气）——轻微信号
            p_score = 8.0 * _ramp(1 / 1.5 - p_ratio, 0.0, 0.6)
        detail["pause"] = round(p_score, 1)
        detail["pause_ratio"] = round(p_ratio, 2)

    # ---- ④ 能量起伏（CV，增益归一化）----
    # 正常说话 CV（每帧能量）约 0.5-1.0；>1.6 显著起伏
    if feats.energy_mean > 0:
        cv = feats.energy_std / (feats.energy_mean + 1e-9)
        e_score = 12.0 * _ramp(cv, 1.2, 2.2)
        detail["energy"] = round(e_score, 1)
        detail["energy_cv"] = round(cv, 2)
    else:
        e_score = 0.0

    # 基线 25：即使所有信号都平稳也给一个非零底（真实人不可能 0 紧张）
    score = 25.0 + j_score + s_score + p_score + e_score
    return max(0.0, min(100.0, score)), detail
