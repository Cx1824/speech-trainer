"""语音训练结束时的字幕持久化与竞态回归测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.api.v1 import interview as interview_api
from app.api.v1 import voice_ws as voice_ws_api
from app.modules import interview
from app.modules.interview import planner
from app.modules.report import generator as report_generator
from app.modules.scenarios import get_pack
from app.schemas import InterviewConfigIn


SCENARIOS = ("interview", "presentation", "speech")


class _WebSocketStub:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_text(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


async def _started_session(db_session, scenario: str):
    created = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario=scenario, position="数据完整性测试"),
    )
    await interview.start_interview(db_session, created.id)
    return created


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_end_flushes_final_and_partial_before_report(
    db_session,
    monkeypatch,
    scenario: str,
) -> None:
    """三个场景结束时都先保存定稿句和最后一条增量字幕，再生成报告。"""
    created = await _started_session(db_session, scenario)
    socket = _WebSocketStub()
    finals = ["先说结论，本阶段已经完成。"]
    partial = ["下一步会补充可核对的数据"]
    analyses: list[dict] = []
    last_committed = [""]

    await voice_ws_api._handle_json(
        socket,
        db_session,
        created.id,
        json.dumps({"type": "end_interview"}),
        finals,
        analyses,
        latest_partial_holder=partial,
        last_committed_holder=last_committed,
    )

    dialogues = await interview.list_dialogues(db_session, created.id)
    assert [item["text"] for item in dialogues] == [
        "先说结论，本阶段已经完成。下一步会补充可核对的数据"
    ]
    assert (await interview.get_session(db_session, created.id)).status == "completed"
    assert socket.messages[-1]["type"] == "interview_completed"

    async def fake_evaluate(db, row, rows, pack, signal_evidence):
        assert [item.text for item in rows if item.role == "user"] == [dialogues[0]["text"]]
        result = {
            "summary": "结束前字幕已完整进入报告。",
            "suggestions": {"short_term": [], "mid_term": []},
            "professional_advice": [],
        }
        for axis in pack.evaluation.axes:
            if axis.source == "llm":
                result[axis.key] = {
                    "score": 80,
                    "feedback": "有完整训练文本。",
                    "evidence": [dialogues[0]["text"]],
                }
        return result

    monkeypatch.setattr(report_generator, "_llm_evaluate", fake_evaluate)
    report = await report_generator.generate_report(db_session, created.id)
    assert len(report["dialogues"]) == 1
    # 没有真实 PCM 时语速轴按事实保持缺失，但绝不能再退化为空报告的 0 覆盖率。
    assert report["score_coverage"] > 0


async def test_rest_end_waits_for_websocket_flush(db_session) -> None:
    """REST /end 不得抢在活动 WebSocket 的字幕落库之前完成。"""
    created = await _started_session(db_session, "presentation")
    socket = _WebSocketStub()
    flush_event = voice_ws_api._register_voice_flush(created.id)

    rest_end = asyncio.create_task(interview_api.end(created.id, db_session))
    await asyncio.sleep(0)
    assert not rest_end.done()

    await voice_ws_api._handle_json(
        socket,
        db_session,
        created.id,
        json.dumps({
            "type": "end_interview",
            "payload": {"text": "这是浏览器结束时携带的最后一段字幕。"},
        }),
        [],
        [],
        latest_partial_holder=[""],
        last_committed_holder=[""],
        voice_flush_event=flush_event,
    )
    ended = await rest_end

    assert ended.status == "completed"
    dialogues = await interview.list_dialogues(db_session, created.id)
    assert [item["text"] for item in dialogues] == ["这是浏览器结束时携带的最后一段字幕。"]


async def test_disconnect_flushes_latest_partial_without_completing_session(db_session) -> None:
    """连接意外断开时保存最后 partial，但保留会话以便恢复。"""
    created = await _started_session(db_session, "speech")

    saved = await voice_ws_api._flush_pending_answer(
        _WebSocketStub(),
        db_session,
        created.id,
        ["已经定稿。"],
        [],
        ["还没来得及定稿"],
        [""],
    )

    assert saved is True
    dialogues = await interview.list_dialogues(db_session, created.id)
    assert [item["text"] for item in dialogues] == ["已经定稿。还没来得及定稿"]
    assert (await interview.get_session(db_session, created.id)).status == "in_progress"


async def test_flush_prefers_refined_backend_text_over_stale_client_text(db_session) -> None:
    created = await _started_session(db_session, "speech")

    await voice_ws_api._flush_pending_answer(
        _WebSocketStub(),
        db_session,
        created.id,
        ["这是句末精校后的最终字幕。"],
        [],
        [""],
        [""],
        explicit_text="这是浏览器提交前看到的流式字幕。",
    )

    dialogues = await interview.list_dialogues(db_session, created.id)
    assert [item["text"] for item in dialogues] == ["这是句末精校后的最终字幕。"]


async def test_commit_detaches_current_turn_before_waiting_for_next_question(
    db_session,
    monkeypatch,
) -> None:
    """生成追问很慢时，已提交回答不能继续占用下一轮字幕缓冲。"""
    created = await _started_session(db_session, "interview")
    finals = ["这是已经定稿的回答。"]
    analyses = [{"sentence": "这是已经定稿的回答。"}]
    partial = ["最后半句"]
    last_committed = [""]

    async def fake_commit(*args, sentence_analyses=None, **kwargs) -> None:
        assert finals == []
        assert analyses == []
        assert partial == [""]
        assert sentence_analyses == [{"sentence": "这是已经定稿的回答。"}]

    monkeypatch.setattr(voice_ws_api, "_commit_answer", fake_commit)
    await voice_ws_api._handle_json(
        _WebSocketStub(),
        db_session,
        created.id,
        json.dumps({
            "type": "commit_answer",
            "payload": {"text": "浏览器中的旧字幕"},
        }),
        finals,
        analyses,
        latest_partial_holder=partial,
        last_committed_holder=last_committed,
    )

    assert last_committed == ["这是已经定稿的回答。最后半句"]


async def test_early_end_does_not_duplicate_last_committed_answer(db_session) -> None:
    """提交回答后立刻结束，前端残留的同一字幕不会被保存两次。"""
    created = await _started_session(db_session, "interview")
    last_committed = [""]
    text = "这是已经提交的一轮回答。"

    assert await voice_ws_api._flush_pending_answer(
        _WebSocketStub(), db_session, created.id, [text], [], [""], last_committed,
    )
    assert not await voice_ws_api._flush_pending_answer(
        _WebSocketStub(),
        db_session,
        created.id,
        [],
        [],
        [""],
        last_committed,
        explicit_text=text,
    )

    dialogues = await interview.list_dialogues(db_session, created.id)
    assert [item["text"] for item in dialogues] == [text]


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_finish_stage_flushes_partial_for_every_scenario(
    db_session,
    monkeypatch,
    scenario: str,
) -> None:
    """阶段结束与整场结束使用同一冲刷逻辑，partial 不因清屏而丢失。"""
    created = await _started_session(db_session, scenario)

    async def skip_next_question(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(voice_ws_api, "_auto_next_question", skip_next_question)
    await voice_ws_api._finish_stage(
        _WebSocketStub(),
        db_session,
        created.id,
        [],
        [],
        ["阶段结束前的最后半句"],
        [""],
    )

    dialogues = await interview.list_dialogues(db_session, created.id)
    assert [item["text"] for item in dialogues] == ["阶段结束前的最后半句"]
    expected_stage = (
        planner.build_plan("full", "standard")["items"][1]["stage"]
        if scenario == "interview"
        else get_pack(scenario).stages[1].key
    )
    assert (await interview.get_session(db_session, created.id)).current_stage == expected_stage
