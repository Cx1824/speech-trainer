"""后端测试公共夹具。"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base

# 确保模型在 create_all 前完成注册。
from app.models import interview as _interview_models  # noqa: F401


@pytest_asyncio.fixture
async def db_session():
    """为单个测试提供隔离的内存 SQLite 会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()
