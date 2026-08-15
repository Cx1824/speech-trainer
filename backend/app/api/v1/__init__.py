"""API v1 路由聚合。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import config, interview, profile, question_bank, report

api_router = APIRouter()
api_router.include_router(config.router, prefix="/config", tags=["config"])
api_router.include_router(interview.router, prefix="/interviews", tags=["interview"])
api_router.include_router(report.router, prefix="/reports", tags=["report"])
api_router.include_router(question_bank.router, prefix="/question_bank", tags=["question_bank"])
api_router.include_router(profile.router, prefix="/profiles", tags=["profile"])
