"""面试会话管理器。

负责：
- 创建/加载会话
- 状态机推进
- 调用 LLM 生成下一题
- 持久化对话
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InterviewError, NotFoundError
from app.models.interview import InterviewDialogueRow, InterviewSessionRow
from app.modules.interview.prompts import build_messages
from app.modules.interview.stages import STAGE_QUESTION_LIMIT, Stage, next_stage
from app.providers import get_llm
from app.schemas import InterviewConfigIn, InterviewSessionOut, ResumeStructured
from datetime import datetime


def _pack(row: InterviewSessionRow):
    """按会话行取场景包（延迟导入避免循环依赖）。"""
    from app.modules.scenarios import get_pack
    return get_pack(row.scenario)


async def create_session(
    db: AsyncSession,
    config: InterviewConfigIn,
) -> InterviewSessionOut:
    """创建空会话。"""
    sid = str(uuid.uuid4())
    row = InterviewSessionRow(
        id=sid,
        scenario=config.scenario or "interview",
        position=config.position or "未指定",
        level=config.level,
        style=config.style,
        company=config.company,
        jd_url=config.jd_url,
        jd_content=config.jd_content,
        duration_limit=config.duration_limit or 0,
        status="configuring",
        current_stage="",
    )
    db.add(row)
    await db.commit()
    return _to_out(row)


async def get_session(db: AsyncSession, sid: str) -> InterviewSessionOut:
    row = await _load_row(db, sid)
    return _to_out(row)


async def list_sessions(db: AsyncSession) -> list[InterviewSessionOut]:
    res = await db.execute(
        select(InterviewSessionRow).order_by(InterviewSessionRow.created_at.desc()).limit(50)
    )
    return [_to_out(r) for r in res.scalars().all()]


async def save_resume(
    db: AsyncSession,
    sid: str,
    resume_file: str,
    parsed: dict[str, Any],
) -> InterviewSessionOut:
    row = await _load_row(db, sid)
    row.resume_file = resume_file
    row.resume_parsed_json = json.dumps(parsed, ensure_ascii=False)
    # 仅在用户未填写岗位时自动填充（不覆盖用户输入）
    position_guess = parsed.get("position_guess", "")
    level_guess = parsed.get("level_guess", "")
    if position_guess and (not row.position or row.position == "未指定"):
        row.position = position_guess
    if level_guess and (not row.level or row.level == "中级"):
        row.level = level_guess
    await db.commit()
    return _to_out(row)


async def update_session(
    db: AsyncSession,
    sid: str,
    *,
    position: str | None = None,
    level: str | None = None,
    style: str | None = None,
    company: str | None = None,
    jd_url: str | None = None,
    jd_content: str | None = None,
    duration_limit: int | None = None,
) -> InterviewSessionOut:
    """更新会话配置字段（部分更新）。"""
    row = await _load_row(db, sid)
    if position is not None:
        row.position = position
    if level is not None:
        row.level = level
    if style is not None:
        row.style = style
    if company is not None:
        row.company = company
    if jd_url is not None:
        row.jd_url = jd_url
    if jd_content is not None:
        row.jd_content = jd_content
    if duration_limit is not None:
        row.duration_limit = duration_limit
    await db.commit()
    return _to_out(row)


async def start_interview(db: AsyncSession, sid: str) -> InterviewSessionOut:
    """开始面试，进入第一阶段。"""
    row = await _load_row(db, sid)
    if row.status not in ("configuring", "in_progress"):
        raise InterviewError(f"会话状态不允许开始：{row.status}")

    # 岗位为空时尝试从简历/简历 + JD 推断
    if not row.position or row.position == "未指定":
        inferred = _infer_position(row)
        if inferred:
            row.position = inferred
        else:
            row.position = "未指定"  # 仍保留，LLM 会基于 JD 出题

    row.status = "in_progress"
    row.current_stage = Stage.OPENING.value
    if not row.started_at:
        row.started_at = datetime.now()
    await db.commit()
    return _to_out(row)


def _infer_position(row: InterviewSessionRow) -> str:
    """从简历 + JD 文本推断岗位名（简化版：优先取简历的 position_guess）。"""
    import json

    # 1. 简历已解析出 position_guess
    if row.resume_parsed_json:
        try:
            data = json.loads(row.resume_parsed_json)
            guess = data.get("position_guess", "")
            if guess:
                return guess
        except Exception:
            pass

    # 2. 从 JD 第一行或前 50 字提取（粗略）
    if row.jd_content:
        first_line = row.jd_content.strip().split("\n")[0][:50]
        return first_line if first_line else ""

    return ""


async def generate_next(
    db: AsyncSession,
    sid: str,
    llm_api: Any | None = None,
) -> str:
    """生成下一句话（基于场景包 + 当前阶段与历史）。

    分发规则：非面试场景走 scenarios 包的 prompt_builder；
    面试场景回落到原有 interview/prompts.py 逻辑，行为不变。

    Args:
        llm_api: 可注入的 LLM 调用函数，用于解耦测试。不传则从配置加载。

    Returns:
        AI 的下一句话。
    """
    row = await _load_row(db, sid)
    if row.status != "in_progress":
        raise InterviewError(f"会话不在进行中：{row.status}")

    dialogues = await _load_dialogues(db, sid)
    history = [{"role": d.role, "text": d.text} for d in dialogues]
    resume = json.loads(row.resume_parsed_json) if row.resume_parsed_json else None

    pack = _pack(row)
    if pack.key == "interview":
        stage = Stage(row.current_stage)
        messages = build_messages(
            stage,
            position=row.position,
            level=row.level,
            resume=resume,
            dialogues=history,
            style=row.style,
            company=row.company,
            jd_content=row.jd_content,
        )
        stage_key = stage.value
    else:
        from app.modules.scenarios.base import ScenarioContext
        stage_def = _get_stage_def(pack, row.current_stage)
        ctx = ScenarioContext(
            position=row.position,
            level=row.level,
            company=row.company,
            jd_content=row.jd_content,
            duration_limit=row.duration_limit or 0,
            resume=resume,
            material_text=row.material_text or "",
            history=history,
        )
        messages = stage_def.prompt_builder(ctx)
        stage_key = stage_def.key

    if llm_api is None:
        from app.modules.config import load_provider_config
        cfg = await load_provider_config(db, "llm")
        provider = get_llm(cfg)
        text = await provider.chat(messages, temperature=0.7)
    else:
        text = await llm_api(messages)

    if not isinstance(text, str):
        raise InterviewError("LLM 返回非字符串")

    # 持久化 AI 消息
    seq = len(dialogues) + 1
    db.add(
        InterviewDialogueRow(
            id=str(uuid.uuid4()),
            session_id=sid,
            seq=seq,
            role="ai",
            stage=stage_key,
            text=text,
        )
    )
    await db.commit()
    return text


async def advance_stage(db: AsyncSession, sid: str) -> InterviewSessionOut:
    """推进到下一阶段（按场景包的 stages 顺序）。"""
    row = await _load_row(db, sid)
    pack = _pack(row)
    keys = [s.key for s in pack.stages]
    if row.current_stage in keys:
        idx = keys.index(row.current_stage)
        nxt_key = keys[idx + 1] if idx + 1 < len(keys) else "report"
    else:
        nxt_key = keys[0]
    row.current_stage = nxt_key
    if nxt_key == "report":
        row.status = "completed"
    await db.commit()
    return _to_out(row)


async def should_advance(
    db: AsyncSession,
    sid: str,
) -> bool:
    """检查当前阶段问题数是否已达上限，需要推进。"""
    row = await _load_row(db, sid)
    cur_key = row.current_stage
    pack = _pack(row)
    if pack.key == "interview":
        cur = Stage(cur_key)
        limit = STAGE_QUESTION_LIMIT.get(cur, 1)
    else:
        limit = _get_stage_def(pack, cur_key).question_limit
    if limit == 0:
        return False
    res = await db.execute(
        select(InterviewDialogueRow)
        .where(InterviewDialogueRow.session_id == sid)
        .where(InterviewDialogueRow.role == "ai")
        .where(InterviewDialogueRow.stage == cur_key)
    )
    ai_count = len(res.scalars().all())
    return ai_count >= limit


async def save_user_message(
    db: AsyncSession,
    sid: str,
    text: str,
    analysis: dict[str, Any] | None = None,
) -> None:
    """保存候选人回答。"""
    row = await _load_row(db, sid)
    res = await db.execute(
        select(InterviewDialogueRow)
        .where(InterviewDialogueRow.session_id == sid)
        .order_by(InterviewDialogueRow.seq.desc())
        .limit(1)
    )
    last = res.scalars().first()
    seq = (last.seq + 1) if last else 1

    db.add(
        InterviewDialogueRow(
            id=str(uuid.uuid4()),
            session_id=sid,
            seq=seq,
            role="user",
            stage=row.current_stage,
            text=text,
            analysis_json=json.dumps(analysis, ensure_ascii=False) if analysis else "",
        )
    )
    await db.commit()


async def list_dialogues(db: AsyncSession, sid: str) -> list[dict]:
    res = await db.execute(
        select(InterviewDialogueRow)
        .where(InterviewDialogueRow.session_id == sid)
        .order_by(InterviewDialogueRow.seq.asc())
    )
    return [
        {
            "id": d.id,
            "seq": d.seq,
            "role": d.role,
            "stage": d.stage,
            "text": d.text,
            "audio_url": d.audio_url,
        }
        for d in res.scalars().all()
    ]


# ---- 内部 ----

def _get_stage_def(pack, stage_key: str):
    """从场景包中取阶段定义，找不到抛错。"""
    for s in pack.stages:
        if s.key == stage_key:
            return s
    raise InterviewError(f"场景 {pack.key} 不存在阶段：{stage_key}")


async def _load_row(db: AsyncSession, sid: str) -> InterviewSessionRow:
    res = await db.execute(select(InterviewSessionRow).where(InterviewSessionRow.id == sid))
    row = res.scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"面试会话不存在：{sid}")
    return row


async def _load_dialogues(db: AsyncSession, sid: str) -> list[InterviewDialogueRow]:
    res = await db.execute(
        select(InterviewDialogueRow)
        .where(InterviewDialogueRow.session_id == sid)
        .order_by(InterviewDialogueRow.seq.asc())
    )
    return list(res.scalars().all())


def _to_out(row: InterviewSessionRow) -> InterviewSessionOut:
    resume_parsed = None
    if row.resume_parsed_json:
        try:
            resume_parsed = ResumeStructured(**json.loads(row.resume_parsed_json))
        except Exception:
            resume_parsed = None
    return InterviewSessionOut(
        id=row.id,
        scenario=row.scenario or "interview",
        position=row.position,
        level=row.level,
        style=row.style,
        company=row.company,
        jd_url=row.jd_url,
        jd_content=row.jd_content,
        status=row.status,
        current_stage=row.current_stage,
        has_resume=bool(row.resume_parsed_json),
        resume_parsed=resume_parsed,
        material_file=row.material_file or "",
        has_material=bool(row.material_text),
        duration_limit=row.duration_limit or 0,
        started_at=row.started_at,
    )
