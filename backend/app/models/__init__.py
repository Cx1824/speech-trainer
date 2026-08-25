"""数据模型。"""

from __future__ import annotations

from app.core.database import ApiConfigRow, Base
from app.models.interview import (
    InterviewDialogueRow,
    InterviewReportRow,
    InterviewSessionRow,
)

__all__ = [
    "Base",
    "ApiConfigRow",
    "InterviewSessionRow",
    "InterviewDialogueRow",
    "InterviewReportRow",
]
