"""面试档案：保存简历 + 岗位/JD/风格配置，支持反复练习。"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import Base


class InterviewProfileRow(Base):
    """面试档案（用户保存的配置组合）。"""

    __tablename__ = "interview_profile"

    id = Column(String(36), primary_key=True)
    name = Column(String(128), nullable=False)             # 档案名，如"产品经理-字节"
    position = Column(String(64), default="")
    level = Column(String(32), default="")
    style = Column(String(32), default="professional")
    company = Column(String(128), default="")
    jd_url = Column(String(512), default="")
    jd_content = Column(Text, default="")
    resume_file = Column(String(512), default="")          # 简历文件路径
    resume_parsed_json = Column(Text, default="")          # 解析结果（含 position_guess）
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


async def create_profile(
    db: AsyncSession,
    data: dict[str, Any],
) -> dict[str, Any]:
    """创建档案。"""
    pid = str(uuid.uuid4())
    row = InterviewProfileRow(id=pid, name=data.get("name") or "未命名档案", **{
        k: data.get(k, "") for k in (
            "position", "level", "style", "company", "jd_url",
            "jd_content", "resume_file", "resume_parsed_json",
        )
    })
    db.add(row)
    await db.commit()
    return _to_dict(row)


async def update_profile(
    db: AsyncSession,
    pid: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """更新档案。"""
    row = await db.get(InterviewProfileRow, pid)
    if row is None:
        raise ValueError(f"档案不存在：{pid}")
    for k in ("name", "position", "level", "style", "company",
              "jd_url", "jd_content", "resume_file", "resume_parsed_json"):
        if k in data and data[k] is not None:
            setattr(row, k, data[k])
    await db.commit()
    return _to_dict(row)


async def delete_profile(db: AsyncSession, pid: str) -> None:
    row = await db.get(InterviewProfileRow, pid)
    if row is None:
        raise ValueError(f"档案不存在：{pid}")
    await db.delete(row)
    await db.commit()


async def list_profiles(db: AsyncSession) -> list[dict[str, Any]]:
    res = await db.execute(
        select(InterviewProfileRow).order_by(InterviewProfileRow.updated_at.desc()).limit(50)
    )
    return [_to_dict(r) for r in res.scalars().all()]


async def get_profile(db: AsyncSession, pid: str) -> Optional[dict[str, Any]]:
    row = await db.get(InterviewProfileRow, pid)
    return _to_dict(row) if row else None


def _to_dict(row: InterviewProfileRow) -> dict[str, Any]:
    resume_parsed = None
    if row.resume_parsed_json:
        try:
            resume_parsed = json.loads(row.resume_parsed_json)
        except Exception:
            resume_parsed = None
    return {
        "id": row.id,
        "name": row.name,
        "position": row.position,
        "level": row.level,
        "style": row.style,
        "company": row.company,
        "jd_url": row.jd_url,
        "jd_content": row.jd_content,
        "has_resume": bool(row.resume_file),
        "resume_file": row.resume_file,
        "resume_parsed": resume_parsed,
    }
