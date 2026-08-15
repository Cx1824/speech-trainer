"""PCM 流式声学特征提取。

与 voice_features.py（librosa 整段解码）不同，本模块面向「旁路缓冲」场景：
后端 voice_ws 把送往 ASR 的 16k/16bit/mono PCM 同步喂进来，
句子定稿时对累积的 PCM 直接计算基频/能量/停顿，输出 VoiceFeatures。

计算全部基于 numpy（无解码开销，毫秒级），复用 compute_tension 打分。
"""

from __future__ import annotations

import logging
import struct

from app.modules.analysis.voice_features import VoiceFeatures, compute_tension

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 25                     # 分帧 25ms
FRAME_LEN = SAMPLE_RATE * FRAME_MS // 1000   # 400 样本
MIN_PITCH_HZ = 80.0
MAX_PITCH_HZ = 400.0


class PcmFeatureBuffer:
    """累积 PCM 帧并在需要时计算声学特征。

    用法：
        buf = PcmFeatureBuffer()
        buf.push(pcm_bytes)        # 与 ASR 同一份音频
        feats = buf.flush()        # 句子定稿时取特征并清空
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._samples: list[int] = []   # Int16 样本累积

    def push(self, pcm: bytes) -> None:
        """喂入 16bit little-endian mono PCM。"""
        n = len(pcm) // 2
        if n == 0:
            return
        self._samples.extend(struct.unpack(f"<{n}h", pcm))

    def __len__(self) -> int:
        return len(self._samples)

    def flush(self) -> VoiceFeatures:
        """取累积音频的特征并清空缓冲。音频不足 0.5s 返回空特征（不可信）。"""
        import numpy as np

        feats = VoiceFeatures()
        count = len(self._samples)
        if count < self.sample_rate // 2:
            self._samples.clear()
            return feats

        y = np.array(self._samples[: count - (count % FRAME_LEN)], dtype=np.float64) / 32768.0
        self._samples.clear()
        sr = self.sample_rate
        feats.duration_sec = count / sr

        # ---- 分帧能量 ----
        frames = y.reshape(-1, FRAME_LEN)
        energy = np.sum(frames * frames, axis=1)
        feats.energy_mean = float(np.mean(energy))
        feats.energy_std = float(np.std(energy))

        # ---- 停顿（能量阈值：低于峰值 5%，且 >0.5s 视为停顿）----
        if len(energy) > 0 and np.max(energy) > 0:
            silent = energy < np.max(energy) * 0.05
            min_frames = int(0.5 * 1000 / FRAME_MS)  # 20 帧 = 0.5s
            pauses: list[int] = []
            cur = 0
            for s in silent:
                if s:
                    cur += 1
                elif cur > 0:
                    pauses.append(cur)
                    cur = 0
            if cur > 0:
                pauses.append(cur)
            valid = [p for p in pauses if p >= min_frames]
            feats.pause_count = len(valid)
            if valid:
                feats.avg_pause_duration = float(np.mean(valid) * FRAME_MS / 1000)

        # ---- 基频（自相关法，逐帧，对浊音帧估计）----
        try:
            f0s = _estimate_f0_per_frame(frames, sr)
            if len(f0s) > 0:
                import numpy as np2
                arr = np2.array(f0s)
                feats.pitch_mean = float(np2.mean(arr))
                feats.pitch_std = float(np2.std(arr))
                if len(arr) > 1:
                    diffs = np2.abs(np2.diff(arr))
                    feats.pitch_jitter = float(np2.mean(diffs) / (feats.pitch_mean + 1e-6))
        except Exception as e:
            logger.debug("基频估计失败：%s", e)

        return feats

    def tension(self) -> tuple[float, VoiceFeatures | None]:
        """flush 并计算紧张度。音频不足时返回 (0, None) 表示无语音信号。"""
        feats = self.flush()
        if feats.duration_sec <= 0:
            return 0.0, None
        return compute_tension(feats), feats


def _estimate_f0_per_frame(frames, sr: int) -> list[float]:
    """每帧基频估计（归一化相似度，抗倍频）。

    sim(lag) = 1 - 2*sum(|x[n]-x[n+lag]|) / sum(x[n]^2 + x[n+lag]^2)
    同相（lag=周期）→ sim≈+1；反相（lag=半周期）→ sim≈-1。
    取相似度最大的 lag：半周期处被天然抑制，不会倍频。
    """
    import numpy as np

    lag_min = int(sr / MAX_PITCH_HZ)   # 40
    lag_max = int(sr / MIN_PITCH_HZ)   # 200
    energy = np.sum(frames * frames, axis=1)
    # 浊音帧判据：能量 ≥ 峰值 10%（与停顿检测同源的稳健阈值；
    # 均值阈值对不同音高段的能量微差过敏，会把整段误滤）
    if len(energy) == 0 or np.max(energy) <= 0:
        return []
    e_threshold = np.max(energy) * 0.10

    f0s: list[float] = []
    for i, frame in enumerate(frames):
        if energy[i] < e_threshold:
            continue  # 静音/清音帧跳过
        x = frame - np.mean(frame)
        best_lag, best_val = -1, -1.0
        for lag in range(lag_min, lag_max + 1):
            a = x[:-lag]
            b = x[lag:]
            denom = np.sum(a * a + b * b)
            if denom <= 0:
                continue
            val = 1.0 - 2.0 * np.sum(np.abs(a - b)) / denom
            if val > best_val:
                best_val, best_lag = val, lag
        # 相似度足够（周期性显著）才算浊音帧；0.5 宽松阈值防漏检
        if best_lag > 0 and best_val > 0.5:
            f0s.append(sr / best_lag)
    return f0s
