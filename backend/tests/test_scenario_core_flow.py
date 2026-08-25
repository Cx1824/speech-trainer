"""三个场景共享核心链路的参数化回归测试。"""

from __future__ import annotations

import pytest

from app.modules import interview
from app.modules.report import generator as report_generator
from app.modules.scenarios import get_pack
from app.schemas import InterviewConfigIn


SCENARIOS = ("interview", "presentation", "speech")


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_scenario_create_start_generate_and_advance(db_session, scenario: str) -> None:
    """三个场景通过同一管理器完成创建、开始、发言、保存与推进。"""
    pack = get_pack(scenario)
    config = InterviewConfigIn(
        scenario=scenario,
        position="测试主题",
        duration_limit=5 if pack.timed else 0,
    )

    created = await interview.create_session(db_session, config)
    assert created.scenario == scenario
    assert created.status == "configuring"

    started = await interview.start_interview(db_session, created.id)
    assert started.status == "in_progress"
    expected_first_stage = "self_intro" if scenario == "interview" else pack.stages[0].key
    assert started.current_stage == expected_first_stage

    async def fake_llm(messages: list[dict]) -> str:
        assert messages[0]["role"] == "system"
        return f"{pack.role_name}测试发言"

    generated = await interview.generate_next(db_session, created.id, fake_llm)
    assert generated == f"{pack.role_name}测试发言"

    await interview.save_user_message(
        db_session,
        created.id,
        "这是一次用于验证共享核心的回答。",
        {"speech_duration_sec": 4.0},
    )
    assert await interview.should_advance(db_session, created.id) is True
    dialogues = await interview.list_dialogues(db_session, created.id)
    assert [dialogue["role"] for dialogue in dialogues] == ["ai", "user"]

    advanced = await interview.advance_stage(db_session, created.id)
    expected_next_stage = "hr_screen" if scenario == "interview" else pack.stages[1].key
    assert advanced.current_stage == expected_next_stage


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_scenario_report_uses_own_evaluation_profile(
    db_session,
    monkeypatch,
    scenario: str,
) -> None:
    """共享事实保持一致，报告语义轴来自各自场景评价配置。"""
    pack = get_pack(scenario)
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario=scenario, position="测试主题"),
    )
    await interview.start_interview(db_session, session.id)
    await interview.save_user_message(
        db_session,
        session.id,
        "先说结论，本次工作完成了目标，并有明确的数据作为依据。",
        {
            "speech_duration_sec": 8.0,
            "voice_signal": True,
            "calibrated": True,
            "pitch_jitter": 0.05,
            "tension_score": 20.0,
            "confidence_score": 80.0,
        },
    )

    async def fake_evaluate(db, row, dialogues, scenario_pack, signal_evidence):
        assert scenario_pack.key == scenario
        assert "语速" in signal_evidence
        result = {
            "summary": "测试总评",
            "suggestions": {"short_term": [], "mid_term": []},
            "professional_advice": [],
        }
        for axis in scenario_pack.evaluation.axes:
            if axis.source == "llm":
                result[axis.key] = {
                    "score": 80,
                    "feedback": axis.description,
                    "evidence": ["训练记录中的原话"],
                }
        return result

    monkeypatch.setattr(report_generator, "_llm_evaluate", fake_evaluate)
    report = await report_generator.generate_report(db_session, session.id)

    expected_keys = [axis.key for axis in pack.evaluation.axes]
    assert [axis["key"] for axis in report["axes"]] == expected_keys
    assert report["rubric_version"] == pack.evaluation.version
    assert report["sample_state"] == "voice_calibrated"
    assert report["score_coverage"] == 1.0
    assert report["overall_score"] is not None


async def test_report_does_not_turn_partial_axes_into_overall_score(
    db_session,
    monkeypatch,
) -> None:
    """语义评价失败时保留客观轴，但不得把局部分数冒充综合分。"""
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="speech", position="完全无关的测试主题"),
    )
    await interview.start_interview(db_session, session.id)
    await interview.save_user_message(
        db_session,
        session.id,
        "这是一段连贯但与测试主题完全无关的发言。",
        {"speech_duration_sec": 8.0},
    )

    async def unavailable_semantic_evaluation(*args, **kwargs):
        return {
            "summary": (
                "语义评价暂未完成，请稍后重试生成报告；"
                "下方客观表达数据仍可参考。"
            )
        }

    monkeypatch.setattr(
        report_generator,
        "_llm_evaluate",
        unavailable_semantic_evaluation,
    )
    report = await report_generator.generate_report(db_session, session.id)

    signal_weight = sum(
        axis.weight for axis in get_pack("speech").evaluation.axes
        if axis.source == "signal"
    )
    assert report["score_coverage"] == signal_weight / 100
    assert report["overall_score"] is None
    assert all(
        axis["score"] is not None
        for axis in report["axes"]
        if axis["source"] == "signal"
    )
    assert all(
        axis["score"] is None
        for axis in report["axes"]
        if axis["source"] == "llm"
    )
    assert "语义评价暂未完成" in report["summary"]


async def test_report_explains_non_compensatory_score_gate(
    db_session,
    monkeypatch,
) -> None:
    """关键任务低分不能被其他高分轴补偿，且报告必须返回可解释原因。"""
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="speech", position="人工智能与教育"),
    )
    await interview.start_interview(db_session, session.id)
    await interview.save_user_message(
        db_session,
        session.id,
        "这段发言非常流畅，但内容只讨论今天的午餐，与指定主题无关。",
        {"speech_duration_sec": 8.0},
    )

    async def fake_evaluate(db, row, dialogues, scenario_pack, signal_evidence):
        result = {
            "summary": "表达流畅，但没有完成主题任务。",
            "suggestions": {"short_term": [], "mid_term": []},
            "professional_advice": [],
        }
        for axis in scenario_pack.evaluation.axes:
            if axis.source == "llm":
                result[axis.key] = {
                    "score": 39 if axis.key == "topic_alignment" else 95,
                    "feedback": axis.description,
                    "evidence": ["内容只讨论今天的午餐"],
                }
        return result

    monkeypatch.setattr(report_generator, "_llm_evaluate", fake_evaluate)
    report = await report_generator.generate_report(db_session, session.id)

    assert report["score_coverage"] == 1.0
    assert report["overall_score"] == 59.0
    assert report["score_constraints"] == [
        {
            "axis_key": "topic_alignment",
            "axis_score": 39.0,
            "below": 40.0,
            "max_overall": 59.0,
            "reason": get_pack("speech").evaluation.score_gates[0].reason,
        }
    ]


def test_three_scenarios_have_distinct_semantic_report_axes() -> None:
    semantic_axes = {
        scenario: tuple(
            axis.key
            for axis in get_pack(scenario).evaluation.axes
            if axis.source == "llm"
        )
        for scenario in SCENARIOS
    }
    assert len(set(semantic_axes.values())) == len(SCENARIOS)


async def test_report_is_cached_until_explicit_regeneration(
    db_session,
    monkeypatch,
) -> None:
    """刷新页面只能读到同一快照，显式重评才创建新版本。"""
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="speech", position="缓存测试"),
    )
    await interview.start_interview(db_session, session.id)
    await interview.save_user_message(
        db_session,
        session.id,
        "先说结论，再说明依据，最后回扣主题。",
        {"speech_duration_sec": 6.0},
    )

    calls = 0

    async def fake_evaluate(db, row, dialogues, scenario_pack, signal_evidence):
        nonlocal calls
        calls += 1
        semantic_score = 60 + calls * 10
        result = {
            "summary": f"第 {calls} 次评估",
            "suggestions": {"short_term": [], "mid_term": []},
            "professional_advice": [],
        }
        for axis in scenario_pack.evaluation.axes:
            if axis.source == "llm":
                result[axis.key] = {
                    "score": semantic_score,
                    "feedback": axis.description,
                    "evidence": ["先说结论"],
                }
        return result

    monkeypatch.setattr(report_generator, "_llm_evaluate", fake_evaluate)

    first = await report_generator.generate_report(db_session, session.id)
    cached = await report_generator.generate_report(db_session, session.id)
    loaded = await report_generator.get_report(db_session, session.id)

    assert calls == 1
    assert first == cached == loaded
    assert first["report_version"] == 1

    regenerated = await report_generator.generate_report(
        db_session,
        session.id,
        regenerate=True,
    )
    latest = await report_generator.get_report(db_session, session.id)

    assert calls == 2
    assert regenerated == latest
    assert regenerated["report_version"] == 2
    assert regenerated["overall_score"] != first["overall_score"]
