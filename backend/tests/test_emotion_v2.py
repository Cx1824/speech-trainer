"""表达信号代理值单测：去趋势 jitter / 连续打分 / 基线 / 平滑 / 校准聚合。"""

from __future__ import annotations

import numpy as np

from app.modules.analysis.emotion import EmotionSmoother, analyze_emotion
from app.modules.analysis.pcm_features import (
    CALIBRATION_TEXT,
    FRAME_LEN,
    SAMPLE_RATE,
    PcmFeatureBuffer,
    _detrended_jitter,
    build_baseline,
)
from app.modules.analysis.text_rules import analyze_text
from app.modules.analysis.voice_features import (
    DEFAULT_BASELINE,
    VoiceBaseline,
    VoiceFeatures,
    compute_tension_v2,
)


def _synth_pcm(f0_curve, sr=SAMPLE_RATE, dur=None) -> bytes:
    """按基频曲线合成 PCM（用于喂 PcmFeatureBuffer）。

    f0_curve: list[(t_sec, f0_hz)] 分段线性基频
    """
    total = dur or max(t for t, _ in f0_curve) + 0.1
    n = int(sr * total)
    y = np.zeros(n)
    phase = 0.0
    ts = np.arange(n) / sr
    f0s = np.interp(ts, [t for t, _ in f0_curve], [f for _, f in f0_curve])
    for i in range(n):
        phase += 2 * np.pi * f0s[i] / sr
        y[i] = 0.6 * np.sin(phase) + 0.2 * np.sin(2 * phase)
    return (y * 32767 * 0.8).astype(np.int16).tobytes()


class TestDetrendedJitter:
    def test_pure_slow_intonation_low_jitter(self):
        """慢速语调（大幅起伏但变化 <2Hz）不应形成高 jitter。"""
        n = 160  # 4s，160 帧
        times = np.arange(n) * 0.025
        # 基频 150→220→150Hz 慢速漂移（一个完整语调弧线，周期 2s = 0.5Hz）
        f0 = 185 + 35 * np.sin(2 * np.pi * 0.5 * times)
        j = _detrended_jitter(f0, list(times))
        # 慢速漂移应被趋势项吸收，残差极小
        assert j < 0.02, f"慢速语调 jitter={j} 应 < 0.02"

    def test_fast_tremor_high_jitter(self):
        """人工注入 8Hz 快速频率调制时，jitter 应显著升高。"""
        n = 160
        times = np.arange(n) * 0.025
        f0 = 185 + 15 * np.sin(2 * np.pi * 8.0 * times)
        j = _detrended_jitter(f0, list(times))
        assert j > 0.03, f"8Hz 颤动 jitter={j} 应 > 0.03"

    def test_mixed_tremor_detected_under_intonation(self):
        """语调 + 颤动混合：颤动分量应仍被检出（高于纯语调的 jitter）。"""
        n = 160
        times = np.arange(n) * 0.025
        slow = 35 * np.sin(2 * np.pi * 0.5 * times)
        tremor = 8 * np.sin(2 * np.pi * 9.0 * times)
        j_mixed = _detrended_jitter(185 + slow + tremor, list(times))
        j_slow = _detrended_jitter(185 + slow, list(times))
        assert j_mixed > j_slow * 2, f"混合 jitter={j_mixed} 应显著高于纯语调 {j_slow}"


class TestComputeTensionV2:
    def _feats(self, **kw) -> VoiceFeatures:
        f = VoiceFeatures(duration_sec=6.0)
        for k, v in kw.items():
            setattr(f, k, v)
        return f

    def test_at_baseline_low_score(self):
        """全部信号贴合个人基线时，表达波动代理值较低。"""
        bl = VoiceBaseline(pitch_jitter=0.02, speech_rate=4.2, pause_rate=3.0, sample_sec=30)
        f = self._feats(pitch_jitter=0.02, pause_count=1, energy_mean=1.0, energy_std=0.7)
        score, detail = compute_tension_v2(f, bl, speech_rate=4.2)
        assert score < 40, f"贴合基线 score={score}"
        assert detail["jitter"] == 0

    def test_tremor_way_above_baseline_high_score(self):
        """颤抖远超基线（3 倍+）→ jitter 贡献满分 30（v2.1 权重）。"""
        bl = VoiceBaseline(pitch_jitter=0.02, sample_sec=30)
        f = self._feats(pitch_jitter=0.06, energy_mean=1.0, energy_std=0.7)
        score, detail = compute_tension_v2(f, bl)
        assert detail["jitter"] == 30.0
        assert score >= 55

    def test_speech_rate_spike(self):
        """语速飙升（1.6 倍基线）→ 语速贡献满分 25。"""
        bl = VoiceBaseline(speech_rate=4.0, sample_sec=30)
        f = self._feats(energy_mean=1.0, energy_std=0.5)
        score, detail = compute_tension_v2(f, bl, speech_rate=6.4)
        assert detail["speech_rate"] == 25.0

    def test_speech_rate_slow_mild(self):
        """语速过慢 → 轻度贡献（上限 10，弱于过快）。"""
        bl = VoiceBaseline(speech_rate=4.5, sample_sec=30)
        f = self._feats(energy_mean=1.0, energy_std=0.5)
        score, detail = compute_tension_v2(f, bl, speech_rate=2.2)
        assert 0 < detail["speech_rate"] <= 10.0

    def test_short_utterance_skips_pause(self):
        """<3s 短句不评停顿/犹豫（防误判）。"""
        bl = VoiceBaseline(pause_rate=3.0, sample_sec=30)
        f = self._feats(duration_sec=2.0, pitch_jitter=0.01, pause_count=0, energy_mean=1.0, energy_std=0.5)
        _, detail = compute_tension_v2(f, bl)
        assert "pause" not in detail
        assert "hesitation" not in detail

    def test_default_baseline_fallback(self):
        """无个人基线时使用算法参考值且不抛异常。"""
        f = self._feats(pitch_jitter=DEFAULT_BASELINE["pitch_jitter"], energy_mean=1.0, energy_std=0.5)
        score, _ = compute_tension_v2(f, None)
        assert 0 <= score <= 100

    def test_energy_gain_invariant(self):
        """能量 CV 对麦克风增益不变：能量×10 → 分数不变。"""
        bl = VoiceBaseline(pitch_jitter=0.02, sample_sec=30)
        f1 = self._feats(pitch_jitter=0.02, energy_mean=1.0, energy_std=2.0)
        f2 = self._feats(pitch_jitter=0.02, energy_mean=10.0, energy_std=20.0)
        s1, _ = compute_tension_v2(f1, bl)
        s2, _ = compute_tension_v2(f2, bl)
        assert abs(s1 - s2) < 0.01


class TestEmotionSmoother:
    def test_ema_smoothing(self):
        """EMA：单句尖峰被平滑，连续高值逐渐上升。"""
        sm = EmotionSmoother(alpha=0.45)
        v1 = sm.update(40)          # 首句原样
        assert v1 == 40
        v2 = sm.update(80)          # 尖峰不直接跳到 80
        assert v2 < 60
        v3 = sm.update(80)
        assert v3 > v2              # 连续高值持续上升

    def test_reset(self):
        sm = EmotionSmoother()
        sm.update(80)
        sm.reset()
        assert sm.update(30) == 30


class TestBuildBaseline:
    def test_aggregation(self):
        """多段特征 + 字数 → 基线聚合正确。"""
        feats = [
            VoiceFeatures(duration_sec=6.0, pitch_jitter=0.018, pitch_mean=160, pause_count=2),
            VoiceFeatures(duration_sec=6.0, pitch_jitter=0.022, pitch_mean=170, pause_count=1),
            VoiceFeatures(duration_sec=8.0, pitch_jitter=0.020, pitch_mean=165, pause_count=2),
        ]
        bl = build_baseline(feats, char_count=84)  # 20s / 84字 = 4.2 字/s
        assert bl.is_valid()
        assert abs(bl.pitch_jitter - 0.020) < 0.001
        assert abs(bl.pitch_mean - 165) < 1
        assert abs(bl.speech_rate - 4.2) < 0.01
        assert abs(bl.pause_rate - 15.0) < 0.01  # 5 次 / 20s * 60 = 15/min
        assert bl.sample_sec == 20.0

    def test_insufficient_sample(self):
        """<10s 不出有效基线。"""
        bl = build_baseline([VoiceFeatures(duration_sec=6.0)], 25)
        assert not bl.is_valid()
        assert bl.sample_sec == 6.0

    def test_roundtrip_dict(self):
        bl = VoiceBaseline(pitch_jitter=0.025, speech_rate=4.0, pause_rate=2.5, pitch_mean=180, sample_sec=30, created_at="2026-08-15T00:00:00+00:00")
        d = bl.to_dict()
        bl2 = VoiceBaseline.from_dict(d)
        assert abs(bl2.pitch_jitter - 0.025) < 1e-5
        assert abs(bl2.speech_rate - 4.0) < 1e-3
        assert bl2.created_at == bl.created_at


class TestEndToEndEmotion:
    def test_steady_voice_with_intonation_stays_near_baseline(self):
        """端到端：慢速语调变化不应被当作快速波动。"""
        # 6s 语音，基频 160→200→160 慢速弧线（有感情朗读）
        pcm = _synth_pcm([(0, 160), (3, 200), (6, 160)], dur=6.0)
        buf = PcmFeatureBuffer()
        buf.push(pcm)
        feats = buf.flush()
        assert feats.pitch_jitter > 0  # 有信号
        text_res = analyze_text("我负责这个项目，从零到一做了三年。")
        bl = VoiceBaseline(
            pitch_jitter=feats.pitch_jitter,  # 用自身做基线（贴合场景）
            speech_rate=4.2, pause_rate=3.0, sample_sec=30,
        )
        snap = analyze_emotion(text_res, feats, baseline=bl, speech_rate=4.0)
        assert snap.tension_score < 40, f"贴近基线的朗读波动值过高：{snap.tension_score}"
        assert snap.calibrated

    def test_fast_frequency_modulation_raises_deviation_score(self):
        """端到端：人工注入 8Hz 频率调制时，波动代理值升高。"""
        # 用正弦调制模拟快速颤动
        ts = np.arange(int(SAMPLE_RATE * 6)) / SAMPLE_RATE
        f0 = 180 + 20 * np.sin(2 * np.pi * 8 * ts)  # 8Hz 颤动
        # 合成逐样本变基频的 PCM
        y = np.zeros(len(ts))
        phase = 0.0
        for i in range(len(ts)):
            phase += 2 * np.pi * f0[i] / SAMPLE_RATE
            y[i] = 0.6 * np.sin(phase)
        pcm = (y * 32767 * 0.8).astype(np.int16).tobytes()
        buf = PcmFeatureBuffer()
        buf.push(pcm)
        feats = buf.flush()
        bl = VoiceBaseline(pitch_jitter=0.02, sample_sec=30)  # 基线 0.02
        text_res = analyze_text("我负责这个项目。")
        snap = analyze_emotion(text_res, feats, baseline=bl)
        assert snap.tension_score >= 55, f"快速调制未提高代理值：{snap.tension_score} jitter={feats.pitch_jitter}"
        assert "jitter" in snap.factors

    def test_calibration_text_reasonable(self):
        """校准文本存在且够长（读完 ≥10s）。"""
        assert len(CALIBRATION_TEXT) >= 80
        assert len(CALIBRATION_TEXT) / 4.2 >= 10
