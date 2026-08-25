"""场景注册表。

新场景在此 import 并加入 REGISTRY 即完成接入，核心模块零改动。
"""

from __future__ import annotations

from app.modules.scenarios.base import (
    EvaluationAxis,
    EvaluationProfile,
    ScenarioContext,
    ScenarioPack,
    ScenarioStage,
)
from app.modules.scenarios.packs.interview import pack as interview_pack
from app.modules.scenarios.packs.presentation import pack as presentation_pack
from app.modules.scenarios.packs.speech import pack as speech_pack

REGISTRY: dict[str, ScenarioPack] = {
    p.key: p for p in (interview_pack, presentation_pack, speech_pack)
}

DEFAULT_SCENARIO = "interview"


def get_pack(key: str | None) -> ScenarioPack:
    """按 key 取场景包，未知/空回落到面试。"""
    return REGISTRY.get(key or "", REGISTRY[DEFAULT_SCENARIO])


def list_packs() -> list[ScenarioPack]:
    """列出全部场景（前端首页卡片用）。"""
    return list(REGISTRY.values())


__all__ = [
    "REGISTRY",
    "DEFAULT_SCENARIO",
    "get_pack",
    "list_packs",
    "EvaluationAxis",
    "EvaluationProfile",
    "ScenarioContext",
    "ScenarioPack",
    "ScenarioStage",
]
