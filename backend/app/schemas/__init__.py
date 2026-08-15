"""Pydantic schemas。"""

from __future__ import annotations

from .common import ScoreWithFeedback
from .config import ApiConfigIn, ApiConfigOut, ProviderConfigIn, ProviderStatus
from .interview import (
    DialogueOut,
    FetchJDIn,
    FetchJDOut,
    InterviewConfigIn,
    InterviewSessionOut,
    NextQuestionOut,
    ResumeStructured,
)

__all__ = [
    "ApiConfigIn",
    "ApiConfigOut",
    "ProviderConfigIn",
    "ProviderStatus",
    "ScoreWithFeedback",
    "DialogueOut",
    "FetchJDIn",
    "FetchJDOut",
    "InterviewConfigIn",
    "InterviewSessionOut",
    "NextQuestionOut",
    "ResumeStructured",
]
