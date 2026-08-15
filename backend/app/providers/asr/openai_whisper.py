"""OpenAI Whisper 协议的 ASR Adapter。

为简化 MVP，使用批量转写（接完整音频段）而非真正的流式。
真正的流式 ASR（如讯飞）需要专门实现，留待后续。
"""

from __future__ import annotations

import asyncio
import io
from typing import AsyncIterator

import httpx

from app.core.exceptions import ProviderError
from app.providers.base import BaseASRProvider, registry
from app.schemas import ProviderConfigIn


class OpenAIWhisperASR(BaseASRProvider):
    """OpenAI /api/v1/audio/transcriptions 协议（兼容 Whisper API）。"""

    name = "custom"

    @property
    def _base_url(self) -> str:
        return (self.config.base_url or "").rstrip("/")

    @property
    def _api_key(self) -> str:
        return self.config.api_key or ""

    @property
    def _model(self) -> str:
        return self.config.model or "whisper-1"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def health_check(self) -> bool:
        return bool(self._base_url and self._api_key)

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[str]:
        """聚合音频流后一次性转写。

        真正的流式 ASR 协议较为复杂，MVP 阶段先聚合再转写。
        """
        buffer = bytearray()
        async for chunk in audio_stream:
            buffer.extend(chunk)

        if not buffer:
            yield ""
            return

        url = f"{self._base_url}/audio/transcriptions"
        files = {
            "file": ("audio.webm", io.BytesIO(bytes(buffer)), "audio/webm"),
        }
        data = {"model": self._model, "language": "zh"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=self._headers(), files=files, data=data)
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "")
        except httpx.HTTPError as e:
            raise ProviderError(f"ASR 调用失败：{e}") from e

        yield text


registry.register(OpenAIWhisperASR)


class WhisperASR(OpenAIWhisperASR):
    name = "whisper"


registry.register(WhisperASR)
