from __future__ import annotations

import json

import pytest

from app.core.exceptions import ProviderError
from app.providers.asr.dashscope_realtime import RealtimeASRSession


class _FakeWebSocket:
    def __init__(self, ack: dict) -> None:
        self.ack = ack
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, value: str | bytes) -> None:
        self.sent.append(value)

    async def recv(self) -> str:
        return json.dumps(self.ack)

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        async def _empty():
            if False:
                yield ""

        return _empty()


class _StreamingFakeWebSocket(_FakeWebSocket):
    def __init__(self, events: list[dict]) -> None:
        super().__init__({"header": {"event": "task-started"}})
        self.events = events

    def __aiter__(self):
        async def _events():
            for event in self.events:
                yield json.dumps(event)

        return _events()


@pytest.mark.asyncio
async def test_realtime_asr_retries_transient_handshake_once(monkeypatch) -> None:
    socket = _FakeWebSocket({"header": {"event": "task-started"}})
    attempts = 0

    async def fake_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("handshake timeout")
        return socket

    monkeypatch.setattr(
        "app.providers.asr.dashscope_realtime.websockets.connect", fake_connect
    )
    monkeypatch.setattr(
        "app.providers.asr.dashscope_realtime.asyncio.sleep",
        lambda _seconds: _completed(),
    )

    session = RealtimeASRSession("test-key")
    await session.start()
    await session.close()

    assert attempts == 2
    assert socket.sent


@pytest.mark.asyncio
async def test_realtime_asr_does_not_retry_provider_rejection(monkeypatch) -> None:
    socket = _FakeWebSocket({
        "header": {"event": "task-failed", "error_message": "invalid model"}
    })
    attempts = 0

    async def fake_connect(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return socket

    monkeypatch.setattr(
        "app.providers.asr.dashscope_realtime.websockets.connect", fake_connect
    )

    with pytest.raises(ProviderError, match="invalid model"):
        await RealtimeASRSession("test-key").start()

    assert attempts == 1
    assert socket.closed is True


@pytest.mark.asyncio
async def test_callback_failure_does_not_stop_receiving() -> None:
    socket = _StreamingFakeWebSocket([
        {
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {"text": "半句", "sentence_end": False}}},
        },
        {
            "header": {"event": "result-generated"},
            "payload": {"output": {"sentence": {"text": "完整句。", "sentence_end": True}}},
        },
    ])
    finals: list[str] = []
    session = RealtimeASRSession("test-key")
    session._ws = socket
    session.on_partial = lambda _text: (_ for _ in ()).throw(RuntimeError("UI closed"))
    session.on_final = finals.append

    await session._recv_loop()

    assert finals == ["完整句。"]


async def _completed() -> None:
    return None
