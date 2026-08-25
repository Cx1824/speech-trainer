"""面试会话 HTTP 路由。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.config import get_settings
from app.core.database import get_session
from app.core.exceptions import ResumeParseError
from app.modules import interview
from app.modules.config import load_provider_config
from app.modules.interview.styles import all_styles
from app.modules.jd import fetch_jd
from app.modules.resume import MAX_FILE_SIZE, SUPPORTED_EXT, extract_text, parse_with_llm
from app.providers import get_llm

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/styles")
async def list_styles() -> dict:
    """列出所有面试官风格。"""
    return {"styles": all_styles()}


@router.get("/modes")
async def list_interview_modes() -> dict:
    """列出面试类型、训练强度与预计耗时。"""
    from app.modules.interview.planner import list_modes

    return list_modes()


@router.get("/scenarios")
async def list_scenarios() -> dict:
    """列出所有训练场景（前端首页卡片用）。"""
    from app.modules.scenarios import list_packs

    return {
        "scenarios": [
            {
                "key": p.key,
                "name": p.name,
                "role_name": p.role_name,
                "description": p.description,
                "needs_resume": p.needs_resume,
                "needs_material": p.needs_material,
                "timed": p.timed,
            }
            for p in list_packs()
        ]
    }


@router.post("/jd/fetch", response_model=schemas.FetchJDOut)
async def fetch_jd_route(payload: schemas.FetchJDIn) -> schemas.FetchJDOut:
    """抓取 JD 链接内容。"""
    result = await fetch_jd(payload.url)
    return schemas.FetchJDOut(**result)


@router.post("", response_model=schemas.InterviewSessionOut)
async def create_interview(
    payload: schemas.InterviewConfigIn,
    db: AsyncSession = Depends(get_session),
) -> schemas.InterviewSessionOut:
    """创建面试会话。"""
    return await interview.create_session(db, payload)


@router.patch("/{sid}", response_model=schemas.InterviewSessionOut)
async def update_interview(
    sid: str,
    payload: schemas.InterviewConfigIn,
    db: AsyncSession = Depends(get_session),
) -> schemas.InterviewSessionOut:
    """更新会话配置（部分字段）。"""
    return await interview.update_session(
        db,
        sid,
        position=payload.position,
        level=payload.level,
        style=payload.style,
        company=payload.company,
        jd_url=payload.jd_url,
        jd_content=payload.jd_content,
        duration_limit=payload.duration_limit,
        interview_mode=payload.interview_mode,
        interview_intensity=payload.interview_intensity,
        source_session_id=payload.source_session_id,
    )


@router.get("", response_model=list[schemas.InterviewSessionOut])
async def list_interviews(db: AsyncSession = Depends(get_session)):
    return await interview.list_sessions(db)


@router.get("/{sid}", response_model=schemas.InterviewSessionOut)
async def get_interview(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> schemas.InterviewSessionOut:
    return await interview.get_session(db, sid)


@router.post("/{sid}/resume", response_model=schemas.InterviewSessionOut)
async def upload_resume(
    sid: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
) -> schemas.InterviewSessionOut:
    """上传简历并解析。"""
    settings = get_settings()
    # 校验
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise ResumeParseError(f"不支持的格式：{ext}，仅支持 {', '.join(SUPPORTED_EXT)}")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise ResumeParseError(f"文件过大：{len(content)} > {MAX_FILE_SIZE}")

    # 提取文本
    text = extract_text(content, file.filename or "resume")

    # 调用 LLM 解析
    cfg = await load_provider_config(db, "llm")
    provider = get_llm(cfg)

    async def _llm(messages):
        return await provider.chat(messages, temperature=0.1)

    parsed = await parse_with_llm(text, _llm)

    # 保存文件 + 解析结果
    file_path = Path(settings.upload_dir) / f"{sid}_resume{ext}"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)

    return await interview.save_resume(db, sid, str(file_path), parsed)


@router.post("/{sid}/material", response_model=schemas.InterviewSessionOut)
async def upload_material(
    sid: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
) -> schemas.InterviewSessionOut:
    """上传汇报/演讲材料并提取文本（复用简历解析的文本抽取链路）。"""
    settings = get_settings()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise ResumeParseError(f"不支持的格式：{ext}，仅支持 {', '.join(SUPPORTED_EXT)}")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise ResumeParseError(f"文件过大：{len(content)} > {MAX_FILE_SIZE}")

    text = extract_text(content, file.filename or "material")

    file_path = Path(settings.upload_dir) / f"{sid}_material{ext}"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)

    return await interview.save_material(db, sid, str(file_path), text)


@router.post("/{sid}/start", response_model=schemas.InterviewSessionOut)
async def start(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> schemas.InterviewSessionOut:
    return await interview.start_interview(db, sid)


@router.post("/{sid}/end", response_model=schemas.InterviewSessionOut)
async def end(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> schemas.InterviewSessionOut:
    """结束面试（HTTP 兜底，不依赖 WS 存活）。"""
    # 浏览器正常结束时 WS 与本请求几乎同时到达。若有活动语音连接，先让它
    # 冲刷内存中的 final/partial，再标记完成，避免报告抢先读到空对话。
    from app.api.v1.voice_ws import wait_for_voice_flush

    await wait_for_voice_flush(sid)
    return await interview.complete_interview(db, sid)


@router.get("/{sid}/dialogues", response_model=list[schemas.DialogueOut])
async def get_dialogues(
    sid: str,
    db: AsyncSession = Depends(get_session),
):
    return await interview.list_dialogues(db, sid)
