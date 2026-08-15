"""OpenAI 兼容协议的 LLM Adapter。

适用于：DeepSeek、通义千问、智谱 GLM、OpenAI、以及任何 OpenAI 协议兼容的 custom endpoint。
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx

from app.core.exceptions import ProviderError
from app.providers.base import BaseLLMProvider, registry
from app.schemas import ProviderConfigIn


class OpenAICompatibleLLM(BaseLLMProvider):
    """OpenAI Chat Completions 协议的 LLM 实现。"""

    name = "custom"  # 默认注册为 custom，其他厂商如 DeepSeek 也可独立子类化

    @property
    def _base_url(self) -> str:
        return (self.config.base_url or "").rstrip("/")

    @property
    def _api_key(self) -> str:
        return self.config.api_key or ""

    @property
    def _model(self) -> str:
        return self.config.model or "deepseek-chat"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def health_check(self) -> bool:
        """用真实 chat 调用测试（max_tokens=1，最贴近实际链路）。

        不用 GET /models：部分网关对该接口不校验 key，会产生"测试通过但实际失败"的误报。
        """
        if not self._base_url or not self._api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                )
            return resp.status_code == 200
        except Exception:
            return False

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> str | AsyncIterator[str]:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if not stream:
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPError as e:
                raise ProviderError(f"LLM 调用失败: {e}") from e

        return self._stream_chat(url, payload)

    async def _stream_chat(self, url: str, payload: dict) -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers()
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        body = line[6:].strip()
                        if body == "[DONE]":
                            break
                        try:
                            import json
                            chunk = json.loads(body)
                            delta = chunk["choices"][0]["delta"].get("content")
                            if delta:
                                yield delta
                        except (KeyError, ValueError):
                            continue
        except httpx.HTTPError as e:
            raise ProviderError(f"LLM 流式调用失败: {e}") from e


# 注册为 "custom"
registry.register(OpenAICompatibleLLM)


# 常见厂商别名（共用 OpenAI 兼容实现）
class DeepSeekLLM(OpenAICompatibleLLM):
    name = "deepseek"


class TongyiLLM(OpenAICompatibleLLM):
    name = "tongyi"


class ZhipuLLM(OpenAICompatibleLLM):
    name = "zhipu"


class OpenAILLM(OpenAICompatibleLLM):
    name = "openai"


for _cls in (DeepSeekLLM, TongyiLLM, ZhipuLLM, OpenAILLM):
    registry.register(_cls)
