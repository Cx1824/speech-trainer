"""场景化评分 v4 的跨场景验收测试。

这些测试只验证公开的场景配置与共享报告管线：表达事实应跨场景保持
一致，语义评价与关键任务约束则必须完全由 ScenarioPack 声明。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules import interview
from app.modules.report import generator as report_generator
from app.modules.report.scoring import compose_total
from app.modules.scenarios import get_pack
from app.schemas import InterviewConfigIn


APPROVED_AXES = {
    "interview": (
        ("continuity", 10, "signal"),
        ("pacing", 10, "signal"),
        ("response_structure", 25, "llm"),
        ("evidence_results", 20, "llm"),
        ("job_relevance", 25, "llm"),
        ("followup_response", 10, "llm"),
    ),
    "presentation": (
        ("continuity", 10, "signal"),
        ("pacing", 10, "signal"),
        ("conclusion_structure", 25, "llm"),
        ("evidence_quality", 25, "llm"),
        ("action_risk", 15, "llm"),
        ("qa_response", 15, "llm"),
    ),
    "speech": (
        ("continuity", 15, "signal"),
        ("pacing", 10, "signal"),
        ("topic_alignment", 25, "llm"),
        ("speech_structure", 30, "llm"),
        ("rhetorical_design", 20, "llm"),
    ),
}

CRITICAL_AXES = {
    "interview": "job_relevance",
    "presentation": "conclusion_structure",
    "speech": "topic_alignment",
}

SEMANTIC_SCORES = {
    "interview": {
        "response_structure": 78,
        "evidence_results": 79,
        "job_relevance": 80,
        "followup_response": 81,
    },
    "presentation": {
        "conclusion_structure": 82,
        "evidence_quality": 83,
        "action_risk": 84,
        "qa_response": 85,
    },
    "speech": {
        "topic_alignment": 86,
        "speech_structure": 87,
        "rhetorical_design": 88,
    },
}

SHARED_TRANSCRIPT = (
    "今天先说明目标，再介绍执行过程与关键发现，最后总结结果、风险和下一步行动。"
    "团队已经完成基础验证，并记录了必要的数据与反馈。"
)
SHARED_SPEECH_DURATION = 20.0


def _axis_scores(report: dict, source: str) -> dict[str, float]:
    return {
        axis["key"]: axis["score"]
        for axis in report["axes"]
        if axis["source"] == source
    }


async def _build_report(db_session, scenario: str) -> dict:
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario=scenario, position="统一验收主题"),
    )
    await interview.start_interview(db_session, session.id)
    await interview.save_user_message(
        db_session,
        session.id,
        SHARED_TRANSCRIPT,
        {"speech_duration_sec": SHARED_SPEECH_DURATION},
    )
    return await report_generator.generate_report(db_session, session.id)


async def _fake_pack_evaluation(
    db,
    row,
    dialogues,
    scenario_pack,
    signal_evidence,
) -> dict:
    del db, row, dialogues
    assert "语速" in signal_evidence
    result = {
        "summary": "跨场景验收总评",
        "suggestions": {"short_term": [], "mid_term": []},
        "professional_advice": [],
    }
    for key, score in SEMANTIC_SCORES[scenario_pack.key].items():
        result[key] = {
            "score": score,
            "feedback": "按当前场景评分锚点评价。",
            "evidence": [SHARED_TRANSCRIPT],
        }
    return result


def test_v4_profiles_match_the_approved_axes_and_shared_signal_contract() -> None:
    semantic_sets: list[frozenset[str]] = []

    for scenario, approved in APPROVED_AXES.items():
        profile = get_pack(scenario).evaluation
        actual = tuple((axis.key, axis.weight, axis.source) for axis in profile.axes)

        assert profile.version == f"{scenario}-v4"
        assert actual == approved
        assert sum(axis.weight for axis in profile.axes) == 100

        signal_axes = tuple(
            (axis.key, axis.signal_key)
            for axis in profile.axes
            if axis.source == "signal"
        )
        assert signal_axes == (("continuity", "continuity"), ("pacing", "pacing"))

        llm_axes = [axis for axis in profile.axes if axis.source == "llm"]
        assert all([anchor.score for anchor in axis.anchors] == [90, 75, 60, 40] for axis in llm_axes)
        assert all(axis.min_evidence >= 1 for axis in llm_axes)
        semantic_sets.append(frozenset(axis.key for axis in llm_axes))

    assert len(set(semantic_sets)) == len(APPROVED_AXES)


@pytest.mark.parametrize(
    ("scenario", "critical_axis"),
    tuple(CRITICAL_AXES.items()),
)
def test_key_task_gate_is_declared_by_each_profile(
    scenario: str,
    critical_axis: str,
) -> None:
    """关键任务来自配置本身，报告管线无需根据场景名称推导。"""
    gates = get_pack(scenario).evaluation.score_gates

    assert len(gates) == 1
    assert gates[0].axis_key == critical_axis
    assert gates[0].below == 40
    assert gates[0].max_overall == 59


async def test_report_applies_declared_gate_after_pack_key_is_renamed(
    db_session,
    monkeypatch,
) -> None:
    """约束跟随评价配置；换掉场景名称也不影响触发行为。"""
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="speech", position="统一验收主题"),
    )
    await interview.start_interview(db_session, session.id)
    await interview.save_user_message(
        db_session,
        session.id,
        SHARED_TRANSCRIPT,
        {"speech_duration_sec": SHARED_SPEECH_DURATION},
    )

    renamed_pack = replace(get_pack("speech"), key="renamed_acceptance_pack")
    critical_axis = renamed_pack.evaluation.score_gates[0].axis_key

    async def fake_low_critical_axis(
        db,
        row,
        dialogues,
        scenario_pack,
        signal_evidence,
    ) -> dict:
        del db, row, dialogues, signal_evidence
        assert scenario_pack.key == "renamed_acceptance_pack"
        result = {
            "summary": "声明式约束验收",
            "suggestions": {"short_term": [], "mid_term": []},
            "professional_advice": [],
        }
        for axis in scenario_pack.evaluation.axes:
            if axis.source == "llm":
                result[axis.key] = {
                    "score": 39 if axis.key == critical_axis else 95,
                    "feedback": "由测试替身直接给分。",
                    "evidence": [SHARED_TRANSCRIPT],
                }
        return result

    monkeypatch.setattr(report_generator, "get_pack", lambda scenario: renamed_pack)
    monkeypatch.setattr(
        report_generator,
        "_llm_evaluate",
        fake_low_critical_axis,
    )
    report = await report_generator.generate_report(db_session, session.id)

    assert report["scenario"] == "renamed_acceptance_pack"
    assert report["overall_score"] == 59.0
    assert report["score_constraints"][0]["axis_key"] == critical_axis


@pytest.mark.parametrize(
    ("left_scenario", "right_scenario"),
    (("speech", "presentation"), ("speech", "interview")),
)
async def test_same_measurements_keep_shared_scores_but_use_pack_semantics(
    db_session,
    monkeypatch,
    left_scenario: str,
    right_scenario: str,
) -> None:
    monkeypatch.setattr(
        report_generator,
        "_llm_evaluate",
        _fake_pack_evaluation,
    )

    left = await _build_report(db_session, left_scenario)
    right = await _build_report(db_session, right_scenario)

    assert _axis_scores(left, "signal") == _axis_scores(right, "signal")
    assert _axis_scores(left, "llm") == SEMANTIC_SCORES[left_scenario]
    assert _axis_scores(right, "llm") == SEMANTIC_SCORES[right_scenario]

    for scenario, report in (
        (left_scenario, left),
        (right_scenario, right),
    ):
        pack = get_pack(scenario)
        assert report["rubric_version"] == f"{scenario}-v4"
        assert [axis["key"] for axis in report["axes"]] == [
            axis.key for axis in pack.evaluation.axes
        ]
        assert set(_axis_scores(report, "llm")) == set(SEMANTIC_SCORES[scenario])
        assert report["score_coverage"] == 1.0


@pytest.mark.parametrize(("scenario", "critical_axis"), tuple(CRITICAL_AXES.items()))
@pytest.mark.parametrize(("critical_score", "should_cap"), ((39, True), (40, False)))
async def test_declared_gate_caps_below_40_but_not_at_boundary(
    db_session,
    monkeypatch,
    scenario: str,
    critical_axis: str,
    critical_score: int,
    should_cap: bool,
) -> None:
    """gate 只响应模型返回的轴分数，不用样本文本或场景名称触发。"""

    async def fake_extreme_evaluation(
        db,
        row,
        dialogues,
        scenario_pack,
        signal_evidence,
    ) -> dict:
        del db, row, dialogues, signal_evidence
        result = {
            "summary": "关键任务边界验收",
            "suggestions": {"short_term": [], "mid_term": []},
            "professional_advice": [],
        }
        for axis in scenario_pack.evaluation.axes:
            if axis.source == "llm":
                result[axis.key] = {
                    "score": critical_score if axis.key == critical_axis else 95,
                    "feedback": "边界分数由测试替身直接提供。",
                    "evidence": [SHARED_TRANSCRIPT],
                }
        return result

    monkeypatch.setattr(
        report_generator,
        "_llm_evaluate",
        fake_extreme_evaluation,
    )
    report = await _build_report(db_session, scenario)
    pack = get_pack(scenario)
    raw_scores = {axis["key"]: axis["score"] for axis in report["axes"]}
    weights = {axis.key: axis.weight for axis in pack.evaluation.axes}
    uncapped = compose_total(raw_scores, weights)

    assert uncapped is not None and uncapped > 59
    if should_cap:
        gate = pack.evaluation.score_gates[0]
        assert report["overall_score"] == 59.0
        assert report["score_constraints"] == [
            {
                "axis_key": critical_axis,
                "axis_score": 39.0,
                "below": 40.0,
                "max_overall": 59.0,
                "reason": gate.reason,
            }
        ]
    else:
        assert report["overall_score"] == uncapped
        assert report["score_constraints"] == []
