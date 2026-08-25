"""报告能力轴评分器单测（Phase 1：信号定分 + 权重合成）。"""

from __future__ import annotations

from app.modules.report.scoring import (
    compose_total,
    score_coverage,
    score_continuity,
    score_delivery_stability,
    score_fluency,
    score_pacing,
    score_voice,
)
from app.modules.scenarios import REGISTRY


class TestWeights:
    def test_weights_sum_to_100(self):
        for pack in REGISTRY.values():
            w = {axis.key: axis.weight for axis in pack.evaluation.axes}
            assert sum(w.values()) == 100
            assert len(w) == len(pack.evaluation.axes)


class TestFluency:
    def test_clean_speech_high_score(self):
        """无口癖、低重复 → 高分。"""
        s = score_fluency(filler_weighted=0, total_chars=100, repetition_rate=0.005)
        assert s >= 95

    def test_heavy_filler_low_score(self):
        """口癖密度高 → 显著扣分。"""
        s = score_fluency(filler_weighted=30, total_chars=100, repetition_rate=0.01)
        assert s < 80

    def test_bounded(self):
        s = score_fluency(filler_weighted=1000, total_chars=100, repetition_rate=1.0)
        assert s is not None
        assert 0 <= s <= 100

    def test_no_text_no_score(self):
        assert score_fluency(0, 0, 0) is None

    def test_continuity_uses_interpretable_text_facts(self):
        assert score_continuity(0, 100, 0.005) == score_fluency(0, 100, 0.005)


class TestPacing:
    def test_ideal_rate_full_score(self):
        s = score_pacing(rate_cpm=180)
        assert s >= 90

    def test_too_fast_penalty(self):
        s = score_pacing(rate_cpm=300)
        assert s < 85

    def test_zero_rate_no_score(self):
        """语速未知时不生成虚假满分。"""
        assert score_pacing(rate_cpm=0) is None


class TestVoice:
    def test_no_signal_none(self):
        assert score_voice(None) is None
        assert score_voice(0.0) is None

    def test_low_jitter_high(self):
        assert score_voice(0.045) >= 95

    def test_high_jitter_low(self):
        assert score_voice(0.12) < 60


class TestDeliveryStability:
    def test_passthrough(self):
        assert score_delivery_stability(80) == 80
        assert score_delivery_stability(None) is None


class TestCompose:
    def test_weighted_average(self):
        w = {axis.key: axis.weight for axis in REGISTRY["speech"].evaluation.axes}
        scores = {key: 70 + index * 5 for index, key in enumerate(w)}
        total = compose_total(scores, w)
        assert total is not None
        # 手算：Σ(score*weight)/100
        expect = sum(scores[k] * w[k] for k in w) / 100
        assert abs(total - expect) < 0.1

    def test_missing_axis_normalized(self):
        """缺省轴权重归一化，不影响其余轴。"""
        w = {axis.key: axis.weight for axis in REGISTRY["interview"].evaluation.axes}
        scores = {key: 70 + index * 5 for index, key in enumerate(w)}
        scores["continuity"] = None
        total = compose_total(scores, w)
        assert total is not None
        remaining_w = sum(w[k] for k in w if scores[k] is not None)
        expect = sum(scores[k] * w[k] for k in w if scores[k] is not None) / remaining_w
        assert abs(total - expect) < 0.1

    def test_all_missing_has_no_score(self):
        w = {axis.key: axis.weight for axis in REGISTRY["interview"].evaluation.axes}
        assert compose_total({}, w) is None

    def test_coverage_uses_available_weights(self):
        w = {"a": 60, "b": 40}
        assert score_coverage({"a": 80, "b": None}, w) == 0.6
        assert score_coverage({}, w) == 0.0
