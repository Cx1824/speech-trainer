"""通用 schema 片段。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScoreWithFeedback(BaseModel):
    """评分 + 反馈。"""

    score: float = Field(..., ge=0, le=100, description="评分 0-100")
    feedback: str = Field(default="", description="反馈说明")
