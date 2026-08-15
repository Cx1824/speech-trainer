"""题库路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.modules.question_bank import get_questions, save_questions

router = APIRouter()


class QuestionIn(BaseModel):
    id: str | None = None
    content: str
    intent: str = ""
    difficulty: str = "medium"


class QuestionBankIn(BaseModel):
    questions: list[QuestionIn]


@router.get("/{position}")
async def list_questions(
    position: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    return {"position": position, "questions": await get_questions(db, position)}


@router.put("/{position}")
async def update_questions(
    position: str,
    payload: QuestionBankIn,
    db: AsyncSession = Depends(get_session),
) -> dict:
    qs = await save_questions(db, position, [q.model_dump() for q in payload.questions])
    return {"position": position, "questions": qs}
