"""阿里云 DashScope CosyVoice TTS Provider。

通过 DashScope WebSocket API 调用 cosyvoice-v3-flash 模型合成语音。
CosyVoice 是专用语音合成模型：输入什么文字就读什么字，逐字合成、零改写，
不会像对话模型那样自行补充内容或改写语气。

协议（DashScope 语音合成 duplex）：
1. wss://dashscope.aliyuncs.com/api-ws/v1/inference?model=<model>
   Header: Authorization: Bearer <key>
2. run-task 启动任务（streaming=duplex，parameters 含 voice/format 等）
3. 服务端返回 task-started 后，客户端发 continue-task 推送文本（payload.input.text）
4. 发 finish-task 表示文本结束
5. 服务端持续推送二进制音频帧（WAV），最后返回 task-finished（含 usage）
6. 出错返回 task-failed（header.error_message）

一次 synthesize() 调用 = 一次 WS 连接 + 一个任务，短句合成延迟低。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from urllib.parse import parse_qs, urlparse

import websockets

from app.core.exceptions import ProviderError
from app.providers.base import BaseTTSProvider, registry
from app.schemas import ProviderConfigIn

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "cosyvoice-v3-flash"
DEFAULT_VOICE = "longcheng_v3"  # 龙橙：智慧青年男，适合面试官角色
WS_BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
SAMPLE_RATE = 24000


class CosyVoiceTTS(BaseTTSProvider):
    """阿里云 CosyVoice 专业语音合成。"""

    name = "cosyvoice"

    @property
    def _api_key(self) -> str:
        return self.config.api_key or ""

    @property
    def _voice(self) -> str:
        return self.config.model or DEFAULT_VOICE  # 复用 model 字段存音色

    @property
    def _model(self) -> str:
        """模型名（base_url 字段复用存模型名，或完整 WS URL）。"""
        raw = self.config.base_url or ""
        if raw.startswith(("wss://", "ws://", "https://", "http://")):
            qs = parse_qs(urlparse(raw).query)
            return qs.get("model", [DEFAULT_MODEL])[0]
        return raw or DEFAULT_MODEL

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            audio = await self.synthesize("测")
            return len(audio) > 100
        except Exception as e:
            logger.info("CosyVoice health_check 失败：%s", e)
            return False

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """合成语音，握手超时/网络抖动自动重试一次。"""
        if not text.strip():
            return b""
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                return await self._synthesize_once(text, voice=voice)
            except ProviderError as e:
                last_err = e
                # 仅对超时/握手类错误重试（内容错误重试无意义）
                if "超时" not in str(e):
                    raise
                logger.warning("CosyVoice 第 %d 次合成超时，重试", attempt + 1)
                await asyncio.sleep(0.5)
        raise last_err  # type: ignore[misc]

    async def _synthesize_once(self, text: str, *, voice: str | None = None) -> bytes:
        if not self._api_key:
            raise ProviderError("CosyVoice 未配置 API Key")

        url = f"{WS_BASE}?model={self._model}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        use_voice = voice or self._voice
        task_id = uuid.uuid4().hex

        run_task = {
            "header": {
                "action": "run-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "tts",
                "function": "SpeechSynthesizer",
                "model": self._model,
                "parameters": {
                    "voice": use_voice,
                    "format": "wav",
                    "sample_rate": SAMPLE_RATE,
                    "volume": 50,
                    "rate": 1.0,
                    "pitch": 1.0,
                },
                "input": {},
            },
        }
        continue_task = {
            "header": {
                "action": "continue-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {"text": text}},
        }
        finish_task = {
            "header": {
                "action": "finish-task",
                "task_id": task_id,
                "streaming": "duplex",
            },
            "payload": {"input": {}},
        }

        try:
            async with websockets.connect(
                url, additional_headers=headers, open_timeout=10, close_timeout=5
            ) as ws:
                await ws.send(json.dumps(run_task))

                # 等 task-started 后再推文本
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    if isinstance(raw, bytes):
                        continue
                    evt = json.loads(raw)
                    event = evt.get("header", {}).get("event", "")
                    if event == "task-started":
                        break
                    if event == "task-failed":
                        err = evt.get("header", {}).get("error_message", "") or evt
                        raise ProviderError(f"CosyVoice 启动失败：{err}")

                await ws.send(json.dumps(continue_task))
                await ws.send(json.dumps(finish_task))

                audio_chunks: list[bytes] = []
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    if isinstance(raw, bytes):
                        audio_chunks.append(raw)
                        continue
                    evt = json.loads(raw)
                    header = evt.get("header", {})
                    event = header.get("event", "")

                    if event == "task-finished":
                        break
                    if event == "task-failed":
                        err = header.get("error_message", "") or evt
                        raise ProviderError(f"CosyVoice 合成失败：{err}")
                    # 其他事件忽略

                audio = b"".join(audio_chunks)
                if not audio:
                    raise ProviderError("CosyVoice 未返回音频")
                return audio

        except asyncio.TimeoutError:
            raise ProviderError("CosyVoice 合成超时")
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"CosyVoice 合成失败：{e}")


registry.register(CosyVoiceTTS)


class CosyVoiceTTSFlash(CosyVoiceTTS):
    """cosyvoice 别名。"""

    name = "aliyun_tts"


registry.register(CosyVoiceTTSFlash)
