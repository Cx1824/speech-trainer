"""AI API 配置路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.core.database import get_session
from app.core.exceptions import ConfigError
from app.modules.config import load_config, load_provider_config, save_config
from app.providers import get_asr, get_llm, get_tts

router = APIRouter()


@router.get("", response_model=schemas.ApiConfigOut)
async def get_config(db: AsyncSession = Depends(get_session)) -> schemas.ApiConfigOut:
    return await load_config(db)


@router.put("", response_model=schemas.ApiConfigOut)
async def update_config(
    payload: schemas.ApiConfigIn,
    db: AsyncSession = Depends(get_session),
) -> schemas.ApiConfigOut:
    return await save_config(db, payload)


@router.post("/test/{kind}")
async def test_provider(
    kind: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    if kind not in ("llm", "asr", "tts"):
        raise ConfigError(f"不支持的 kind: {kind}")
    cfg = await load_provider_config(db, kind)

    # 前置检查：关键字段是否配置
    missing = []
    if cfg.provider != "edge" and not cfg.api_key:
        missing.append("API Key")
    if kind in ("llm", "tts") and cfg.provider not in ("qwen_audio", "cosyvoice", "aliyun_tts", "edge") and not cfg.base_url:
        missing.append("Base URL")
    if missing:
        return {"ok": False, "message": f"未配置：{', '.join(missing)}"}

    try:
        if kind == "llm":
            provider = get_llm(cfg)
        elif kind == "tts":
            provider = get_tts(cfg)
        else:
            provider = get_asr(cfg)
        ok = await provider.health_check()
        if ok:
            message = "连通正常（已用真实调用验证）"
        else:
            message = (
                "连通失败：真实调用未通过。"
                "请检查 API Key 是否有效、Base URL/模型名是否正确"
            )
    except ValueError as e:
        ok, message = False, f"未实现该 provider：{e}"
    except Exception as e:
        ok, message = False, f"测试异常：{e}"
    return {"ok": ok, "message": message}
