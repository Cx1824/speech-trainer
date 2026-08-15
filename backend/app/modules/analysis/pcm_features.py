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
            f0s, times = _estimate_f0_per_frame(frames, sr)
            if len(f0s) > 0:
                arr = np.array(f0s)
                feats.pitch_mean = float(np.mean(arr))
                feats.pitch_std = float(np.std(arr))
                if len(arr) > 1:
                    # 情绪 2.0：先去趋势（滤掉慢速语调起伏），只留快速颤动算 jitter。
                    # 直接 diff 会把抑扬顿挫（正常重音/语调）误判为紧张。
                    feats.pitch_jitter = _detrended_jitter(arr, times)
        except Exception as e:
            logger.debug("基频估计失败：%s", e)

        return feats

    def tension(self) -> tuple[float, VoiceFeatures | None]:
        """flush 并计算紧张度。音频不足时返回 (0, None) 表示无语音信号。"""
        feats = self.flush()
        if feats.duration_sec <= 0:
            return 0.0, None
        return compute_tension(feats), feats


class LivePcmTracker:
    """实时声学追踪（情绪/语速实时化用）。

    与 PcmFeatureBuffer（句子级整段计算）不同，本类做增量处理：
    - push() 逐帧计算能量 + 浊音帧基频，结果存入定长环形缓冲（默认 8s）
    - snapshot(window_sec) 取最近 N 秒窗口聚合 → VoiceFeatures（实时紧张度用）
    - 说话秒/静音秒累积（实时语速 = 字数 ÷ 发音时长，而非音频总时长）
    - 全程 O(帧)，单帧 25ms 计算微秒级

    注意：不负责清空——句子定稿后由调用方 reset_speech_stats() 清语速统计，
    声学环形缓冲保留（跨句连续观察紧张度）。
    """

    def __init__(self, window_sec: float = 8.0, sample_rate: int = SAMPLE_RATE) -> None:
        import numpy as np

        self._np = np
        self.sample_rate = sample_rate
        max_frames = int(window_sec * 1000 / FRAME_MS)
        # 环形缓冲：每帧 (energy, f0)；f0=0 表示清音/静音帧
        self._energy = np.zeros(max_frames)
        self._f0 = np.zeros(max_frames)
        self._cap = max_frames
        self._n = 0            # 已写总数（>=cap 后环形覆盖）
        self._tail = np.zeros(FRAME_LEN, dtype=np.int16)  # 跨 chunk 的残帧拼接
        self._tail_len = 0
        # 语音活动统计（实时语速分母：发音秒数）
        self.speech_frames = 0     # 发音帧累计（自上次 reset）
        self.silence_frames = 0    # 静音帧累计（自上次 reset）
        self._last_silence_start: float | None = None  # 当前连续静音起始时间（秒）
        self._speech_run_start: float | None = None    # 当前连续发音起始时间

    # ---- 增量喂入 ----

    def push(self, pcm: bytes) -> None:
        """喂入 PCM chunk（16bit LE mono）。内部按 25ms 帧切分逐帧处理。"""
        import struct as _st

        n = len(pcm) // 2
        if n == 0:
            return
        samples = _st.unpack(f"<{n}h", pcm)
        np = self._np

        buf = np.concatenate([
            self._tail[: self._tail_len], np.frombuffer(np.array(samples, dtype=np.int16), dtype=np.int16)
        ])
        self._tail_len = 0

        n_frames = len(buf) // FRAME_LEN
        for i in range(n_frames):
            frame = buf[i * FRAME_LEN:(i + 1) * FRAME_LEN].astype(np.float64) / 32768.0
            self._push_frame(frame)
        rest = len(buf) - n_frames * FRAME_LEN
        if rest > 0:
            self._tail[:rest] = buf[n_frames * FRAME_LEN:]
            self._tail_len = rest

    def _push_frame(self, frame) -> None:
        np = self._np
        energy = float(np.sum(frame * frame))
        t_sec = self._n * FRAME_MS / 1000.0
        idx = self._n % self._cap
        f0 = 0.0
        # 浊音判据与整段版一致：能量 ≥ 窗口内峰值 10% 才做基频估计
        recent_max = float(np.max(self._energy)) if self._n > 0 else 0.0
        recent_max = max(recent_max, energy)
        if energy >= recent_max * 0.10 and energy > 1e-6:
            sr = self.sample_rate
            lag_min = int(sr / MAX_PITCH_HZ)
            lag_max = min(int(sr / MIN_PITCH_HZ), FRAME_LEN - 1)
            x = frame - np.mean(frame)
            f0 = _best_f0(x, sr, lag_min, lag_max)
        self._energy[idx] = energy
        self._f0[idx] = f0
        self._n += 1

        # ---- 语音活动统计（能量阈值：静音 = 峰值 5%，与停顿检测一致）----
        peak = float(np.max(self._energy)) if self._n > 0 else 0.0
        is_silence = energy < peak * 0.05 or energy <= 1e-7
        if is_silence:
            self.silence_frames += 1
            if self._last_silence_start is None:
                self._last_silence_start = t_sec
            self._speech_run_start = None
        else:
            self.speech_frames += 1
            if self._speech_run_start is None:
                self._speech_run_start = t_sec
            self._last_silence_start = None

    # ---- 查询 ----

    @property
    def speech_sec(self) -> float:
        """自上次 reset 以来的发音秒数（语速分母）。"""
        return self.speech_frames * FRAME_MS / 1000.0

    @property
    def silence_sec(self) -> float:
        return self.silence_frames * FRAME_MS / 1000.0

    def current_silence_sec(self) -> float:
        """当前连续静音时长（冷场检测用）。说话中返回 0。"""
        if self._last_silence_start is None:
            return 0.0
        return self._n * FRAME_MS / 1000.0 - self._last_silence_start

    def current_speech_run_sec(self) -> float:
        """当前连续发音（无有效停顿）时长（换气提醒用）。"""
        if self._speech_run_start is None:
            return 0.0
        return self._n * FRAME_MS / 1000.0 - self._speech_run_start

    def snapshot(self, window_sec: float = 5.0) -> VoiceFeatures:
        """取最近 window_sec 秒窗口聚合特征（实时紧张度用）。

        不足窗口时用全部已有数据；完全无数据返回空特征。
        """
        np = self._np
        total = self._n
        if total == 0:
            return VoiceFeatures()
        n_win = min(int(window_sec * 1000 / FRAME_MS), total, self._cap)
        if n_win < 4:
            return VoiceFeatures()

        # 环形缓冲取最近 n_win 帧（可能跨环首尾）
        idxs = np.arange(total - n_win, total) % self._cap
        energies = self._energy[idxs]
        f0s = self._f0[idxs]

        feats = VoiceFeatures()
        feats.duration_sec = n_win * FRAME_MS / 1000.0
        feats.energy_mean = float(np.mean(energies))
        feats.energy_std = float(np.std(energies))
        # 窗口内停顿（复用同款阈值逻辑）
        if feats.energy_mean > 0:
            silent = energies < np.max(energies) * 0.05
            min_frames = int(0.5 * 1000 / FRAME_MS)
            cur = 0
            pauses = []
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
        # 基频（去趋势 jitter，与整段版一致）
        voiced = f0s[f0s > 0]
        if len(voiced) >= 4:
            feats.pitch_mean = float(np.mean(voiced))
            feats.pitch_std = float(np.std(voiced))
            feats.pitch_jitter = _detrended_jitter(voiced, np.arange(len(voiced)) * FRAME_MS / 1000.0)
        elif len(voiced) > 1:
            feats.pitch_mean = float(np.mean(voiced))
            feats.pitch_jitter = float(np.mean(np.abs(np.diff(voiced))) / (feats.pitch_mean + 1e-6))
        return feats

    def reset_speech_stats(self) -> None:
        """句子定稿后清语速统计（字数归零同步，分母也归零）。声学缓冲保留。"""
        self.speech_frames = 0
        self.silence_frames = 0
        self._last_silence_start = None
        self._speech_run_start = None


# ---------------------------------------------------------------------------
# 校准：朗读基准段落 → 个人基线
# ---------------------------------------------------------------------------

# 校准提示语：给使用者一段平和的朗读材料（数字/专名混合，长短句交错，
# 覆盖正常语速下的自然停顿与语调）
CALIBRATION_TEXT = (
    "大家好，我是一名热爱表达的普通人。今天天气不错，微风轻拂，阳光洒在窗台上。"
    "我平时喜欢读书、跑步，也喜欢和朋友聊天。有人说，说话是一件简单的事，"
    "但我相信，把话说得清楚、说得从容，需要长期的练习。现在是我做声音校准的时间，"
    "我会用最自然、最放松的状态读完这一段话。数字方面，从 1 数到 10，"
    "一二三四五六七八九十。好，读完了，谢谢大家。"
)


def build_baseline(
    feats_list: list[VoiceFeatures],
    char_count: int,
    created_at: str = "",
) -> VoiceBaseline:
    """从校准音频的多个句子特征聚合出个人基线。

    feats_list：校准朗读按句切分的特征列表（PcmFeatureBuffer.flush 的输出）
    char_count：ASR 识别的总字数（算语速用）
    """
    from datetime import datetime, timezone

    from app.modules.analysis.voice_features import DEFAULT_BASELINE, VoiceBaseline

    if created_at:
        ts = created_at
    else:
        ts = datetime.now(timezone.utc).isoformat()

    bl = VoiceBaseline(created_at=ts)
    total_sec = sum(f.duration_sec for f in feats_list)
    if total_sec < 10.0 or not feats_list:
        # 样本不足：保持默认基线但记录时长（前端提示"再读一次"）
        bl.sample_sec = total_sec
        return bl

    jitters = [f.pitch_jitter for f in feats_list if f.pitch_jitter > 0]
    pitches = [f.pitch_mean for f in feats_list if f.pitch_mean > 0]
    pauses = sum(f.pause_count for f in feats_list)

    if jitters:
        bl.pitch_jitter = float(sum(jitters) / len(jitters))
    if pitches:
        bl.pitch_mean = float(sum(pitches) / len(pitches))
    if char_count > 0:
        bl.speech_rate = char_count / total_sec
    bl.pause_rate = pauses / total_sec * 60
    bl.sample_sec = total_sec
    return bl


def _detrended_jitter(f0: "np.ndarray", times: "np.ndarray") -> float:
    """去趋势 jitter：分离慢速语调曲线与快速生理性颤动。

    原理：真紧张的声带颤动是 8-12Hz 快速振荡（帧间隔 25ms 足以采样），
    而语调抑扬是 <4Hz 的慢速漂移。用滑动中值滤波拟合语调基线，
    残差的平均绝对偏差 / 均值 = 快速 jitter。
    """
    import numpy as np

    if len(f0) < 4:
        diffs = np.abs(np.diff(f0))
        return float(np.mean(diffs) / (np.mean(f0) + 1e-6)) if len(f0) > 1 else 0.0

    # 滑动中值窗口 ≈ 0.4s（16 帧 @25ms）：能跟上 2.5Hz 以内的语调变化，
    # 但不被 8Hz+ 的快速颤动带偏（中值对短振荡鲁棒）
    win = 16
    half = win // 2
    trend = np.empty_like(f0)
    for i in range(len(f0)):
        lo = max(0, i - half)
        hi = min(len(f0), i + half + 1)
        trend[i] = np.median(f0[lo:hi])

    resid = f0 - trend
    return float(np.mean(np.abs(resid)) / (np.mean(f0) + 1e-6))


def _best_f0(x, sr: int, lag_min: int, lag_max: int) -> float:
    """单帧基频：归一化相似度法 + 八度校验。

    sim(lag) = 1 - 2*sum(|x[n]-x[n+lag]|) / sum(x[n]^2 + x[n+lag]^2)
    同相（lag=周期）→ sim≈+1；反相（lag=半周期）→ sim≈-1。

    八度校验：argmax 可能落在双周期（2T）处（基频微变时该处 sim 反而更高）。
    找到最优 lag 后反复减半：若半 lag 处 sim 仍达最优的 90%，说明真实周期
    是一半 → 改用半 lag。彻底消除 f0 减半的错误。
    返回 0 表示非浊音（sim < 0.5）。
    """
    import numpy as np

    def sim(lag: int) -> float:
        a = x[:-lag]
        b = x[lag:]
        denom = np.sum(a * a + b * b)
        return 1.0 - 2.0 * np.sum(np.abs(a - b)) / denom if denom > 0 else -1.0

    lags = range(lag_min, lag_max + 1)
    best_lag, best_val = -1, -1.0
    for lag in lags:
        v = sim(lag)
        if v > best_val:
            best_val, best_lag = v, lag
    if best_lag <= 0 or best_val <= 0.5:
        return 0.0

    # 八度校验：最优 lag 减半后 sim 仍≥90% → 真实周期是一半
    while best_lag // 2 >= lag_min:
        half = best_lag // 2
        if sim(half) >= best_val * 0.90:
            best_lag = half
        else:
            break
    return sr / best_lag


def _estimate_f0_per_frame(frames, sr: int) -> tuple["list[float]", "list[float]"]:
    """每帧基频估计（归一化相似度，抗倍频）。

    sim(lag) = 1 - 2*sum(|x[n]-x[n+lag]|) / sum(x[n]^2 + x[n+lag]^2)
    同相（lag=周期）→ sim≈+1；反相（lag=半周期）→ sim≈-1。
    取相似度最大的 lag：半周期处被天然抑制，不会倍频。

    返回 (f0 列表, 对应帧中心时间秒列表)。
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
    times: list[float] = []
    for i, frame in enumerate(frames):
        if energy[i] < e_threshold:
            continue  # 静音/清音帧跳过
        x = frame - np.mean(frame)
        f0 = _best_f0(x, sr, lag_min, lag_max)
        # 相似度足够（周期性显著）才算浊音帧；0.5 宽松阈值防漏检
        if f0 > 0:
            f0s.append(f0)
            times.append((i + 0.5) * FRAME_LEN / sr)
    return f0s, times
