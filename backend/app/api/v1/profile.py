"""面试档案路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.modules.profile import (
    create_profile,
    delete_profile,
    list_profiles,
    update_profile,
)

router = APIRouter()


class ProfileIn(BaseModel):
    name: str = Field(default="未命名档案")
    position: str = ""
    level: str = ""
    style: str = "professional"
    company: str = ""
    jd_url: str = ""
    jd_content: str = ""
    resume_file: str = ""
    resume_parsed_json: str = ""


@router.get("")
async def get_profiles(db: AsyncSession = Depends(get_session)) -> list[dict]:
    return await list_profiles(db)


@router.post("")
async def save_profile(
    payload: ProfileIn,
    db: AsyncSession = Depends(get_session),
) -> dict:
    return await create_profile(db, payload.model_dump())


@router.put("/{pid}")
async def modify_profile(
    pid: str,
    payload: ProfileIn,
    db: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await update_profile(db, pid, payload.model_dump(exclude_none=True))
    except ValueError as e:
        raise KeyError(str(e)) from e


@router.delete("/{pid}")
async def remove_profile(pid: str, db: AsyncSession = Depends(get_session)) -> dict:
    try:
        await delete_profile(db, pid)
        return {"ok": True}
    except ValueError as e:
        raise KeyError(str(e)) from e
