"""题库数据模型与 Manager（简化版，单表）。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import Column, String, Text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base


class QuestionBankRow(Base):
    """岗位题库（按岗位一行）。"""

    __tablename__ = "question_bank"

    position = Column(String(64), primary_key=True)
    questions_json = Column(Text, default="")


async def get_questions(db: AsyncSession, position: str) -> list[dict[str, Any]]:
    from sqlalchemy import select
    res = await db.execute(select(QuestionBankRow).where(QuestionBankRow.position == position))
    row = res.scalar_one_or_none()
    if not row or not row.questions_json:
        return []
    return json.loads(row.questions_json)


async def save_questions(
    db: AsyncSession, position: str, questions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    row = await db.get(QuestionBankRow, position)
    if row is None:
        row = QuestionBankRow(position=position, questions_json="")
        db.add(row)
    # 标注 id
    for q in questions:
        if not q.get("id"):
            q["id"] = str(uuid.uuid4())
    row.questions_json = json.dumps(questions, ensure_ascii=False)
    await db.commit()
    return questions
