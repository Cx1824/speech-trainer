"""实时 ASR 会话工厂，让业务层不依赖具体厂商。"""

from __future__ import annotations

from typing import Callable, Protocol

from app.core.exceptions import ProviderError
from app.providers.asr.dashscope_realtime import DEFAULT_MODEL, RealtimeASRSession
from app.providers.asr.sherpa_local import SherpaOnnxRealtimeSession
from app.schemas import ProviderConfigIn

LOCAL_ASR_PROVIDERS = {"sherpa_onnx", "sherpa-onnx", "sherpa"}
CLOUD_ASR_PROVIDERS = {"dashscope", "aliyun", "paraformer"}


class RealtimeSession(Protocol):
    on_partial: Callable[[str], None]
    on_final: Callable[[str], None]
    on_error: Callable[[str], None]

    async def start(self) -> None: ...
    async def push_audio(self, pcm: bytes) -> None: ...
    async def flush_utterance(self) -> None: ...
    async def finish(self) -> None: ...
    async def close(self) -> None: ...


def is_local_asr(provider: str) -> bool:
    return provider.strip().lower() in LOCAL_ASR_PROVIDERS


def asr_requires_api_key(provider: str) -> bool:
    return not is_local_asr(provider)


def create_realtime_session(
    config: ProviderConfigIn,
    *,
    hotwords: list[str] | None = None,
    vocabulary_id: str = "",
) -> RealtimeSession:
    provider = config.provider.strip().lower()
    if provider in LOCAL_ASR_PROVIDERS:
        return SherpaOnnxRealtimeSession(config, hotwords=hotwords)
    if provider in CLOUD_ASR_PROVIDERS:
        model = config.model.strip() or config.base_url.strip() or DEFAULT_MODEL
        if model.startswith(("ws://", "wss://")):
            model = DEFAULT_MODEL
        return RealtimeASRSession(config.api_key, model, vocabulary_id=vocabulary_id)
    raise ProviderError("当前实时字幕尚未支持所选语音识别服务")
