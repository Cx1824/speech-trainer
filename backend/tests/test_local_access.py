"""本地 HTTP/WebSocket 访问边界测试。"""

from __future__ import annotations

import httpx

from app.core.local_access import accept_trusted_websocket, is_allowed_origin
from app.main import app


class _WebSocket:
    def __init__(self, origin: str | None) -> None:
        self.headers = {} if origin is None else {"origin": origin}
        self.accepted = False
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


def test_origin_requires_exact_allowlist_match() -> None:
    allowed = ["http://localhost:5178", "http://127.0.0.1:5178"]
    assert is_allowed_origin("http://localhost:5178", allowed) is True
    assert is_allowed_origin("http://localhost:5178/", allowed) is True
    assert is_allowed_origin("https://malicious.example", allowed) is False
    assert is_allowed_origin("http://localhost:5178.evil.example", allowed) is False


def test_non_browser_client_without_origin_is_allowed() -> None:
    assert is_allowed_origin(None, ["http://localhost:5178"]) is True


async def test_trusted_websocket_is_accepted() -> None:
    websocket = _WebSocket("http://localhost:5178")
    assert await accept_trusted_websocket(websocket) is True
    assert websocket.accepted is True
    assert websocket.closed is None


async def test_untrusted_websocket_is_closed_before_accept() -> None:
    websocket = _WebSocket("https://malicious.example")
    assert await accept_trusted_websocket(websocket) is False
    assert websocket.accepted is False
    assert websocket.closed == (1008, "WebSocket origin not allowed")


async def test_http_host_is_limited_to_local_names() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
    ) as local_client:
        assert (await local_client.get("/api/health")).status_code == 200

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://malicious.example",
    ) as untrusted_client:
        assert (await untrusted_client.get("/api/health")).status_code == 400
