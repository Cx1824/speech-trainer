"""AI API 配置 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderStatus(BaseModel):
    """单个 Provider 的脱敏状态（用于返回前端展示）。"""

    provider: str
    base_url: str = ""
    model: str = ""
    has_key: bool = False


class ProviderConfigIn(BaseModel):
    """单个 Provider 的配置写入。"""

    provider: str = Field(..., description="厂商标识：tencent/xfyun/aliyun/openai/custom")
    base_url: str = ""
    api_key: str = ""
    api_secret: str = ""
    model: str = ""


class ApiConfigIn(BaseModel):
    """配置写入请求体。"""

    llm: ProviderConfigIn | None = None
    asr: ProviderConfigIn | None = None
    tts: ProviderConfigIn | None = None


class ApiConfigOut(BaseModel):
    """配置读取响应（脱敏）。"""

    llm: ProviderStatus
    asr: ProviderStatus
    tts: ProviderStatus
