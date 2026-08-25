"""可观察声学特征提取。

模块计算音高、能量、停顿和发音密度等训练信号。评分仅表示这些信号
相对个人朗读基线的变化程度，不用于推断情绪、紧张或其他心理状态。
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
    hesitation_count: int = 0           # 0.2-0.5s 短停顿数
    voiced_ratio: float = 0.0           # 浊音帧占比（声学语速/发音密度用）
    raw: dict = field(default_factory=dict)


def extract_features(audio_bytes: bytes, fmt: str = "webm") -> VoiceFeatures:
    """从音频二进制提取可观察声学特征。

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
            # 找正文内部连续静音段；录音首尾等待不属于表达停顿。
            runs: list[tuple[int, int]] = []
            start: int | None = None
            for index, is_silent in enumerate(silent):
                if is_silent and start is None:
                    start = index
                elif not is_silent and start is not None:
                    runs.append((start, index))
                    start = None
            if start is not None:
                runs.append((start, len(silent)))
            pauses = [
                end - start
                for start, end in runs
                if start > 0 and end < len(silent)
            ]
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
    """旧协议兼容的表达波动代理值（0-100）。

    启发式规则：
    - 音高快速波动增加时升高
    - 能量起伏增加时升高
    - 停顿密度明显偏离参考区间时升高

    名称为历史兼容字段，不代表心理状态判断。
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

    # 停顿：过密或过稀都记为相对参考区间的变化
    if feats.duration_sec > 0:
        pause_rate = feats.pause_count / feats.duration_sec * 60
        if pause_rate > 10 or pause_rate < 1:
            score += 10

    return max(0.0, min(100.0, score))


# ---------------------------------------------------------------------------
# 表达信号偏离 2.0：个人基线 + 连续打分（保留旧函数名兼容协议）
# ---------------------------------------------------------------------------

# 未校准时的算法参考值，仅用于回归；不会生成用户可见的稳定性评分。
# pitch_jitter=0.045 是本管线（25ms 帧 NCF + 轨迹净化）在平稳语音上的
# 测量噪声底——低于它区分不出生理颤抖，只有强颤抖（>0.07）才升分。
DEFAULT_BASELINE = {
    "pitch_jitter": 0.045,     # 去趋势+净化后，平稳语音噪声底
    "speech_rate": 4.2,        # 字/秒，正常 3.5-5.0
    "pause_rate": 3.0,         # 每分钟 >0.5s 停顿次数，正常 2-6
    "hesitation_rate": 24.0,   # 每分钟 0.2-0.5s 犹豫短停顿（标点停顿含其中）
    "pitch_mean": 0.0,         # 个人音域中心（Hz），0=未知不参与
}


@dataclass
class VoiceBaseline:
    """个人声学基线（校准段落朗读得到）。"""

    pitch_jitter: float = DEFAULT_BASELINE["pitch_jitter"]
    speech_rate: float = DEFAULT_BASELINE["speech_rate"]
    pause_rate: float = DEFAULT_BASELINE["pause_rate"]
    hesitation_rate: float = DEFAULT_BASELINE["hesitation_rate"]
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
            "hesitation_rate": round(self.hesitation_rate, 3),
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
            hesitation_rate=float(d.get("hesitation_rate", DEFAULT_BASELINE["hesitation_rate"])),
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
    """连续计算表达信号偏离代理值（0-100）及明细。

    v2.1（反差集驱动重校）：
    - 短停顿和长停顿分开计量，避免把慢速朗读直接视为负面状态。
    - 基频只相对有效个人基线比较；无个人音域基线时不参与。
    - 文字语速缺席时，发音密度只作为弱代理信号。

    返回 (score, detail)。旧函数名和 score 偏移量用于数据兼容；该值没有
    心理测量含义，未校准时不得作为用户可见的稳定性评分。
    """
    bl = baseline or VoiceBaseline()
    detail: dict[str, float] = {}

    # ---- ① 快速颤抖（pitch jitter，去趋势+净化后）----
    # 新噪声底 0.045（管线自测）：低于它不升分，1.8 倍起升，3 倍封顶
    j_ratio = feats.pitch_jitter / max(bl.pitch_jitter, 1e-4)
    j_score = 30.0 * _ramp(j_ratio, 1.8, 3.0)
    detail["jitter"] = round(j_score, 1)

    # ---- ② 基频变化（相对个人音域中心，需校准基线）----
    # 不使用人群音高先验，避免把音色差异误当作训练状态变化。
    p_score = 0.0
    if feats.pitch_mean > 0 and bl.pitch_mean > 0:
        p_ratio = feats.pitch_mean / bl.pitch_mean
        # 抬升 ≥8% 起分（语调上扬自然波动内不罚），25% 封顶
        p_score = 20.0 * _ramp(p_ratio - 1.0, 0.08, 0.25)
        detail["pitch_rise"] = round(p_score, 1)
        detail["pitch_ratio"] = round(p_ratio, 2)

    # ---- ③ 短停顿密度（0.2-0.5s 短停顿/分钟）----
    # 自然标点停顿也可能落入此带，因此只比较相对个人基线的明显变化。
    h_score = 0.0
    if feats.duration_sec >= 3.0:
        hrate = feats.hesitation_count / feats.duration_sec * 60
        h_ratio = hrate / max(bl.hesitation_rate, 5.0)
        if h_ratio > 1.4:
            h_score = 20.0 * _ramp(h_ratio, 1.4, 3.0)
        detail["hesitation"] = round(h_score, 1)
        detail["hesitation_ratio"] = round(h_ratio, 2)
        # 长停顿过多只做轻微信号（漏字/忘词的长卡壳），不再 U 形重罚
        prate = feats.pause_count / feats.duration_sec * 60
        p_ratio = prate / max(bl.pause_rate, 0.5)
        if p_ratio > 2.0:
            detail["pause"] = round(6.0 * _ramp(p_ratio, 2.0, 5.0), 1)
            h_score += detail["pause"]

    # ---- ④ 语速（文字语速优先，发音密度兜底）----
    s_score = 0.0
    if speech_rate and speech_rate > 0:
        s_ratio = speech_rate / max(bl.speech_rate, 0.5)
        if s_ratio > 1.2:
            s_score = 25.0 * _ramp(s_ratio, 1.2, 1.6)
        elif s_ratio < 0.8:
            s_score = 10.0 * _ramp(0.8 - s_ratio, 0.0, 0.4)
        detail["speech_rate"] = round(s_score, 1)
        detail["speech_rate_ratio"] = round(s_ratio, 2)
    elif feats.voiced_ratio > 0:
        # 发音密度兜底：正常连续朗读 0.30-0.45（合成集实测），>0.55 急促
        v_score = 12.0 * _ramp(feats.voiced_ratio, 0.45, 0.65)
        detail["voiced_density"] = round(v_score, 1)
        s_score = v_score

    # ---- ⑤ 能量起伏（CV，增益归一化）----
    if feats.energy_mean > 0:
        cv = feats.energy_std / (feats.energy_mean + 1e-9)
        e_score = 10.0 * _ramp(cv, 1.4, 2.4)
        detail["energy"] = round(e_score, 1)
        detail["energy_cv"] = round(cv, 2)
    else:
        e_score = 0.0

    # 历史量表保留 25 点偏移量，确保旧数据和阈值可继续读取。
    score = 25.0 + j_score + p_score + h_score + s_score + e_score
    return max(0.0, min(100.0, score)), detail
