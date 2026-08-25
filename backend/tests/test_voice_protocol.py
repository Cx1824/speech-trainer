"""面向浮层/语音状态机的 WebSocket 协议回归测试。"""

from __future__ import annotations

import json

import pytest

from app.api.v1 import voice_ws
from app.modules.interview.ws_protocol import ServerMsgType, envelope


def test_ai_question_delivery_is_explicit_and_user_facing() -> None:
    voice = json.loads(envelope(
        ServerMsgType.AI_QUESTION,
        stage="project",
        text="请介绍一个项目。",
        delivery="voice",
    ))
    assert voice["payload"]["delivery"] == "voice"
    assert "id" not in voice["payload"]

    text = json.loads(envelope(
        ServerMsgType.AI_QUESTION,
        stage="presenting",
        text="请开始你的汇报。",
        delivery="text",
    ))
    assert text["payload"]["delivery"] == "text"


def test_ai_audio_failure_keeps_question_text_and_allows_continue() -> None:
    message = json.loads(envelope(
        ServerMsgType.AI_AUDIO_UNAVAILABLE,
        text="请介绍一个项目。",
        reason="AI 语音暂时不可用",
        can_continue=True,
    ))
    assert message["type"] == "ai_audio_unavailable"
    assert message["payload"] == {
        "text": "请介绍一个项目。",
        "reason": "AI 语音暂时不可用",
        "can_continue": True,
    }


def test_timed_session_has_reminder_and_hard_stop_messages() -> None:
    reminder = json.loads(envelope(
        ServerMsgType.TIME_UP,
        limit_minutes=5,
        grace_minutes=10,
    ))
    assert reminder == {
        "type": "time_up",
        "payload": {"limit_minutes": 5, "grace_minutes": 10},
    }

    hard_stop = json.loads(envelope(
        ServerMsgType.HARD_TIME_UP,
        limit_minutes=5,
        overtime_seconds=600,
    ))
    assert hard_stop == {
        "type": "hard_time_up",
        "payload": {"limit_minutes": 5, "overtime_seconds": 600},
    }


@pytest.mark.asyncio
async def test_timed_deadlines_wait_for_grace_before_hard_stop() -> None:
    class SocketStub:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_text(self, raw: str) -> None:
            self.messages.append(json.loads(raw))

    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    socket = SocketStub()
    await voice_ws._send_timed_deadlines(socket, 5, sleep=fake_sleep)

    assert delays == [300, 600]
    assert [message["type"] for message in socket.messages] == [
        "time_up",
        "hard_time_up",
    ]
