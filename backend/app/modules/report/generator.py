"""面试报告生成器。"""

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
from app.providers import get_llm

logger = logging.getLogger(__name__)


async def generate_report(db: AsyncSession, sid: str) -> dict[str, Any]:
    """生成完整面试报告。"""
    # 加载会话与对话
    sres = await db.execute(select(InterviewSessionRow).where(InterviewSessionRow.id == sid))
    session = sres.scalar_one_or_none()
    if not session:
        raise NotFoundError(f"会话不存在：{sid}")

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

    # 语速（估算：用户消息总字数 / 估算时长；MVP 简化）
    est_duration = max(60.0, total_words / 3.0)  # 平均 3 字/秒
    speech_rate = compute_speech_rate(all_text, est_duration)

    # 情绪综合
    text_res = analyze_text(all_text)
    emotion_overall = analyze_emotion(text_res, None)

    # 调 LLM 生成内容评分 + 建议
    llm_summary = await _llm_evaluate(db, session, dialogues)

    report = {
        "session_id": sid,
        "position": session.position,
        "level": session.level,
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
    session.resume_parsed_json = session.resume_parsed_json  # noqa
    session.status = "completed"
    session.current_stage = "report"
    await db.commit()

    return report


async def _llm_evaluate(
    db: AsyncSession,
    session: InterviewSessionRow,
    dialogues: list[InterviewDialogueRow],
) -> dict[str, Any]:
    """调用 LLM 对回答内容做评估并生成建议。"""
    dialogue_text = "\n".join(
        [f"{'面试官' if d.role == 'ai' else '候选人'}：{d.text}" for d in dialogues]
    )

    prompt = f"""你是一名资深面试官，请基于以下面试记录，对候选人的表现给出评估。

岗位：{session.position}
职级：{session.level}

面试记录：
{dialogue_text[:6000]}

请返回严格的 JSON，字段如下：
{{
  "overall_score": 0-100 的整数,
  "summary": "一句话总评",
  "content_metrics": {{
    "project_familiarity": {{"score": 0-100, "feedback": "..."}},
    "logicality": {{"score": 0-100, "feedback": "..."}},
    "completeness": {{"score": 0-100, "feedback": "..."}}
  }},
  "suggestions": {{
    "short_term": ["建议1", "建议2"],
    "mid_term": ["建议1", "建议2"]
  }}
}}

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
