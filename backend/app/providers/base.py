"""AI Provider 抽象基类。

所有 LLM/TTS/ASR 厂商实现都必须继承对应基类。
通过 ProviderRegistry 按名称注册与查找。
"""

from __future__ import annotations

import abc
from typing import AsyncIterator, Dict, Optional, Type

from app.schemas import ProviderConfigIn


class BaseProvider(abc.ABC):
    """所有 Provider 的公共基类。"""

    kind: str = "base"  # llm / tts / asr
    name: str = ""      # 厂商标识，例如 deepseek / tencent / xfyun

    def __init__(self, config: ProviderConfigIn) -> None:
        self.config = config

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """连通性测试。"""


class BaseLLMProvider(BaseProvider):
    """LLM 提供商。"""

    kind = "llm"

    @abc.abstractmethod
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
        """对话补全。

        stream=True 时返回 AsyncIterator[str]（增量文本）。
        max_tokens/read_timeout 仅用于有明确边界的非流式长任务；普通调用沿用默认值。
        thinking 仅在调用方明确要求且厂商协议支持时传递。
        """


class BaseTTSProvider(BaseProvider):
    """TTS 提供商。"""

    kind = "tts"

    @abc.abstractmethod
    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """文本 -> 语音二进制（mp3 或 wav）。"""


class BaseASRProvider(BaseProvider):
    """ASR 提供商。"""

    kind = "asr"

    @abc.abstractmethod
    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[str]:
        """流式音频 -> 流式文本。"""


class ProviderRegistry:
    """按 (kind, name) 注册与查找 Provider 类。"""

    def __init__(self) -> None:
        self._registry: Dict[tuple[str, str], Type[BaseProvider]] = {}

    def register(self, cls: Type[BaseProvider]) -> Type[BaseProvider]:
        """可作为装饰器使用。"""
        if not cls.kind or not cls.name:
            raise ValueError(f"Provider 缺少 kind/name: {cls}")
        self._registry[(cls.kind, cls.name)] = cls
        return cls

    def get(
        self,
        kind: str,
        name: str,
    ) -> Optional[Type[BaseProvider]]:
        return self._registry.get((kind, name))

    def list(self, kind: str) -> list[str]:
        return sorted([n for (k, n) in self._registry if k == kind])


# 全局单例
registry = ProviderRegistry()
