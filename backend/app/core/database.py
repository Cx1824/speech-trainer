"""数据库连接与表结构。

MVP 用 SQLite + aiosqlite。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, String, Text, func, inspect, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


# 幂等迁移：给已有表补充缺失列（SQLite 的 create_all 不会 ALTER 旧表）
_MIGRATIONS: dict[str, list[tuple[str, Column]]] = {
    "interview_session": [
        ("scenario", Column("scenario", String(32), nullable=False, default="interview")),
        ("material_file", Column("material_file", String(256), default="")),
        ("material_text", Column("material_text", Text, default="")),
        ("duration_limit", Column("duration_limit", Integer, default=0)),
        ("started_at", Column("started_at", DateTime)),
        ("interview_mode", Column("interview_mode", String(32), default="full")),
        ("interview_intensity", Column("interview_intensity", String(32), default="standard")),
        ("source_session_id", Column("source_session_id", String(36), default="")),
        ("interview_plan_json", Column("interview_plan_json", Text, default="")),
    ],
    "api_config": [
        ("voice_baseline_json", Column("voice_baseline_json", Text, default="")),
    ],
}


def _apply_migrations(conn) -> None:
    inspector = inspect(conn)
    for table, columns in _MIGRATIONS.items():
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for name, col in columns:
            if name not in existing:
                col_type = col.type.compile(conn.dialect)
                conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"))


class ApiConfigRow(Base):
    """AI API 配置（key-value 简化存储）。

    单行存储，id=1。voice_baseline_json 存情绪 2.0 的个人声学基线（校准朗读产物）。
    """

    __tablename__ = "api_config"

    id = Column(Integer, primary_key=True)
    llm_json = Column(Text, default="")
    asr_json = Column(Text, default="")
    tts_json = Column(Text, default="")
    voice_baseline_json = Column(Text, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


engine: "async_sessionmaker[AsyncSession] | None" = None


async def init_db() -> None:
    """初始化数据库（幂等：建表 + 补列）。"""
    global engine
    settings = get_settings()
    eng = create_async_engine(settings.database_url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_migrations)
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
