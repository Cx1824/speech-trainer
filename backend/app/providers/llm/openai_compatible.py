"""OpenAI 兼容协议的 LLM Adapter。

适用于：DeepSeek、通义千问、智谱 GLM、OpenAI、以及任何 OpenAI 协议兼容的 custom endpoint。
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from app.core.exceptions import ProviderError
from app.providers.base import BaseLLMProvider, registry
from app.schemas import ProviderConfigIn

LLM_REQUEST_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=60.0,
    write=30.0,
    pool=10.0,
)
LLM_STREAM_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=90.0,
    write=30.0,
    pool=10.0,
)
MAX_LLM_OUTPUT_TOKENS = 32_768
MAX_LLM_READ_TIMEOUT_SECONDS = 300.0

logger = logging.getLogger(__name__)


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
        return self.config.model or "deepseek-v4-pro"

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
        max_tokens: int | None = None,
        read_timeout: float | None = None,
        thinking: bool | None = None,
    ) -> str | AsyncIterator[str]:
        if max_tokens is not None and (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= MAX_LLM_OUTPUT_TOKENS
        ):
            raise ValueError(
                f"max_tokens 必须是 1 到 {MAX_LLM_OUTPUT_TOKENS} 之间的整数"
            )
        if read_timeout is not None and (
            isinstance(read_timeout, bool)
            or not isinstance(read_timeout, (int, float))
            or not 1 <= read_timeout <= MAX_LLM_READ_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "read_timeout 必须是 1 到 "
                f"{int(MAX_LLM_READ_TIMEOUT_SECONDS)} 秒之间的数字"
            )
        if thinking is not None and not isinstance(thinking, bool):
            raise ValueError("thinking 必须是布尔值")

        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if not stream:
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if thinking is not None:
                payload["thinking"] = {
                    "type": "enabled" if thinking else "disabled"
                }
            timeout = LLM_REQUEST_TIMEOUT
            if read_timeout is not None:
                timeout = httpx.Timeout(
                    connect=LLM_REQUEST_TIMEOUT.connect,
                    read=float(read_timeout),
                    write=LLM_REQUEST_TIMEOUT.write,
                    pool=LLM_REQUEST_TIMEOUT.pool,
                )
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
                try:
                    choice = data["choices"][0]
                    message = choice["message"]
                except (KeyError, IndexError, TypeError) as e:
                    raise ProviderError("LLM 返回格式异常，请稍后重试") from e

                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content

                finish_reason = choice.get("finish_reason")
                reasoning_content = message.get("reasoning_content")
                reasoning_length = (
                    len(reasoning_content)
                    if isinstance(reasoning_content, str)
                    else 0
                )
                logger.warning(
                    "LLM 未返回最终正文：finish_reason=%s "
                    "content_length=%d reasoning_length=%d",
                    finish_reason,
                    len(content) if isinstance(content, str) else 0,
                    reasoning_length,
                )
                if finish_reason == "length":
                    raise ProviderError(
                        "LLM 输出达到长度上限，未生成完整结果，请稍后重试"
                    )
                if reasoning_length:
                    raise ProviderError(
                        "LLM 仅完成了内部分析，尚未生成最终结果，请稍后重试"
                    )
                raise ProviderError("LLM 未返回有效内容，请稍后重试")
            except httpx.TimeoutException as e:
                raise ProviderError("LLM 响应超时，请检查网络或服务地址后重试") from e
            except httpx.HTTPError as e:
                raise ProviderError(f"LLM 调用失败: {e}") from e

        return self._stream_chat(url, payload)

    async def _stream_chat(self, url: str, payload: dict) -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(timeout=LLM_STREAM_TIMEOUT) as client:
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
        except httpx.TimeoutException as e:
            raise ProviderError("LLM 流式响应超时，请检查网络或服务地址后重试") from e
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
