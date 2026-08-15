"""PCM 声学特征单测（合成音频验证）。"""

from __future__ import annotations

import math
import struct

from app.modules.analysis.pcm_features import PcmFeatureBuffer, SAMPLE_RATE


def sine_pcm(freq_hz: float, seconds: float, amp: float = 0.6) -> bytes:
    """合成稳定正弦波 PCM（16k/16bit/mono）。"""
    n = int(SAMPLE_RATE * seconds)
    return struct.pack(f"<{n}h", *[int(amp * 32767 * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE)) for i in range(n)])


def silence_pcm(seconds: float) -> bytes:
    n = int(SAMPLE_RATE * seconds)
    return b"\x00\x00" * n


def concat(*parts: bytes) -> bytes:
    return b"".join(parts)


class TestPcmFeatureBuffer:
    def test_stable_voice_low_jitter(self):
        """稳定 150Hz 正弦（模拟平稳说话）：有基频、抖动小。"""
        buf = PcmFeatureBuffer()
        buf.push(sine_pcm(150, 2.0))
        tension, feats = buf.tension()
        assert feats is not None
        assert feats.duration_sec > 1.9
        assert 100 < feats.pitch_mean < 250   # 基频接近 150Hz
        assert feats.pitch_jitter < 0.05      # 稳定音 → 低抖动

    def test_too_short_returns_none(self):
        """音频不足 0.5s：返回无信号（不误判）。"""
        buf = PcmFeatureBuffer()
        buf.push(sine_pcm(150, 0.3))
        tension, feats = buf.tension()
        assert feats is None

    def test_silence_detected_as_pause(self):
        """说话-停顿-说话：停顿计数 ≥1。"""
        buf = PcmFeatureBuffer()
        buf.push(concat(
            sine_pcm(180, 1.0),
            silence_pcm(0.8),   # 明显停顿
            sine_pcm(180, 1.0),
        ))
        tension, feats = buf.tension()
        assert feats is not None
        assert feats.pause_count >= 1

    def test_flush_clears_buffer(self):
        buf = PcmFeatureBuffer()
        buf.push(sine_pcm(150, 1.0))
        buf.flush()
        assert len(buf) == 0
        # 再 flush 空缓冲 → 无信号
        _, feats = buf.tension()
        assert feats is None

    def test_push_empty_noop(self):
        buf = PcmFeatureBuffer()
        buf.push(b"")
        buf.push(b"\x01")  # 奇数字节（半个样本）
        assert len(buf) == 0

    def test_energy_computed(self):
        buf = PcmFeatureBuffer()
        buf.push(sine_pcm(150, 1.0))
        feats = buf.flush()
        assert feats.energy_mean > 0
