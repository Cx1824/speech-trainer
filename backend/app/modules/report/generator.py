"""训练报告生成器（三场景通用）。

按会话的 scenario 取场景包：
- 统计部分（表达/情绪/对话）全场景复用
- LLM 评估的维度与专业建议按场景包的 report_focus 定制
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.interview import InterviewDialogueRow, InterviewSessionRow
from app.modules.analysis import analyze_text, compute_speech_rate, rate_speech_rate
from app.modules.analysis.emotion import analyze_emotion
from app.modules.interview.styles import get_style
from app.modules.scenarios import get_pack
from app.providers import get_llm

logger = logging.getLogger(__name__)


async def generate_report(db: AsyncSession, sid: str) -> dict[str, Any]:
    """生成完整训练报告。"""
    # 加载会话与对话
    sres = await db.execute(select(InterviewSessionRow).where(InterviewSessionRow.id == sid))
    session = sres.scalar_one_or_none()
    if not session:
        raise NotFoundError(f"会话不存在：{sid}")

    pack = get_pack(session.scenario)

    dres = await db.execute(
        select(InterviewDialogueRow)
        .where(InterviewDialogueRow.session_id == sid)
        .order_by(InterviewDialogueRow.seq.asc())
    )
    dialogues = list(dres.scalars().all())

    # 提取所有用户回答
    user_texts = [d.text for d in dialogues if d.role == "user"]
    all_text = "".join(user_texts)

    # 表达维度统计
    total_words = sum(len(t) for t in user_texts)
    filler_counter: dict[str, int] = {}
    for t in user_texts:
        res = analyze_text(t)
        for hit in res.filler_hits:
            filler_counter[hit["word"]] = filler_counter.get(hit["word"], 0) + hit["count"]
    filler_top = sorted(filler_counter.items(), key=lambda x: -x[1])[:5]

    # 语速：限时场景用真实用时，其他场景按字数估算
    est_duration = _estimate_duration(session, total_words)
    speech_rate = compute_speech_rate(all_text, est_duration)

    # 情绪综合
    text_res = analyze_text(all_text)
    emotion_overall = analyze_emotion(text_res, None)

    # 调 LLM 生成内容评分 + 建议（按场景）
    llm_summary = await _llm_evaluate(db, session, dialogues, pack)

    report = {
        "session_id": sid,
        "scenario": pack.key,
        "scenario_name": pack.name,
        "position": session.position,
        "level": session.level,
        "duration_limit": session.duration_limit or 0,
        "elapsed_minutes": round(est_duration / 60.0, 1),
        "overall_score": llm_summary.get("overall_score", 70),
        "summary": llm_summary.get("summary", ""),
        "expression_metrics": {
            "speech_rate": speech_rate,
            "speech_rate_level": rate_speech_rate(speech_rate),
            "total_words": total_words,
            "filler_total": sum(filler_counter.values()),
            "filler_top": [{"word": w, "count": c} for w, c in filler_top],
            "repetition_rate": text_res.repetition_rate,
        },
        "content_metrics": llm_summary.get("content_metrics", {}),
        "emotion_metrics": {
            "tension_score": emotion_overall.tension_score,
            "tension_level": emotion_overall.tension_level,
            "confidence_score": emotion_overall.confidence_score,
            "confidence_level": emotion_overall.confidence_level,
        },
        "suggestions": llm_summary.get("suggestions", {"short_term": [], "mid_term": []}),
        "professional_advice": llm_summary.get("professional_advice", []),
        "dialogues": [
            {
                "seq": d.seq,
                "role": d.role,
                "stage": d.stage,
                "text": d.text,
            }
            for d in dialogues
        ],
    }

    # 落库
    session.status = "completed"
    session.current_stage = "report"
    await db.commit()

    return report


def _estimate_duration(session: InterviewSessionRow, total_words: int) -> float:
    """估算实际用时（秒）：优先 started_at 真实值，回落字数估算。"""
    if session.started_at:
        from datetime import datetime

        elapsed = (datetime.now() - session.started_at).total_seconds()
        if elapsed > 30:  # 过短的会话不可信，回落估算
            return elapsed
    return max(60.0, total_words / 3.0)  # 平均 3 字/秒


async def _llm_evaluate(
    db: AsyncSession,
    session: InterviewSessionRow,
    dialogues: list[InterviewDialogueRow],
    pack,
) -> dict[str, Any]:
    """调用 LLM 对内容做评估并生成建议（按场景包定制维度与建议框架）。"""
    dialogue_text = "\n".join(
        [f"{pack.role_name if d.role == 'ai' else '我'}：{d.text}" for d in dialogues]
    )

    # 按场景定制：身份、content_metrics 维度、专业建议指引
    if pack.key == "presentation":
        persona = "你是一名资深的管理咨询顾问，擅长评估工作汇报与述职表现。"
        metrics_def = """{
    "structure": {"score": 0-100, "feedback": "金字塔结构/结论先行"},
    "data_support": {"score": 0-100, "feedback": "数据与量化支撑"},
    "qa_handling": {"score": 0-100, "feedback": "质询应对质量"}
  }"""
    elif pack.key == "speech":
        persona = "你是一名资深演讲教练，曾指导过多场大型公开演讲。"
        metrics_def = """{
    "engagement": {"score": 0-100, "feedback": "感染力与观众连接"},
    "pacing": {"score": 0-100, "feedback": "节奏与停顿"},
    "structure": {"score": 0-100, "feedback": "开场-主体-结尾的结构设计"}
  }"""
    else:  # interview
        persona = "你是一名资深面试官。"
        metrics_def = """{
    "project_familiarity": {"score": 0-100, "feedback": "..."},
    "logicality": {"score": 0-100, "feedback": "..."},
    "completeness": {"score": 0-100, "feedback": "..."}
  }"""

    advice_guide = "\n".join([f"- {s}" for s in pack.report_focus.advice_sections])

    duration_note = (
        f"预设时长：{session.duration_limit} 分钟（请评估时间掌控）"
        if pack.timed and session.duration_limit
        else ""
    )

    prompt = f"""{persona}请基于以下{pack.name}训练记录，对表现给出评估。

主题/岗位：{session.position}
{duration_note}

训练记录：
{dialogue_text[:6000]}

请返回严格的 JSON，字段如下：
{{
  "overall_score": 0-100 的整数,
  "summary": "一句话总评",
  "content_metrics": {metrics_def},
  "suggestions": {{
    "short_term": ["建议1", "建议2"],
    "mid_term": ["建议1", "建议2"]
  }},
  "professional_advice": [
    {{"topic": "章节名", "detail": "该专业维度的具体分析与建议（2-4句）"}}
  ]
}}

professional_advice 必须覆盖以下 {pack.name} 专业维度，每个维度一条：
{advice_guide}

只返回 JSON，不要解释。"""

    try:
        from app.modules.config import load_provider_config
        cfg = await load_provider_config(db, "llm")
        provider = get_llm(cfg)
        style_profile = get_style(session.style)
        raw = await provider.chat(
            [
                {"role": "system", "content": style_profile.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return _safe_json(raw)
    except Exception as e:
        logger.exception("LLM 报告评估失败")
        return {"summary": f"自动评估失败：{e}", "overall_score": 70}


def _safe_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        return json.loads(cleaned)
    except Exception:
        return {"summary": "评估结果解析失败", "overall_score": 70}
