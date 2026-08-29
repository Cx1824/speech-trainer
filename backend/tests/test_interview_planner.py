"""结构化面试计划与换题边界回归测试。"""

from __future__ import annotations

import pytest

from app.modules import interview
from app.modules import config as config_module
from app.modules.interview import manager, planner
from app.modules.question_bank import save_questions
from app.modules.question_bank.manager import QuestionBankRow  # noqa: F401
from app.core.exceptions import ProviderError
from app.schemas import InterviewConfigIn, ProviderConfigIn


def _concrete_answer() -> str:
    return (
        "我负责主导这个项目的目标拆解和跨部门协调，先验证关键风险，"
        "再推动三个阶段落地，最终按期完成并将处理时间降低了百分之二十。"
    )


def test_mode_catalog_contains_six_clear_training_paths() -> None:
    catalog = planner.list_modes()

    assert [mode["key"] for mode in catalog["modes"]] == [
        "full", "hr", "professional", "project", "behavioral", "weakness",
    ]
    full = catalog["modes"][0]
    assert full["estimates"]["standard"] == {"min": 25, "max": 35}
    assert full["recommended"] is True
    assert [item["key"] for item in catalog["intensities"]] == [
        "quick", "standard", "deep",
    ]


@pytest.mark.parametrize(
    ("mode", "expected_stages"),
    [
        ("full", {"self_intro", "hr_screen", "project", "professional", "behavioral", "qa"}),
        ("hr", {"self_intro", "hr_screen", "behavioral", "qa"}),
        ("professional", {"self_intro", "professional", "project", "qa"}),
        ("project", {"self_intro", "project", "qa"}),
        ("behavioral", {"self_intro", "behavioral", "qa"}),
        ("weakness", {"self_intro", "weakness", "qa"}),
    ],
)
def test_each_mode_has_a_bounded_multi_dimension_plan(
    mode: str,
    expected_stages: set[str],
) -> None:
    plan = planner.build_plan(mode, "standard")

    assert plan["items"][0]["id"] == "self_intro"
    assert {item["stage"] for item in plan["items"]} == expected_stages
    assert all(int(item["followup_limit"]) <= 1 for item in plan["items"])


def test_hr_mode_contains_real_basic_screening_instead_of_trick_questions() -> None:
    plan = planner.build_plan("hr", "standard")
    foundation = next(item for item in plan["items"] if item["id"] == "hr_foundation")

    assert foundation["label"] == "基础筛选"
    assert "公司" in foundation["goal"]
    assert "不出脑筋急转弯" in foundation["goal"]


def test_vague_answer_gets_at_most_one_followup_then_switches() -> None:
    plan = planner.build_plan("full", "deep")
    planner.advance(plan)
    item = planner.current_item(plan)
    assert item is not None

    planner.record_question(plan, item)
    assert planner.should_advance(plan, "我做过产品工作。") is False

    planner.record_question(plan, item)
    assert planner.should_advance(plan, "我还负责过一些需求。") is True
    assert plan["state"]["followups_used"] == 1


@pytest.mark.parametrize("answer", ["没有精确数据", "这个数字我不清楚", "暂时没有相关经历"])
def test_clear_data_boundary_moves_on_instead_of_repeated_probing(answer: str) -> None:
    plan = planner.build_plan("project", "standard")
    planner.advance(plan)
    current = planner.current_item(plan)
    assert current is not None and current["evidence_required"] is True
    planner.record_question(plan, current)

    assert planner.should_advance(plan, answer) is True


def test_skipped_direction_is_not_counted_as_covered() -> None:
    plan = planner.build_plan("hr", "standard")
    first = planner.current_item(plan)
    assert first is not None

    planner.advance(plan, mark_covered=False)
    status = planner.progress(plan)

    assert first["label"] in status["skipped_labels"]
    assert first["label"] not in status["covered_labels"]


def test_progress_exposes_only_current_answer_goal_for_ui() -> None:
    plan = planner.build_plan("full", "standard")
    current = planner.current_item(plan)
    assert current is not None

    status = planner.progress(plan)
    assert status["current_goal"] == current["goal"][:120]
    assert "id" not in status
    assert "intent" not in status


async def test_full_flow_covers_all_dimensions_before_auto_completion(db_session) -> None:
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(
            scenario="interview",
            position="产品经理",
            interview_mode="full",
            interview_intensity="standard",
        ),
    )
    current = await interview.start_interview(db_session, session.id)
    seen_stages: list[str] = []

    async def fake_llm(messages: list[dict]) -> str:
        assert "本轮能力维度" in messages[-1]["content"]
        return "请结合一段真实经历说明你的做法和结果？"

    while current.status == "in_progress":
        seen_stages.append(current.current_stage)
        await interview.generate_next(db_session, session.id, fake_llm)
        await interview.save_user_message(db_session, session.id, _concrete_answer())
        assert await interview.should_advance(db_session, session.id) is True
        current = await interview.advance_stage(db_session, session.id)

    assert current.status == "completed"
    assert {"hr_screen", "project", "professional", "behavioral", "qa"}.issubset(seen_stages)
    assert current.interview_progress is not None
    assert current.interview_progress["covered"] == current.interview_progress["total"]


async def test_question_bank_is_used_only_for_matching_main_dimension(db_session) -> None:
    await save_questions(
        db_session,
        "产品经理",
        [{"content": "你如何判断自己真的理解这个岗位？", "intent": "hr_foundation"}],
    )
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(
            scenario="interview",
            position="产品经理",
            interview_mode="hr",
            interview_intensity="standard",
        ),
    )
    await interview.start_interview(db_session, session.id)
    prompts: list[str] = []

    async def fake_llm(messages: list[dict]) -> str:
        prompts.append(messages[-1]["content"])
        return "测试问题"

    await interview.generate_next(db_session, session.id, fake_llm)
    assert "岗位题库候选" not in prompts[-1]
    await interview.save_user_message(db_session, session.id, _concrete_answer())
    await interview.advance_stage(db_session, session.id)
    await interview.generate_next(db_session, session.id, fake_llm)
    assert "岗位题库候选：你如何判断自己真的理解这个岗位？" in prompts[-1]


async def test_live_question_call_uses_latency_bounds_for_deepseek(
    db_session,
    monkeypatch,
) -> None:
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="interview", position="产品经理"),
    )
    await interview.start_interview(db_session, session.id)
    options: dict = {}

    class RecordingProvider:
        async def chat(self, messages, **kwargs):
            assert messages
            options.update(kwargs)
            return "请先做一个简短的自我介绍。"

    async def fake_load_config(db, kind):
        assert kind == "llm"
        return ProviderConfigIn(provider="deepseek", api_key="test-key")

    monkeypatch.setattr(config_module, "load_provider_config", fake_load_config)
    monkeypatch.setattr(manager, "get_llm", lambda _config: RecordingProvider())

    await interview.generate_next(db_session, session.id)

    assert options == {
        "temperature": 0.7,
        "max_tokens": manager.QUESTION_MAX_TOKENS,
        "read_timeout": manager.QUESTION_READ_TIMEOUT_SECONDS,
        "thinking": False,
    }


async def test_live_question_falls_back_without_guessing_user_experience(
    db_session,
    monkeypatch,
) -> None:
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="interview", position="产品经理"),
    )
    await interview.start_interview(db_session, session.id)

    class FailingProvider:
        async def chat(self, messages, **kwargs):
            raise ProviderError("测试超时")

    async def fake_load_config(db, kind):
        return ProviderConfigIn(provider="deepseek")

    monkeypatch.setattr(config_module, "load_provider_config", fake_load_config)
    monkeypatch.setattr(manager, "get_llm", lambda _config: FailingProvider())

    question = await interview.generate_next(db_session, session.id)

    assert "自我介绍" in question
    assert "具体回答" in question
