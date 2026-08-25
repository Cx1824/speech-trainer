from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.core.exceptions import ProviderError
from app.providers.llm import openai_compatible
from app.providers.llm.openai_compatible import (
    LLM_REQUEST_TIMEOUT,
    LLM_STREAM_TIMEOUT,
    MAX_LLM_OUTPUT_TOKENS,
    MAX_LLM_READ_TIMEOUT_SECONDS,
    OpenAICompatibleLLM,
)
from app.schemas import ProviderConfigIn


def _provider() -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        ProviderConfigIn(
            provider="custom",
            base_url="https://llm.example/v1",
            api_key="test-key",
            model="test-model",
        )
    )


def test_llm_default_model_is_supported_deepseek_model() -> None:
    provider = OpenAICompatibleLLM(
        ProviderConfigIn(
            provider="custom",
            base_url="https://api.deepseek.com",
            api_key="test-key",
        )
    )

    assert provider._model == "deepseek-v4-pro"


def test_fresh_install_prefills_deepseek_v4_pro_without_a_key() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "deepseek"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-v4-pro"
    assert settings.llm_api_key == ""


def test_llm_timeouts_are_bounded() -> None:
    assert LLM_REQUEST_TIMEOUT.connect == 10.0
    assert LLM_REQUEST_TIMEOUT.read == 60.0
    assert LLM_STREAM_TIMEOUT.connect == 10.0
    assert LLM_STREAM_TIMEOUT.read == 90.0


@pytest.mark.asyncio
async def test_non_stream_timeout_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_timeout: httpx.Timeout | None = None

    class TimeoutClient:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            nonlocal captured_timeout
            captured_timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
            raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", TimeoutClient)

    with pytest.raises(ProviderError, match="响应超时"):
        await _provider().chat([{"role": "user", "content": "你好"}])

    assert captured_timeout is LLM_REQUEST_TIMEOUT


@pytest.mark.asyncio
async def test_non_stream_long_task_sets_output_limit_and_only_overrides_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_timeout: httpx.Timeout | None = None
    captured_payload: dict | None = None

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "完成"}}]}

    class Client:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            nonlocal captured_timeout
            captured_timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, *args, **kwargs):
            nonlocal captured_payload
            captured_payload = kwargs["json"]
            return Response()

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", Client)

    result = await _provider().chat(
        [{"role": "user", "content": "生成报告"}],
        max_tokens=4096,
        read_timeout=120,
        thinking=False,
    )

    assert result == "完成"
    assert captured_payload is not None
    assert captured_payload["max_tokens"] == 4096
    assert captured_payload["thinking"] == {"type": "disabled"}
    assert captured_timeout is not None
    assert captured_timeout.connect == LLM_REQUEST_TIMEOUT.connect
    assert captured_timeout.read == 120.0
    assert captured_timeout.write == LLM_REQUEST_TIMEOUT.write
    assert captured_timeout.pool == LLM_REQUEST_TIMEOUT.pool


@pytest.mark.asyncio
async def test_non_stream_default_does_not_send_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_timeout: httpx.Timeout | None = None
    captured_payload: dict | None = None

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "完成"}}]}

    class Client:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            nonlocal captured_timeout
            captured_timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, *args, **kwargs):
            nonlocal captured_payload
            captured_payload = kwargs["json"]
            return Response()

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", Client)

    await _provider().chat([{"role": "user", "content": "你好"}])

    assert captured_timeout is LLM_REQUEST_TIMEOUT
    assert captured_payload is not None
    assert "max_tokens" not in captured_payload
    assert "thinking" not in captured_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_data", "error_message"),
    [
        (
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": "",
                            "reasoning_content": "内部分析内容",
                        },
                    }
                ]
            },
            "长度上限",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": None,
                            "reasoning_content": "内部分析内容",
                        },
                    }
                ]
            },
            "尚未生成最终结果",
        ),
        (
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "", "reasoning_content": None},
                    }
                ]
            },
            "未返回有效内容",
        ),
    ],
)
async def test_non_stream_empty_final_content_has_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
    response_data: dict,
    error_message: str,
) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return response_data

    class Client:
        def __init__(self, *, timeout: httpx.Timeout) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", Client)

    with pytest.raises(ProviderError, match=error_message):
        await _provider().chat([{"role": "user", "content": "生成报告"}])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_tokens": 0}, "max_tokens"),
        ({"max_tokens": MAX_LLM_OUTPUT_TOKENS + 1}, "max_tokens"),
        ({"read_timeout": 0}, "read_timeout"),
        ({"read_timeout": MAX_LLM_READ_TIMEOUT_SECONDS + 1}, "read_timeout"),
        ({"thinking": "disabled"}, "thinking"),
    ],
)
async def test_non_stream_long_task_bounds_are_validated(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        await _provider().chat([{"role": "user", "content": "你好"}], **kwargs)
