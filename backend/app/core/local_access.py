"""本地优先部署的 HTTP/WebSocket 访问边界。"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from fastapi import WebSocket

from app.config import get_settings

logger = logging.getLogger(__name__)


def is_allowed_origin(origin: str | None, allowed_origins: Iterable[str]) -> bool:
    """判断浏览器 Origin 是否在显式允许列表中。

    非浏览器客户端通常不发送 Origin；在服务默认仅监听回环地址的前提下允许
    这类连接。浏览器一旦携带 Origin，则必须精确匹配配置。
    """
    if origin is None:
        return True
    normalized = origin.rstrip("/")
    allowed = {item.rstrip("/") for item in allowed_origins if item}
    return normalized in allowed


async def accept_trusted_websocket(websocket: WebSocket) -> bool:
    """仅接受来自已配置本地前端的浏览器 WebSocket。"""
    origin = websocket.headers.get("origin")
    allowed_origins = get_settings().cors_origin_list
    if not is_allowed_origin(origin, allowed_origins):
        logger.warning("拒绝非受信 WebSocket 来源：%s", origin)
        await websocket.close(code=1008, reason="WebSocket origin not allowed")
        return False
    await websocket.accept()
    return True
