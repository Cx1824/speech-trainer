"""AI API 配置路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.core.database import get_session
from app.core.exceptions import ConfigError
from app.modules.config import load_config, load_provider_config, save_config
from app.providers import get_asr, get_llm, get_tts
from app.providers.asr.realtime import asr_requires_api_key

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
    requires_key = not (
        (kind == "tts" and cfg.provider == "edge")
        or (kind == "asr" and not asr_requires_api_key(cfg.provider))
    )
    if requires_key and not cfg.api_key:
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
            message = (
                "本地语音识别可用，音频不会上传"
                if kind == "asr" and not asr_requires_api_key(cfg.provider)
                else "配置可用"
            )
        elif kind == "asr" and not asr_requires_api_key(cfg.provider):
            message = "本地语音模型尚未安装或无法加载，请先完成本地模型安装"
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


@router.get("/voice-calibration")
async def get_voice_calibration(db: AsyncSession = Depends(get_session)) -> dict:
    """获取校准文本 + 当前基线状态（前端校准卡片用）。"""
    from app.modules.analysis import CALIBRATION_TEXT
    from app.modules.config.store import load_voice_baseline

    baseline = await load_voice_baseline(db)
    return {
        "text": CALIBRATION_TEXT,
        "char_count": len(CALIBRATION_TEXT),
        "estimated_sec": round(len(CALIBRATION_TEXT) / 4.2),  # 按常人语速估
        "calibrated": baseline is not None,
        "baseline": baseline,
    }


@router.delete("/voice-calibration")
async def reset_voice_calibration(db: AsyncSession = Depends(get_session)) -> dict:
    """清除基线（换人时先清再重新校准；直接重校准也会覆盖）。"""
    from app.modules.config.store import save_voice_baseline

    await save_voice_baseline(db, {})
    return {"ok": True, "message": "已清除，请重新校准"}
