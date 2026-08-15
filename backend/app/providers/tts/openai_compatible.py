"""OpenAI 兼容协议的 TTS Adapter。

OpenAI /api/v1/audio/speech 协议，MiniMax、智谱等部分厂商也兼容。
"""

from __future__ import annotations

import httpx

from app.core.exceptions import ProviderError
from app.providers.base import BaseTTSProvider, registry
from app.schemas import ProviderConfigIn


class OpenAICompatibleTTS(BaseTTSProvider):
    """OpenAI TTS 协议实现。"""

    name = "custom"

    @property
    def _base_url(self) -> str:
        return (self.config.base_url or "").rstrip("/")

    @property
    def _api_key(self) -> str:
        return self.config.api_key or ""

    @property
    def _voice(self) -> str:
        return self.config.model or "alloy"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def health_check(self) -> bool:
        if not self._base_url or not self._api_key:
            return False
        # 用真实合成路径测试（合成"测"一个字），太短视为失败
        try:
            data = await self.synthesize("测")
            return len(data) > 100
        except Exception:
            return False

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        url = f"{self._base_url}/audio/speech"
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice or self._voice,
            "response_format": "mp3",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as e:
            raise ProviderError(f"TTS 调用失败: {e}") from e


registry.register(OpenAICompatibleTTS)


class OpenAITTS(OpenAICompatibleTTS):
    name = "openai"


class MiniMaxTTS(OpenAICompatibleTTS):
    name = "minimax"


for _cls in (OpenAITTS, MiniMaxTTS):
    registry.register(_cls)
