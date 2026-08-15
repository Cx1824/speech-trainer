"""场景包注册表单测。"""

from __future__ import annotations

import pytest

from app.modules.scenarios import REGISTRY, get_pack, list_packs
from app.modules.scenarios.base import ScenarioContext


@pytest.fixture
def ctx() -> ScenarioContext:
    return ScenarioContext(
        position="季度汇报",
        duration_limit=5,
        material_text="Q2 营收增长 30%",
        history=[
            {"role": "ai", "text": "请开始你的汇报。"},
            {"role": "user", "text": "各位领导好。"},
        ],
    )


class TestRegistry:
    def test_three_packs_registered(self):
        assert set(REGISTRY.keys()) == {"interview", "presentation", "speech"}
        assert len(list_packs()) == 3

    def test_fallback_to_interview(self):
        assert get_pack("unknown").key == "interview"
        assert get_pack(None).key == "interview"
        assert get_pack("").key == "interview"

    def test_keys_unique(self):
        keys = [p.key for p in list_packs()]
        assert len(keys) == len(set(keys))


class TestStages:
    def test_all_stages_build_messages(self, ctx):
        for pack in REGISTRY.values():
            for stage in pack.stages:
                msgs = stage.prompt_builder(ctx)
                assert isinstance(msgs, list) and len(msgs) >= 2
                assert msgs[0]["role"] == "system"
                assert all(m.get("content") is not None for m in msgs)

    def test_report_stage_is_last(self):
        for pack in REGISTRY.values():
            assert pack.stages[-1].key == "report"
            assert pack.stages[-1].question_limit == 0

    def test_speech_no_mid_interrupt(self):
        """演讲场景无质询环节（interrupt_allowed=False 且无 qa 阶段）。"""
        speech = REGISTRY["speech"]
        assert speech.interrupt_allowed is False
        assert "qa" not in [s.key for s in speech.stages]

    def test_presentation_has_qa(self):
        pres = REGISTRY["presentation"]
        assert "qa" in [s.key for s in pres.stages]
        qa = next(s for s in pres.stages if s.key == "qa")
        assert qa.question_limit >= 1


class TestPackFlags:
    def test_interview_flags(self):
        p = REGISTRY["interview"]
        assert p.needs_resume is True
        assert p.needs_material is False
        assert p.timed is False

    def test_presentation_flags(self):
        p = REGISTRY["presentation"]
        assert p.needs_resume is False
        assert p.needs_material is True
        assert p.timed is True

    def test_speech_flags(self):
        p = REGISTRY["speech"]
        assert p.needs_resume is False
        assert p.needs_material is True
        assert p.timed is True


class TestReportFocus:
    def test_all_have_advice_sections(self):
        for pack in REGISTRY.values():
            assert len(pack.report_focus.advice_sections) >= 3
            assert len(pack.report_focus.dimensions) >= 3

    def test_focus_differs_by_scenario(self):
        iv = REGISTRY["interview"].report_focus
        pres = REGISTRY["presentation"].report_focus
        assert iv.advice_sections != pres.advice_sections
