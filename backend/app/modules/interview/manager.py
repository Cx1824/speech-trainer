"""面试会话管理器。

负责：
- 创建/加载会话
- 状态机推进
- 调用 LLM 生成下一题
- 持久化对话
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InterviewError, NotFoundError, ProviderError
from app.models.interview import InterviewDialogueRow, InterviewReportRow, InterviewSessionRow
from app.modules.interview import planner
from app.modules.interview.prompts import build_messages, build_planned_messages
from app.modules.interview.stages import STAGE_QUESTION_LIMIT, Stage, next_stage
from app.providers import get_llm
from app.schemas import InterviewConfigIn, InterviewSessionOut, ResumeStructured

logger = logging.getLogger(__name__)

QUESTION_MAX_TOKENS = 320
QUESTION_READ_TIMEOUT_SECONDS = 15.0


def _fallback_interview_question(item: dict[str, Any], is_followup: bool) -> str:
    """LLM 暂时不可用时维持训练连续性，不猜测用户经历。"""
    label = str(item.get("label") or "当前方向").strip()
    if is_followup:
        return f"关于“{label}”，请再补充一个刚才没有提到的具体事实或例子。"
    return f"接下来进入“{label}”。请围绕这个方向具体回答，并说明你的判断依据。"


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
        interview_mode=planner.normalize_mode(config.interview_mode),
        interview_intensity=planner.normalize_intensity(config.interview_intensity),
        source_session_id=config.source_session_id or "",
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


async def save_material(
    db: AsyncSession,
    sid: str,
    material_file: str,
    material_text: str,
) -> InterviewSessionOut:
    """保存汇报/演讲材料。"""
    row = await _load_row(db, sid)
    row.material_file = material_file
    row.material_text = material_text
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
    interview_mode: str | None = None,
    interview_intensity: str | None = None,
    source_session_id: str | None = None,
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
    if interview_mode is not None:
        row.interview_mode = planner.normalize_mode(interview_mode)
    if interview_intensity is not None:
        row.interview_intensity = planner.normalize_intensity(interview_intensity)
    if source_session_id is not None:
        row.source_session_id = source_session_id
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
    if row.scenario == "interview" and not planner.load_plan(row.interview_plan_json):
        weakness_focus = await _load_weakness_focus(db, row)
        plan = planner.build_plan(
            row.interview_mode or "full",
            row.interview_intensity or "standard",
            weakness_focus=weakness_focus,
        )
        row.interview_plan_json = planner.dump_plan(plan)
        first_item = planner.current_item(plan)
        row.current_stage = first_item["stage"] if first_item else "report"
    elif row.scenario != "interview":
        row.current_stage = Stage.OPENING.value
    if not row.started_at:
        row.started_at = datetime.now()
    await db.commit()
    return _to_out(row)


async def complete_interview(db: AsyncSession, sid: str) -> InterviewSessionOut:
    """幂等完成训练会话。

    语音链路必须先保存尚未提交的回答，再调用本函数。HTTP 结束接口也复用
    这里，避免 WebSocket 与 REST 各自维护一套容易漂移的完成逻辑。
    """
    row = await _load_row(db, sid)
    if row.scenario == "interview":
        plan = planner.load_plan(row.interview_plan_json)
        if plan:
            dialogues = await _load_dialogues(db, sid)
            if dialogues and dialogues[-1].role == "user":
                current = planner.current_item(plan)
                covered = plan["state"]["covered_item_ids"]
                if current and current["id"] not in covered:
                    covered.append(current["id"])
                row.interview_plan_json = planner.dump_plan(plan)
    row.status = "completed"
    row.current_stage = "report"
    await db.commit()
    await db.refresh(row)
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
        plan = planner.load_plan(row.interview_plan_json)
        if plan:
            item = planner.current_item(plan)
            if item is None:
                raise InterviewError("面试计划已完成")
            is_followup = planner.is_followup_turn(plan, item)
            question_candidate = (
                None
                if is_followup
                else await _select_question_candidate(db, row, plan, item)
            )
            plan_progress = planner.progress(plan)
            messages = build_planned_messages(
                position=row.position,
                level=row.level,
                resume=resume,
                dialogues=history,
                style=row.style,
                company=row.company,
                jd_content=row.jd_content,
                item=item,
                is_followup=is_followup,
                covered_labels=plan_progress["covered_labels"],
                remaining_labels=plan_progress["remaining_labels"],
                question_candidate=question_candidate,
            )
            stage_key = item["stage"]
        else:
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
        options: dict[str, Any] = {
            "temperature": 0.7,
            "max_tokens": QUESTION_MAX_TOKENS,
            "read_timeout": QUESTION_READ_TIMEOUT_SECONDS,
        }
        if cfg.provider == "deepseek":
            options["thinking"] = False
        started = time.monotonic()
        try:
            text = await provider.chat(messages, **options)
            logger.info("训练问题生成完成：%.2f 秒", time.monotonic() - started)
        except ProviderError as exc:
            if pack.key != "interview" or not plan or item is None:
                raise
            logger.warning(
                "训练问题生成失败，使用本地兜底问题：error_type=%s elapsed=%.2f",
                type(exc).__name__,
                time.monotonic() - started,
            )
            text = _fallback_interview_question(item, is_followup)
    else:
        text = await llm_api(messages)

    if not isinstance(text, str):
        raise InterviewError("LLM 返回非字符串")

    if pack.key == "interview" and plan:
        planner.record_question(
            plan,
            item,
            question_bank_id=str((question_candidate or {}).get("id") or ""),
        )
        row.current_stage = stage_key
        row.interview_plan_json = planner.dump_plan(plan)

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
    if pack.key == "interview":
        plan = planner.load_plan(row.interview_plan_json)
        if plan:
            next_item = planner.advance(plan)
            row.interview_plan_json = planner.dump_plan(plan)
            if next_item is None:
                row.current_stage = "report"
                row.status = "completed"
            else:
                row.current_stage = next_item["stage"]
            await db.commit()
            return _to_out(row)
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
        plan = planner.load_plan(row.interview_plan_json)
        if plan:
            dialogues = await _load_dialogues(db, sid)
            latest_answer = dialogues[-1].text if dialogues and dialogues[-1].role == "user" else ""
            return planner.should_advance(plan, latest_answer)
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


async def get_interview_progress(db: AsyncSession, sid: str) -> dict[str, Any] | None:
    row = await _load_row(db, sid)
    if row.scenario != "interview":
        return None
    plan = planner.load_plan(row.interview_plan_json)
    return planner.progress(plan) if plan else None


async def skip_interview_item(db: AsyncSession, sid: str) -> InterviewSessionOut:
    """跳过当前能力方向且不把它标记为已覆盖。"""
    row = await _load_row(db, sid)
    if row.scenario != "interview":
        raise InterviewError("当前场景不支持换题")
    plan = planner.load_plan(row.interview_plan_json)
    if not plan:
        raise InterviewError("当前面试没有可用的覆盖计划")
    next_item = planner.advance(plan, mark_covered=False)
    row.interview_plan_json = planner.dump_plan(plan)
    if next_item is None:
        row.current_stage = "report"
        row.status = "completed"
    else:
        row.current_stage = next_item["stage"]
    await db.commit()
    return _to_out(row)


# ---- 内部 ----

def _get_stage_def(pack, stage_key: str):
    """从场景包中取阶段定义，找不到抛错。"""
    for s in pack.stages:
        if s.key == stage_key:
            return s
    raise InterviewError(f"场景 {pack.key} 不存在阶段：{stage_key}")


async def _select_question_candidate(
    db: AsyncSession,
    row: InterviewSessionRow,
    plan: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any] | None:
    """从用户岗位题库选择尚未使用且意图匹配的一题。"""
    from app.modules.question_bank import get_questions

    questions = await get_questions(db, row.position)
    used = set(plan["state"].get("used_question_bank_ids") or [])
    available = [question for question in questions if str(question.get("id") or "") not in used]
    if not available:
        return None
    intent = item.get("intent", "")
    label = item.get("label", "")
    matched = [
        question for question in available
        if question.get("intent") in {intent, label, item.get("stage")}
    ]
    return matched[0] if matched else None


async def _load_weakness_focus(
    db: AsyncSession,
    row: InterviewSessionRow,
) -> list[dict[str, str]] | None:
    if row.interview_mode != "weakness":
        return None
    query = select(InterviewReportRow)
    if row.source_session_id:
        query = query.where(InterviewReportRow.session_id == row.source_session_id)
    else:
        query = (
            query.join(InterviewSessionRow, InterviewReportRow.session_id == InterviewSessionRow.id)
            .where(InterviewSessionRow.scenario == "interview")
            .where(InterviewSessionRow.id != row.id)
        )
    result = await db.execute(query.order_by(InterviewReportRow.created_at.desc()).limit(1))
    report_row = result.scalar_one_or_none()
    if report_row is None:
        return None
    try:
        report = json.loads(report_row.report_json)
    except (TypeError, json.JSONDecodeError):
        return None
    goals = {
        "response_structure": ("回答结构", "围绕薄弱回答练习先直接作答，再用背景、行动和结果形成完整结构。"),
        "evidence_results": ("案例证据", "围绕薄弱回答补充本人行动、范围、结果和可核查依据。"),
        "job_relevance": ("岗位匹配", "围绕薄弱回答明确说明经历如何迁移到目标岗位。"),
        "followup_response": ("追问回应", "围绕薄弱回答练习补充新证据、澄清边界并保持前后一致。"),
        "continuity": ("表达连贯", "用短句和明确连接关系重答一个岗位问题，减少口癖和局部重复。"),
        "pacing": ("语速与停顿", "在完整回答中练习稳定语速和有意义的结构停顿。"),
    }
    axes = [axis for axis in report.get("axes", []) if isinstance(axis, dict) and axis.get("score") is not None]
    axes.sort(key=lambda axis: float(axis.get("score") or 0))
    focus = []
    for axis in axes:
        if axis.get("key") in goals:
            label, goal = goals[axis["key"]]
            focus.append({"label": label, "goal": goal})
    return focus[:4] or None


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
    plan = planner.load_plan(row.interview_plan_json) if row.scenario == "interview" else None
    return InterviewSessionOut(
        id=row.id,
        scenario=row.scenario or "interview",
        position=row.position,
        level=row.level,
        style=row.style,
        interview_mode=row.interview_mode or "full",
        interview_intensity=row.interview_intensity or "standard",
        interview_progress=planner.progress(plan) if plan else None,
        source_session_id=row.source_session_id or "",
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
