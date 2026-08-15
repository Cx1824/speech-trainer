"""数据库连接与表结构。

MVP 用 SQLite + aiosqlite。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class ApiConfigRow(Base):
    """AI API 配置（key-value 简化存储）。

    单行存储，id=1。
    """

    __tablename__ = "api_config"

    id = Column(Integer, primary_key=True)
    llm_json = Column(Text, default="")
    asr_json = Column(Text, default="")
    tts_json = Column(Text, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


engine: "async_sessionmaker[AsyncSession] | None" = None


async def init_db() -> None:
    """初始化数据库（幂等）。"""
    global engine
    settings = get_settings()
    eng = create_async_engine(settings.database_url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    engine = async_sessionmaker(eng, expire_on_commit=False)


async def get_session() -> AsyncSession:
    """FastAPI 依赖注入用的生成器。"""
    if engine is None:
        await init_db()
    assert engine is not None
    async with engine() as session:
        yield session


async def get_db_session() -> AsyncSession:
    """返回 AsyncSession 上下文管理器（用于非 FastAPI 依赖场景，如 WebSocket）。"""
    if engine is None:
        await init_db()
    assert engine is not None
    return engine()
