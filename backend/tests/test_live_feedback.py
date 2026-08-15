"""实时反馈引擎 + 实时声学追踪单测。"""

from __future__ import annotations

import numpy as np

from app.modules.analysis.live_feedback import LiveFeedbackEngine
from app.modules.analysis.pcm_features import FRAME_MS, LivePcmTracker

SR = 16000


def _synth(f0_lo, f0_hi, dur):
    n = int(SR * dur)
    f0 = np.linspace(f0_lo, f0_hi, n)
    y = np.zeros(n)
    phase = 0.0
    for i in range(n):
        phase += 2 * np.pi * f0[i] / SR
        y[i] = 0.6 * np.sin(phase)
    return (y * 32767 * 0.8).astype(np.int16).tobytes()


class TestLiveFeedbackEngine:
    def test_filler_detected_immediately(self):
        """口癖词出现在 partial 新增文本中 → 立即反馈。"""
        eng = LiveFeedbackEngine()
        fbs = eng.on_partial("我觉得嗯这个方案")
        kinds = [(f["kind"], f["word"]) for f in fbs]
        assert ("filler", "嗯") in kinds

    def test_cooldown_prevents_spam(self):
        """冷却期内同词不重复提示。"""
        eng = LiveFeedbackEngine()
        assert eng.on_partial("嗯")          # 第一次触发
        assert not eng.on_partial("嗯好")     # 冷却内不触发
        assert not eng.on_partial("嗯好的")   # 仍不触发

    def test_consecutive_repeat_detected(self):
        """连读重复（就是就是）→ repeat 反馈。"""
        eng = LiveFeedbackEngine()
        fbs = eng.on_partial("我就是就是觉得")
        assert any(f["kind"] == "repeat" and f["word"] == "就是" for f in fbs)

    def test_long_unbroken_sentence_warning(self):
        """partial 超 60 字未断 → 超长句预警。"""
        eng = LiveFeedbackEngine()
        text = "这是一个特别长的句子" * 10  # 100 字
        fbs = eng.on_partial(text)
        assert any(f["kind"] == "long_sentence" for f in fbs)

    def test_hedge_detected(self):
        eng = LiveFeedbackEngine()
        fbs = eng.on_partial("这个可能大概行吧")
        words = {f["word"] for f in fbs}
        assert "可能" in words or "大概" in words

    def test_partial_regression_resets_scan(self):
        """partial 文本回退（变短）→ 扫描位置重置，不误报不漏报。"""
        eng = LiveFeedbackEngine()
        eng.on_partial("嗯我觉得这个方案")
        # ASR 修正后回退（部分场景 partial 会缩短）
        fbs = eng.on_partial("我觉得这个")
        assert not any(f["word"] == "嗯" for f in fbs)  # 回退文本无嗯

    def test_rhythm_silence(self):
        """冷场：静音 >3s 提示。"""
        eng = LiveFeedbackEngine()
        fbs = eng.on_rhythm(speech_run_sec=0, silence_sec=3.5, speech_rate=None, base_rate=4.2, speaking=True)
        assert any(f["kind"] == "silence" for f in fbs)

    def test_rhythm_no_breath(self):
        """连续发音 >15s 无停顿 → 换气提醒。"""
        eng = LiveFeedbackEngine()
        fbs = eng.on_rhythm(speech_run_sec=16.0, silence_sec=0, speech_rate=4.0, base_rate=4.2, speaking=True)
        assert any(f["kind"] == "no_breath" for f in fbs)

    def test_rhythm_fast_run(self):
        """连续快说 >10s 且语速超基线 120% → 语速提醒。"""
        eng = LiveFeedbackEngine()
        fbs = eng.on_rhythm(speech_run_sec=11.0, silence_sec=0, speech_rate=5.5, base_rate=4.2, speaking=True)
        assert any(f["kind"] == "fast_run" for f in fbs)

    def test_rhythm_normal_no_feedback(self):
        """正常节奏（短发言、正常语速）→ 无反馈。"""
        eng = LiveFeedbackEngine()
        fbs = eng.on_rhythm(speech_run_sec=5.0, silence_sec=0.5, speech_rate=4.0, base_rate=4.2, speaking=True)
        assert not fbs

    def test_reset_sentence(self):
        """句子定稿重置后，同一口癖可再次提示（新句重新计）。"""
        eng = LiveFeedbackEngine()
        eng.on_partial("嗯")
        eng.reset_sentence()
        assert eng.on_partial("嗯")


class TestLivePcmTracker:
    def test_speech_sec_counts_only_voice(self):
        """发音秒只计有声帧，静音不计。"""
        tr = LivePcmTracker()
        tr.push(_synth(160, 190, 2.0))
        tr.push(np.zeros(SR * 2, dtype=np.int16).tobytes())
        assert 1.5 < tr.speech_sec <= 2.2   # 约 2s 语音
        assert tr.silence_sec >= 1.5        # 约 2s 静音

    def test_current_silence_grows(self):
        tr = LivePcmTracker()
        tr.push(_synth(160, 190, 1.0))
        assert tr.current_silence_sec() < 0.5
        tr.push(np.zeros(int(SR * 1.5), dtype=np.int16).tobytes())
        assert tr.current_silence_sec() >= 1.0

    def test_speech_run_sec(self):
        """连续发音时长（无 >0.5s 停顿时持续增长）。"""
        tr = LivePcmTracker()
        tr.push(_synth(160, 190, 3.0))
        assert tr.current_speech_run_sec() >= 2.5

    def test_snapshot_features(self):
        """窗口快照产出完整特征（能量/基频/时长）。"""
        tr = LivePcmTracker()
        tr.push(_synth(160, 200, 4.0))
        f = tr.snapshot(5.0)
        assert 3.5 <= f.duration_sec <= 4.2
        assert f.energy_mean > 0
        assert f.pitch_mean > 100   # 合成音在 160-200 区间

    def test_snapshot_respects_window(self):
        """窗口 2s：喂 6s 音频后快照只含最近 2s。"""
        tr = LivePcmTracker()
        tr.push(_synth(160, 170, 3.0))
        tr.push(_synth(190, 200, 3.0))
        f = tr.snapshot(2.0)
        assert f.duration_sec <= 2.2
        assert f.pitch_mean > 180    # 最近段是 190-200

    def test_reset_speech_stats_keeps_acoustic(self):
        """reset 语速统计不影响声学环形缓冲。"""
        tr = LivePcmTracker()
        tr.push(_synth(160, 190, 4.0))
        tr.reset_speech_stats()
        assert tr.speech_sec == 0.0
        f = tr.snapshot(5.0)
        assert f.energy_mean > 0     # 声学还在

    def test_ring_buffer_wraps(self):
        """超过缓冲容量（8s）后环形覆盖不崩溃，快照仍可用。"""
        tr = LivePcmTracker(window_sec=4.0)
        tr.push(_synth(160, 190, 3.0))
        tr.push(np.zeros(SR, dtype=np.int16).tobytes())
        tr.push(_synth(170, 200, 3.0))   # 超容量，环形覆盖
        f = tr.snapshot(2.0)
        assert f.duration_sec > 0
