"""FastAPI 入口。

所有路由按 v1 前缀聚合，WebSocket 路由直接挂在 app 上。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1 import api_router
from app.api.v1 import voice_ws as voice_ws_router
from app.api.v1 import ws as ws_router
from app.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.app_log_level)
    from app.core.database import init_db
    await init_db()
    yield


app = FastAPI(
    title="表达能力训练平台",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_host_list,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router.router, prefix="/ws")
app.include_router(voice_ws_router.router, prefix="/ws")


def _resolve_frontend_file(frontend_dist: Path, request_path: str) -> Path:
    """解析生产前端文件；不存在的客户端路由统一回落到 index.html。"""
    root = frontend_dist.resolve()
    index = root / "index.html"
    candidate = (root / request_path.lstrip("/")).resolve()
    if candidate.is_relative_to(root) and candidate.is_file():
        return candidate
    return index


frontend_dist = settings.frontend_dist_path
if frontend_dist and (frontend_dist / "index.html").is_file():
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{request_path:path}", include_in_schema=False)
    async def production_frontend(request_path: str) -> FileResponse:
        return FileResponse(_resolve_frontend_file(frontend_dist, request_path))
