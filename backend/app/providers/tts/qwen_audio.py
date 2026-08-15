"""阿里云 DashScope Qwen-Audio TTS Provider。

通过 DashScope Realtime API WebSocket 调用 Qwen-Audio-3.0-realtime-flash 模型合成语音。

工作原理：
1. 建立 WebSocket 连接
2. 发送 session.update 配置 voice
3. 发送 conversation.item.create 注入文本（作为"用户消息"触发模型回复）
4. 发送 response.create 触发生成
5. 接收 response.audio.delta 增量音频，合并 PCM
6. 接收 response.audio.done 表示完成
7. 把 PCM 16bit/24kHz 包装为 WAV 返回

注：Qwen-Audio 是对话模型，每次合成都会消耗一次对话轮次。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import wave
from typing import AsyncIterator

import websockets

from app.core.exceptions import ProviderError
from app.providers.base import BaseTTSProvider, registry
from app.schemas import ProviderConfigIn

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen-audio-3.0-realtime-flash"
DEFAULT_VOICE = "longanqian"
DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
SAMPLE_RATE = 24000  # Qwen 输出固定 24kHz


class QwenAudioTTS(BaseTTSProvider):
    """阿里云 Qwen-Audio TTS。"""

    name = "qwen_audio"

    @property
    def _api_key(self) -> str:
        return self.config.api_key or ""

    @property
    def _voice(self) -> str:
        return self.config.model or DEFAULT_VOICE  # 复用 model 字段存 voice

    @property
    def _model(self) -> str:
        """模型名（base_url 字段复用存模型名，或完整 WS URL）。"""
        raw = self.config.base_url or ""
        # 用户可能填完整 WS URL，此时从中提取模型名
        if raw.startswith(("wss://", "ws://", "https://", "http://")):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(raw)
            qs = parse_qs(parsed.query)
            return qs.get("model", [DEFAULT_MODEL])[0]
        return raw or DEFAULT_MODEL

    def _ws_url(self) -> str:
        return f"{DEFAULT_URL}?model={self._model}"

    async def health_check(self) -> bool:
        """用真实合成路径测试（合成 1 个字），确保 key 与音色都有效。"""
        if not self._api_key:
            return False
        try:
            audio = await self.synthesize("测")
            return len(audio) > 100  # WAV 头至少几十字节，太短视为失败
        except Exception as e:
            logger.info("Qwen-Audio health_check 失败：%s", e)
            return False

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        if not text.strip():
            return b""
        if not self._api_key:
            raise ProviderError("Qwen-Audio 未配置 API Key")

        headers = {"Authorization": f"Bearer {self._api_key}"}
        use_voice = voice or self._voice

        try:
            async with websockets.connect(
                self._ws_url(), additional_headers=headers, open_timeout=10, close_timeout=5
            ) as ws:
                # 1. session.update
                await ws.send(json.dumps({
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "voice": use_voice,
                        "turn_detection": None,  # 仅 TTS，关闭 VAD
                    },
                }))

                # 等待 session.created 确认
                ack = await asyncio.wait_for(ws.recv(), timeout=10)
                evt = json.loads(ack)
                if evt.get("type") == "error":
                    raise ProviderError(f"Qwen session 错误：{evt.get('error', {}).get('message')}")

                # 2. 注入用户消息（把要合成的文本作为"用户输入"，让模型复述/朗读）
                #    使用指令提示让模型逐字朗读
                instruction = (
                    f"请逐字朗读以下内容，只读文字本身，"
                    f"不要添加任何解释、问候、标点说明：\n\n{text}"
                )
                await ws.send(json.dumps({
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": instruction}],
                    },
                }))

                # 3. 触发响应
                await ws.send(json.dumps({"type": "response.create"}))

                # 4. 收集音频增量
                pcm_chunks: list[bytes] = []
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    evt = json.loads(raw)
                    t = evt.get("type")

                    if t == "response.audio.delta":
                        chunk = base64.b64decode(evt["delta"])
                        pcm_chunks.append(chunk)
                    elif t == "response.audio.done":
                        break
                    elif t == "response.done":
                        break
                    elif t == "error":
                        msg = evt.get("error", {}).get("message", "未知错误")
                        raise ProviderError(f"Qwen 合成错误：{msg}")
                    # 其他事件忽略

                pcm = b"".join(pcm_chunks)
                if not pcm:
                    raise ProviderError("Qwen 未返回音频")

                return _pcm_to_wav(pcm, SAMPLE_RATE)

        except asyncio.TimeoutError:
            raise ProviderError("Qwen-Audio 合成超时")
        except websockets.HTTPException as e:
            raise ProviderError(f"Qwen WebSocket 错误：{e}")
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Qwen-Audio 合成失败：{e}")


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """16bit PCM mono → WAV。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


# 注册为 qwen_audio 与 aliyun 别名
registry.register(QwenAudioTTS)


class AliyunQwenTTS(QwenAudioTTS):
    name = "aliyun"


registry.register(AliyunQwenTTS)
