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
