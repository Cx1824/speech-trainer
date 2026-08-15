"""AI 配置持久化与读取。

用户在前端配置后，写入数据库；运行时 Provider 实例化时从数据库读。
未配置时回退到 .env 默认值。
"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.database import ApiConfigRow
from app.schemas import ApiConfigIn, ApiConfigOut, ProviderConfigIn, ProviderStatus


async def load_config(db: AsyncSession) -> ApiConfigOut:
    """读取配置（脱敏）。"""
    settings = get_settings()
    row = await _get_row(db)
    llm = _status_from_row_or_env(row.llm_json if row else None, "llm", settings)
    asr = _status_from_row_or_env(row.asr_json if row else None, "asr", settings)
    tts = _status_from_row_or_env(row.tts_json if row else None, "tts", settings)
    return ApiConfigOut(llm=llm, asr=asr, tts=tts)


async def save_config(db: AsyncSession, data: ApiConfigIn) -> ApiConfigOut:
    """合并保存（部分字段不传则保留旧值）。"""
    row = await _get_row(db)
    if row is None:
        row = ApiConfigRow(id=1, llm_json="", asr_json="", tts_json="")
        db.add(row)

    if data.llm:
        row.llm_json = _merge(row.llm_json, data.llm)
    if data.asr:
        row.asr_json = _merge(row.asr_json, data.asr)
    if data.tts:
        row.tts_json = _merge(row.tts_json, data.tts)

    await db.commit()
    return await load_config(db)


async def load_provider_config(
    db: AsyncSession,
    kind: str,
) -> ProviderConfigIn:
    """读取用于 Provider 实例化的原始配置（含密钥）。"""
    settings = get_settings()
    row = await _get_row(db)
    raw = ""
    if row:
        raw = {"llm": row.llm_json, "asr": row.asr_json, "tts": row.tts_json}[kind]
    if raw:
        return ProviderConfigIn(**json.loads(raw))
    # 回退 env
    return _provider_from_env(kind, settings)


async def load_voice_baseline(db: AsyncSession) -> dict | None:
    """读取个人声学基线（情绪 2.0 校准产物）。未校准/已清除返回 None。"""
    row = await _get_row(db)
    if row and row.voice_baseline_json:
        try:
            data = json.loads(row.voice_baseline_json)
        except json.JSONDecodeError:
            return None
        # 清除时存的是空 dict {}；无有效字段视为未校准
        if not data or "pitch_jitter" not in data:
            return None
        return data
    return None


async def save_voice_baseline(db: AsyncSession, baseline: dict) -> None:
    """保存/覆盖个人声学基线（换人重新校准即覆盖）。"""
    row = await _get_row(db)
    if row is None:
        row = ApiConfigRow(id=1, llm_json="", asr_json="", tts_json="", voice_baseline_json="")
        db.add(row)
    row.voice_baseline_json = json.dumps(baseline, ensure_ascii=False)
    await db.commit()


# ---- 内部 ----

async def _get_row(db: AsyncSession) -> Optional[ApiConfigRow]:
    result = await db.execute(select(ApiConfigRow).where(ApiConfigRow.id == 1))
    return result.scalar_one_or_none()


def _merge(old_json: str, new: ProviderConfigIn) -> str:
    old = json.loads(old_json) if old_json else {}
    merged = {**old, **new.model_dump(exclude_none=True)}
    return json.dumps(merged, ensure_ascii=False)


def _status_from_row_or_env(
    row_json: Optional[str],
    kind: str,
    settings: Settings,
) -> ProviderStatus:
    if row_json:
        data = json.loads(row_json)
        return ProviderStatus(
            provider=data.get("provider", "custom"),
            base_url=data.get("base_url", ""),
            model=data.get("model", ""),
            has_key=bool(data.get("api_key")),
        )
    # env fallback
    pc = _provider_from_env(kind, settings)
    return ProviderStatus(
        provider=pc.provider,
        base_url=pc.base_url or "",
        model=pc.model or "",
        has_key=bool(pc.api_key),
    )


def _provider_from_env(kind: str, settings: Settings) -> ProviderConfigIn:
    prefix = kind
    # TTS 用 voice 字段，LLM/ASR 用 model 字段
    if kind == "tts":
        model = getattr(settings, "tts_voice", "")
    else:
        model = getattr(settings, f"{prefix}_model", "")
    return ProviderConfigIn(
        provider=getattr(settings, f"{prefix}_provider"),
        base_url=getattr(settings, f"{prefix}_base_url"),
        api_key=getattr(settings, f"{prefix}_api_key"),
        # LLM 无 secret 字段，缺省为空
        api_secret=getattr(settings, f"{prefix}_api_secret", ""),
        model=model,
    )
