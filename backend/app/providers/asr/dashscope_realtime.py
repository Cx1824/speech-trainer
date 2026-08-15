"""阿里云 DashScope Paraformer 实时 ASR Provider。

通过 DashScope WebSocket API 调用 paraformer-realtime-v2 模型做流式识别。

协议（DashScope 实时语音识别）：
1. wss://dashscope.aliyuncs.com/api-ws/v1/inference?model=paraformer-realtime-v2
   Header: Authorization: Bearer <key>
2. run-task 指令启动任务（stream=NLS 流式，task_id 随机 uuid）
3. 客户端持续发二进制音频帧（16k 16bit mono PCM，每帧 ~100ms）
4. 服务端持续返回 JSON：result.text（增量）、sentence.end（句子完成）
5. finish-task 结束

设计为「会话级」长连接：open_stream() 返回一个控制器，
run() 内部管理 WS 生命周期，调用方通过 push_audio() 喂音频、
事件回调收识别结果。适合挂在面试语音 WS 连接上。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Callable, Optional

import websockets

from app.core.exceptions import ProviderError
from app.providers.base import BaseASRProvider, registry
from app.schemas import ProviderConfigIn

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "paraformer-realtime-v2"
WS_BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
SAMPLE_RATE = 16000


class DashScopeRealtimeASR(BaseASRProvider):
    """阿里云 DashScope Paraformer 流式 ASR。"""

    name = "dashscope"

    @property
    def _api_key(self) -> str:
        return self.config.api_key or ""

    @property
    def _model(self) -> str:
        raw = self.config.base_url or ""
        # base_url 可存模型名，也可存完整 WS URL
        if raw.startswith(("wss://", "ws://")):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(raw).query)
            return qs.get("model", [DEFAULT_MODEL])[0]
        return raw or DEFAULT_MODEL

    async def health_check(self) -> bool:
        return bool(self._api_key)

    async def transcribe_stream(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[str]:
        """聚合转写（兼容旧接口）：收完音频一次性流式识别。"""
        chunks: list[bytes] = []
        async for chunk in audio_stream:
            chunks.append(chunk)
        if not chunks:
            yield ""
            return

        results: list[str] = []

        def on_partial(_t: str) -> None:
            pass

        def on_final(t: str) -> None:
            results.append(t)

        session = RealtimeASRSession(self._api_key, self._model)
        await session.start()
        try:
            for c in chunks:
                await session.push_audio(c)
            await session.finish()
        finally:
            await session.close()
        yield "".join(results)


class RealtimeASRSession:
    """一次流式识别会话（WS 长连接）。

    用法：
        s = RealtimeASRSession(api_key)
        s.on_partial = lambda t: ...   # 增量文本（一句内不断更新）
        s.on_final = lambda t: ...     # 句子完成
        await s.start()
        await s.push_audio(pcm_chunk)  # 16k/16bit/mono PCM
        await s.finish()               # 结束当前音频流（等待剩余结果）
        await s.close()
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise ProviderError("DashScope ASR 未配置 API Key")
        self.api_key = api_key
        self.model = model
        self.task_id = uuid.uuid4().hex
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._final_event = asyncio.Event()
        self._closed = False
        self._started = False
        # 回调（可被外部替换）
        self.on_partial: Callable[[str], None] = lambda t: None
        self.on_final: Callable[[str], None] = lambda t: None
        self.on_error: Callable[[str], None] = lambda t: None

    async def start(self) -> None:
        url = f"{WS_BASE}?model={self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            self._ws = await websockets.connect(url, additional_headers=headers, open_timeout=10)
        except Exception as e:
            raise ProviderError(f"ASR WebSocket 连接失败：{e}") from e

        await self._ws.send(json.dumps({
            "header": {
                "action": "run-task",
                "task_id": self.task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "parameters": {
                    "format": "pcm",
                    "sample_rate": SAMPLE_RATE,
                    "language_hints": ["zh"],
                    # VAD 断句：静音 800ms 切句（贴合面试对话节奏）
                    "max_sentence_silence": 800,
                    "punctuation_prediction_enabled": True,
                    # 心跳：持续静音时保持连接
                    "heartbeat": True,
                },
                "input": {},
            },
        }))

        ack = await asyncio.wait_for(self._ws.recv(), timeout=10)
        evt = json.loads(ack)
        status = evt.get("header", {}).get("event")
        if status == "task-started":
            self._started = True
            self._recv_task = asyncio.create_task(self._recv_loop())
        else:
            msg = evt.get("header", {}).get("error_message", "启动失败")
            await self._ws.close()
            raise ProviderError(f"ASR 任务启动失败：{msg}")

    async def push_audio(self, pcm: bytes) -> None:
        if self._ws is None or not self._started or self._closed:
            return
        try:
            await self._ws.send(pcm)
        except websockets.ConnectionClosed:
            pass

    async def finish(self) -> None:
        """发送 finish-task，等待所有结果返回。"""
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send(json.dumps({
                "header": {
                    "action": "finish-task",
                    "task_id": self.task_id,
                    "streaming": "duplex",
                },
                "payload": {"input": {}},
            }))
            # 等待 task-finished 事件（由 _recv_loop set）
            try:
                await asyncio.wait_for(self._final_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("ASR finish 等待超时")
        except websockets.ConnectionClosed:
            pass

    async def close(self) -> None:
        self._closed = True
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    evt = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                header = evt.get("header", {})
                event = header.get("event", "")
                payload = evt.get("payload", {})

                if event == "result-generated":
                    sentence = payload.get("output", {}).get("sentence", {})
                    text = sentence.get("text", "")
                    # sentence_end=True 表示该句已定稿
                    if sentence.get("sentence_end"):
                        if text:
                            self.on_final(text)
                    else:
                        if text:
                            self.on_partial(text)
                elif event == "task-finished":
                    self._final_event.set()
                elif event == "task-failed":
                    err = payload.get("message") or header.get("error_message", "识别失败")
                    logger.error("ASR task-failed: %s", err)
                    self.on_error(err)
                    self._final_event.set()
        except websockets.ConnectionClosed:
            self._final_event.set()
        except asyncio.CancelledError:
            raise


registry.register(DashScopeRealtimeASR)


class AliyunParaformerASR(DashScopeRealtimeASR):
    name = "paraformer"


registry.register(AliyunParaformerASR)
