"""Provider 工厂。

按 kind + name 查找并实例化 Provider。
具体实现通过 import 触发注册（不在 __init__ 顶层导入，避免循环）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.providers.base import (
    BaseASRProvider,
    BaseLLMProvider,
    BaseProvider,
    BaseTTSProvider,
    registry,
)
from app.schemas import ProviderConfigIn

if TYPE_CHECKING:
    pass


def _ensure_registered() -> None:
    """惰性导入所有 provider 实现以触发注册。幂等。"""
    if getattr(registry, "_loaded", False):
        return
    # 触发注册
    from app.providers import llm  # noqa: F401
    from app.providers import tts  # noqa: F401
    from app.providers import asr  # noqa: F401
    registry._loaded = True  # type: ignore[attr-defined]


def get_llm(config: ProviderConfigIn) -> BaseLLMProvider:
    _ensure_registered()
    cls = registry.get("llm", config.provider) or registry.get("llm", "custom")
    assert cls is not None
    return cls(config)  # type: ignore[return-value]


def get_tts(config: ProviderConfigIn) -> BaseTTSProvider:
    _ensure_registered()
    cls = registry.get("tts", config.provider) or registry.get("tts", "custom")
    if cls is None:
        raise ValueError(f"未找到 TTS provider: {config.provider}")
    return cls(config)  # type: ignore[return-value]


def get_asr(config: ProviderConfigIn) -> BaseASRProvider:
    _ensure_registered()
    cls = registry.get("asr", config.provider) or registry.get("asr", "custom")
    if cls is None:
        raise ValueError(f"未找到 ASR provider: {config.provider}")
    return cls(config)  # type: ignore[return-value]


def list_providers(kind: str) -> list[str]:
    _ensure_registered()
    return registry.list(kind)


__all__ = [
    "get_llm",
    "get_tts",
    "get_asr",
    "list_providers",
    "BaseProvider",
    "BaseLLMProvider",
    "BaseTTSProvider",
    "BaseASRProvider",
]
