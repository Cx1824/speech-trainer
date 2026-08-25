"""Provider 配置部分更新与密钥边界测试。"""

from __future__ import annotations

import json

import pytest

from app.core.exceptions import ConfigError
from app.modules.config.store import _merge
from app.schemas import ProviderConfigIn


def _saved_config() -> str:
    return json.dumps(
        {
            "provider": "custom",
            "base_url": "https://api.example.com/v1",
            "api_key": "saved-key",
            "model": "model-a",
        }
    )


def test_partial_update_preserves_fields_not_sent() -> None:
    update = ProviderConfigIn.model_validate(
        {"provider": "custom", "model": "model-b"}
    )
    merged = json.loads(_merge(_saved_config(), update))

    assert merged["api_key"] == "saved-key"
    assert merged["base_url"] == "https://api.example.com/v1"
    assert merged["model"] == "model-b"


def test_destination_change_requires_new_key() -> None:
    update = ProviderConfigIn.model_validate(
        {"provider": "custom", "base_url": "https://other.example.com/v1"}
    )
    with pytest.raises(ConfigError, match="重新输入"):
        _merge(_saved_config(), update)


def test_destination_change_accepts_explicit_new_key() -> None:
    update = ProviderConfigIn.model_validate(
        {
            "provider": "custom",
            "base_url": "https://other.example.com/v1",
            "api_key": "new-key",
        }
    )
    merged = json.loads(_merge(_saved_config(), update))

    assert merged["base_url"] == "https://other.example.com/v1"
    assert merged["api_key"] == "new-key"


def test_switching_to_local_asr_preserves_cloud_key() -> None:
    update = ProviderConfigIn.model_validate(
        {"provider": "sherpa_onnx", "base_url": "", "model": ""}
    )
    merged = json.loads(_merge(_saved_config(), update))

    assert merged["provider"] == "sherpa_onnx"
    assert merged["api_key"] == "saved-key"


def test_switching_back_from_local_asr_can_reuse_preserved_key() -> None:
    local = json.dumps(
        {
            "provider": "sherpa_onnx",
            "base_url": "",
            "api_key": "saved-dashscope-key",
            "model": "",
        }
    )
    update = ProviderConfigIn.model_validate(
        {"provider": "dashscope", "model": "paraformer-realtime-v2"}
    )
    merged = json.loads(_merge(local, update))

    assert merged["provider"] == "dashscope"
    assert merged["api_key"] == "saved-dashscope-key"
