"""训练报告生成器（三场景通用）。

共享分析层先产出可观察事实，随后按 ``ScenarioPack.evaluation`` 应用场景评价。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.interview import (
    InterviewDialogueRow,
    InterviewReportRow,
    InterviewSessionRow,
)
from app.modules.analysis import analyze_text, compute_speech_rate, rate_speech_rate
from app.modules.analysis.emotion import analyze_emotion
from app.modules.report.scoring import (
    apply_score_gates,
    compose_total,
    score_coverage,
    score_continuity,
    score_pacing,
)
from app.modules.report.voice_reference import build_voice_reference
from app.modules.interview import planner as interview_planner
from app.modules.scenarios import get_pack
from app.providers import get_llm

logger = logging.getLogger(__name__)

REPORT_LLM_MAX_TOKENS = 4_096
REPORT_LLM_READ_TIMEOUT_SECONDS = 120.0

ANALYSIS_VERSION = "speech-signals-v5"
_REPORT_LOCKS: dict[str, asyncio.Lock] = {}


async def get_report(db: AsyncSession, sid: str) -> dict[str, Any]:
    """读取最新报告快照；读取操作不会触发模型调用。"""
    row = await _latest_report_row(db, sid)
    if row is None:
        raise NotFoundError(f"报告尚未生成：{sid}")
    return _decode_report(row)


async def generate_report(
    db: AsyncSession,
    sid: str,
    *,
    regenerate: bool = False,
) -> dict[str, Any]:
    """幂等生成报告；只有显式 ``regenerate`` 才创建新版本。"""
    lock = _REPORT_LOCKS.setdefault(sid, asyncio.Lock())
    async with lock:
        latest = await _latest_report_row(db, sid)
        if latest is not None and not regenerate:
            return _decode_report(latest)

        report = await _build_report(db, sid)
        version = latest.version + 1 if latest is not None else 1
        report["report_version"] = version
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        db.add(
            InterviewReportRow(
                id=str(uuid.uuid4()),
                session_id=sid,
                version=version,
                analysis_version=report["analysis_version"],
                rubric_version=report["rubric_version"],
                report_json=json.dumps(report, ensure_ascii=False),
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # 多进程同时首次生成时，数据库唯一约束决定唯一快照；
            # 失败方回读胜出的版本，保证调用者看到完全相同的报告。
            await db.rollback()
            winner = await _latest_report_row(db, sid)
            if winner is not None:
                return _decode_report(winner)
            raise
        return report


async def _latest_report_row(
    db: AsyncSession,
    sid: str,
) -> InterviewReportRow | None:
    result = await db.execute(
        select(InterviewReportRow)
        .where(InterviewReportRow.session_id == sid)
        .order_by(InterviewReportRow.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _decode_report(row: InterviewReportRow) -> dict[str, Any]:
    report = json.loads(row.report_json)
    if not isinstance(report, dict):
        raise ValueError(f"报告快照格式无效：{row.id}")
    # v4 之前的快照没有关键任务约束字段；读时补空数组，避免旧报告报错。
    report.setdefault("score_constraints", [])
    return report


async def _build_report(db: AsyncSession, sid: str) -> dict[str, Any]:
    """基于当前训练数据构建一个新的完整报告。"""
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

    # 表达维度统计：连贯性只使用上下文口癖与局部重复。
    all_res = _aggregate_text_analysis(user_texts)
    total_chars = _count_chars(all_text)
    filler_weighted = sum(h["weight"] * h["count"] for h in all_res.filler_hits)
    filler_counter = {h["word"]: h["count"] for h in all_res.filler_hits}
    filler_top = sorted(filler_counter.items(), key=lambda x: -x[1])[:5]
    # 语速只使用持久化的真实发言时长。旧会话没有时长时明确返回未知，
    # 不再用固定字速反推一个看似精确的结果。
    speech_duration = _aggregate_speech_duration(dialogues)
    speech_rate = (
        compute_speech_rate(all_text, speech_duration)
        if speech_duration is not None
        else 0.0
    )
    elapsed_duration = _elapsed_duration(session)

    # 表达信号综合：优先聚合逐句落库的声学事实与个人基线结果，
    # 避免从全文措辞反推声音状态，对朗读型场景造成系统性偏差。
    emotion_overall = _aggregate_emotion(dialogues, all_text)

    # 声音事实保留用于复现和后续个人基线研究，但不再把 jitter、短停顿、
    # 语速合并成单一“稳定/紧张”分。真人同文盲听已证明它们不是同一维度。
    avg_jitter = _aggregate_jitter(dialogues)
    hesitation_count = _aggregate_analysis_count(dialogues, "hesitation_count")
    long_pause_count = _aggregate_analysis_count(dialogues, "pause_count")
    hesitation_rate = (
        round(hesitation_count / speech_duration * 60, 1)
        if hesitation_count is not None and speech_duration
        else None
    )
    long_pause_rate = (
        round(long_pause_count / speech_duration * 60, 1)
        if long_pause_count is not None and speech_duration
        else None
    )

    # 信号证据包（喂给 LLM 归因，替代凭空点评）
    signal_evidence = _build_signal_evidence(
        speech_rate=speech_rate,
        filler_top=filler_top,
        repetition_rate=all_res.repetition_rate,
        avg_jitter=avg_jitter,
        hesitation_count=hesitation_count,
        hesitation_rate=hesitation_rate,
        long_pause_count=long_pause_count,
        long_pause_rate=long_pause_rate,
        expression_breaks=all_res.expression_breaks,
    )

    # 没有有效用户发言时不调用 LLM，避免对空记录生成虚构评价。
    llm_summary = (
        await _llm_evaluate(db, session, dialogues, pack, signal_evidence)
        if total_chars > 0
        else {"summary": "有效发言不足，暂不能生成完整评价。"}
    )

    # ---- 共享信号定分 + 场景评价 ----
    signal_scores: dict[str, float | None] = {
        "continuity": score_continuity(
            filler_weighted,
            total_chars,
            all_res.repetition_rate,
            break_events=all_res.expression_breaks,
        ),
        "pacing": score_pacing(
            speech_rate,
            pause_rate=hesitation_rate,
            long_pause_rate=long_pause_rate,
            break_events=all_res.expression_breaks,
        ),
    }
    voice_reference = build_voice_reference(
        voice_signal=getattr(emotion_overall, "voice_signal", False),
        pitch_jitter=avg_jitter,
        speech_duration=speech_duration,
        total_chars=total_chars,
        speech_rate=speech_rate,
        speech_rate_level=rate_speech_rate(speech_rate),
        filler_total=sum(filler_counter.values()),
        repetition_rate=all_res.repetition_rate,
        expression_break_count=len(all_res.expression_breaks),
        continuity_score=signal_scores["continuity"],
        pacing_score=signal_scores["pacing"],
    )
    axis_scores: dict[str, float | None] = {}
    for axis in pack.evaluation.axes:
        axis_scores[axis.key] = (
            signal_scores.get(axis.signal_key)
            if axis.source == "signal"
            else _axis_from_llm(llm_summary, axis.key, axis.min_evidence)
        )

    weights = {axis.key: axis.weight for axis in pack.evaluation.axes}
    coverage = score_coverage(axis_scores, weights)
    # “综合评分”必须覆盖完整评价维度。缺失轴通常意味着语义模型或
    # 声音分析未完成；对剩余轴重新归一化会把局部分数伪装成总分。
    uncapped_score = compose_total(axis_scores, weights) if coverage == 1.0 else None
    overall_score, score_constraints = apply_score_gates(
        uncapped_score,
        axis_scores,
        pack.evaluation.score_gates,
    )

    signal_details = _build_signal_axis_details(
        scores=signal_scores,
        total_chars=total_chars,
        filler_total=sum(filler_counter.values()),
        repetition_rate=all_res.repetition_rate,
        expression_break_count=len(all_res.expression_breaks),
        speech_rate=speech_rate,
        speech_duration=speech_duration,
        hesitation_count=hesitation_count,
        hesitation_rate=hesitation_rate,
        long_pause_count=long_pause_count,
        long_pause_rate=long_pause_rate,
    )

    axes = []
    for axis in pack.evaluation.axes:
        entry: dict[str, Any] = {
            "key": axis.key,
            "label": axis.label,
            "description": axis.description,
            "score": (
                round(axis_scores[axis.key], 1)
                if axis_scores[axis.key] is not None
                else None
            ),
            "weight": axis.weight,
            "source": axis.source,
        }
        if axis.source == "llm":
            raw_detail = llm_summary.get(axis.key)
            detail = raw_detail if isinstance(raw_detail, dict) else {}
            evidence = detail.get("evidence")
            valid_evidence = (
                [item.strip() for item in evidence if isinstance(item, str) and item.strip()]
                if isinstance(evidence, list)
                else []
            )
            entry.update(
                {
                    "feedback": (
                        detail.get("feedback", "")
                        if isinstance(detail.get("feedback", ""), str)
                        else ""
                    ),
                    "evidence": valid_evidence,
                }
            )
        elif axis.signal_key:
            detail = signal_details.get(axis.signal_key, {})
            entry.update(
                {
                    "feedback": detail.get("feedback", ""),
                    "evidence": detail.get("evidence", []),
                }
            )
        axes.append(entry)

    interview_coverage = None
    if pack.key == "interview":
        interview_plan = interview_planner.load_plan(session.interview_plan_json)
        if interview_plan:
            interview_coverage = interview_planner.progress(interview_plan)

    report = {
        "session_id": sid,
        "scenario": pack.key,
        "scenario_name": pack.name,
        "position": session.position,
        "level": session.level,
        "duration_limit": session.duration_limit or 0,
        "elapsed_minutes": round(elapsed_duration / 60.0, 1),
        "analysis_version": ANALYSIS_VERSION,
        "rubric_version": pack.evaluation.version,
        "overall_score": overall_score,
        "score_constraints": score_constraints,
        "score_coverage": coverage,
        "interview_coverage": interview_coverage,
        "sample_state": _sample_state(
            total_chars=total_chars,
            speech_duration=speech_duration,
            calibrated=getattr(emotion_overall, "calibrated", False),
        ),
        "summary": llm_summary.get("summary", ""),
        "axes": axes,
        "expression_metrics": {
            "speech_rate": speech_rate,
            "speech_rate_level": rate_speech_rate(speech_rate),
            "speech_duration_sec": speech_duration,
            "duration_source": "voice" if speech_duration is not None else "unavailable",
            # 中文表达以有效汉字计数；字段名为兼容既有前端与已生成报告保留。
            "total_words": total_chars,
            "filler_total": sum(filler_counter.values()),
            "filler_top": [{"word": w, "count": c} for w, c in filler_top],
            "repetition_rate": all_res.repetition_rate,
            "expression_break_count": len(all_res.expression_breaks),
            "expression_break_examples": [
                {
                    "excerpt": event.get("excerpt", ""),
                    "description": event.get("description", ""),
                }
                for event in _select_expression_breaks(all_res.expression_breaks)
            ],
            "short_pause_count": hesitation_count,
            "short_pause_rate": hesitation_rate,
            "long_pause_count": long_pause_count,
            "long_pause_rate": long_pause_rate,
        },
        "delivery_metrics": {
            # 旧字段保留结构兼容，但不再生成组合分。
            "stability_score": None,
            "calibrated": getattr(emotion_overall, "calibrated", False),
            "voice_signal": getattr(emotion_overall, "voice_signal", False),
            "pitch_jitter": round(avg_jitter, 4) if avg_jitter is not None else None,
            "note": "声音波动仅作为实验事实记录，暂不合并为稳定或紧张评分",
        },
        "voice_reference": voice_reference,
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

    # 报告快照由 generate_report 与会话状态在同一事务中落库。
    session.status = "completed"
    session.current_stage = "report"

    return report


def _count_chars(text: str) -> int:
    import re
    return len(re.findall(r"[\u4e00-\u9fa5]", text))


def _aggregate_text_analysis(user_texts: list[str]):
    """逐轮分析后再聚合，避免跨回答边界制造虚假的紧邻重启。"""
    combined = analyze_text("".join(user_texts))
    per_turn = [analyze_text(text) for text in user_texts if text.strip()]
    total_bigrams = sum(result.word_count for result in per_turn)
    combined.repetition_rate = (
        sum(result.repetition_rate * result.word_count for result in per_turn)
        / total_bigrams
        if total_bigrams > 0
        else 0.0
    )
    combined.expression_breaks = []
    for turn_index, result in enumerate(per_turn):
        for event in result.expression_breaks:
            copied = dict(event)
            copied["event_id"] = f"turn-{turn_index}:{event.get('event_id', '')}"
            combined.expression_breaks.append(copied)
    return combined


def _select_expression_breaks(events: list[dict], limit: int = 3) -> list[dict]:
    """优先展示改口与较重断裂，使报告证据能解释分数。"""
    kind_order = {
        "self_correction": 0,
        "fragmented_clause": 1,
        "unfinished_clause": 2,
        "consecutive_repeat": 3,
    }
    return sorted(
        events,
        key=lambda event: (
            kind_order.get(str(event.get("kind")), 4),
            -float(event.get("weight", 0.0)),
            int(event.get("start", 0)),
        ),
    )[:limit]


def _axis_from_llm(
    llm_summary: dict,
    key: str,
    min_evidence: int = 1,
) -> float | None:
    """从 LLM 结果取语义轴分；证据不足时不接受模型给出的分数。"""
    detail = llm_summary.get(key)
    evidence = detail.get("evidence") if isinstance(detail, dict) else None
    valid_evidence = (
        [item for item in evidence if isinstance(item, str) and item.strip()]
        if isinstance(evidence, list)
        else []
    )
    if (
        isinstance(detail, dict)
        and not isinstance(detail.get("score"), bool)
        and isinstance(detail.get("score"), (int, float))
        and isfinite(float(detail["score"]))
        and len(valid_evidence) >= min_evidence
    ):
        return max(0.0, min(100.0, float(detail["score"])))
    return None


def _build_signal_evidence(
    speech_rate: float,
    filler_top: list,
    repetition_rate: float,
    avg_jitter: float | None,
    hesitation_count: int | None,
    hesitation_rate: float | None,
    long_pause_count: int | None,
    long_pause_rate: float | None,
    expression_breaks: list[dict],
) -> str:
    """把确定性信号整理成文本证据，供 LLM 归因时引用（不凭空点评）。"""
    lines = [
        f"- 语速：{speech_rate} 字/分" if speech_rate > 0 else "- 语速：无有效发言时长，不评价",
        f"- 明确口癖 Top5：{'、'.join(f'{w}×{c}' for w, c in filler_top) if filler_top else '无明显口癖'}",
        f"- 紧邻用词重复率：{repetition_rate:.1%}",
        f"- 局部表达断裂：{len(expression_breaks)} 处",
    ]
    for event in _select_expression_breaks(expression_breaks):
        excerpt = str(event.get("excerpt", "")).strip()
        if excerpt:
            lines.append(f"  - {excerpt}")
    if hesitation_count is not None and hesitation_rate is not None:
        lines.append(
            f"- 正文短停顿：{hesitation_count} 次（{hesitation_rate} 次/分钟）"
        )
    else:
        lines.append("- 正文短停顿：无有效声音数据")
    if long_pause_count is not None and long_pause_rate is not None:
        lines.append(
            f"- 正文长停顿：{long_pause_count} 次（{long_pause_rate} 次/分钟）"
        )
    else:
        lines.append("- 正文长停顿：无有效声音数据")
    lines.append("- 停顿仅在同时出现文本断裂时用于节奏评分，避免误罚修辞停顿")
    if avg_jitter is not None:
        lines.append(f"- 快速音高波动：{avg_jitter:.4f}（实验信号，不作心理解释）")
    lines.append("- 声音状态：不生成稳定或紧张综合分")
    return "\n".join(lines)


def _build_signal_axis_details(
    *,
    scores: dict[str, float | None],
    total_chars: int,
    filler_total: int,
    repetition_rate: float,
    expression_break_count: int,
    speech_rate: float,
    speech_duration: float | None,
    hesitation_count: int | None,
    hesitation_rate: float | None,
    long_pause_count: int | None,
    long_pause_rate: float | None,
) -> dict[str, dict[str, Any]]:
    """把确定性评分转换为用户可核对的结论与依据。"""

    continuity_score = scores.get("continuity")
    if continuity_score is None:
        continuity_feedback = "有效发言不足，暂不能评价表达连贯性。"
    elif continuity_score >= 85:
        continuity_feedback = "表达整体连贯，明确口癖、紧邻重复和局部断裂较少。"
    elif continuity_score >= 70:
        continuity_feedback = "表达基本连贯，少量口癖、重复或局部断裂影响了顺畅度。"
    else:
        continuity_feedback = "口癖、紧邻重复或局部断裂较集中，建议先缩短句子再完整表达。"

    pacing_score = scores.get("pacing")
    if pacing_score is None:
        pacing_feedback = "缺少有效发言时长，暂不能评价语速与节奏。"
    elif pacing_score >= 85:
        pacing_feedback = "语速处于清晰表达区间，未发现有文本断裂佐证的明显节奏问题。"
    elif pacing_score >= 70:
        pacing_feedback = "语速或有断裂佐证的停顿略有波动，可通过分句和换气改善。"
    else:
        pacing_feedback = "语速或有断裂佐证的停顿偏离较明显，建议按信息点分句并留出换气。"

    pacing_evidence = (
        [
            f"真实发言 {speech_duration:.1f} 秒，平均语速 {speech_rate:.0f} 字/分。",
            (
                f"正文短停顿 {hesitation_count} 次"
                + (
                    f"（{hesitation_rate:.1f} 次/分钟）"
                    if hesitation_rate is not None
                    else ""
                )
                + "；"
                + f"正文长停顿 {long_pause_count} 次"
                + (
                    f"（{long_pause_rate:.1f} 次/分钟）"
                    if long_pause_rate is not None
                    else ""
                )
                + "。"
            )
            if hesitation_count is not None and long_pause_count is not None
            else "停顿数据不足，本项仅依据真实发言语速。",
            "停顿只有同时出现局部表达断裂时才影响节奏分，首尾等待不计入。",
        ]
        if speech_duration is not None and speech_rate > 0
        else ["未记录到可用的真实发言时长，本维度不评分。"]
    )

    return {
        "continuity": {
            "feedback": continuity_feedback,
            "evidence": [
                f"有效表达 {total_chars} 字；明确口癖 {filler_total} 次。",
                f"紧邻用词重复率 {repetition_rate:.1%}；局部表达断裂 {expression_break_count} 处。",
            ],
        },
        "pacing": {
            "feedback": pacing_feedback,
            "evidence": pacing_evidence,
        },
    }


def _aggregate_emotion(dialogues, all_text: str):
    """读取旧协议的逐句兼容代理值。

    每句 analysis_json 里有 tension/confidence score/level（说话时实时推送的同一份）。
    无任何声学记录（纯文本会话/旧数据）时回落纯文本重算。
    """
    tensions: list[float] = []
    confidences: list[float] = []
    for d in dialogues:
        if d.role != "user" or not d.analysis_json:
            continue
        try:
            a = json.loads(d.analysis_json)
        except Exception:
            continue
        if isinstance(a.get("tension_score"), (int, float)):
            tensions.append(float(a["tension_score"]))
        if isinstance(a.get("confidence_score"), (int, float)):
            confidences.append(float(a["confidence_score"]))

    if tensions or confidences:
        from app.modules.analysis.emotion import _level

        t = sum(tensions) / len(tensions) if tensions else 0.0
        c = sum(confidences) / len(confidences) if confidences else 0.0
        # 轻量命名空间对象，供报告字典直接取字段
        calibrated = False
        voice_signal = False
        for d in dialogues:
            if d.role != "user" or not d.analysis_json:
                continue
            try:
                item = json.loads(d.analysis_json)
            except Exception:
                continue
            calibrated = calibrated or item.get("calibrated") is True
            voice_signal = voice_signal or item.get("voice_signal") is True

        class _Emotion:
            def __init__(
                self,
                t: float,
                c: float,
                calibrated: bool,
                voice_signal: bool,
            ) -> None:
                self.tension_score = round(t, 1)
                self.tension_level = _level(t, [40, 70], ["接近平时", "有所波动", "波动明显"])
                self.confidence_score = round(c, 1)
                self.confidence_level = _level(100 - c, [40, 70], ["强", "适中", "偏弱"])
                self.calibrated = calibrated
                self.voice_signal = voice_signal

        return _Emotion(t, c, calibrated, voice_signal)

    # 回落：无逐句记录（旧会话/纯文本），保持原纯文本判定
    text_res = analyze_text(all_text)
    fallback = analyze_emotion(text_res, None)
    fallback.voice_signal = False
    return fallback


def _aggregate_jitter(dialogues) -> float | None:
    """聚合逐句快速音高波动实验值；无记录返回 ``None``。"""
    jitters: list[float] = []
    for d in dialogues:
        if d.role != "user" or not d.analysis_json:
            continue
        try:
            a = json.loads(d.analysis_json)
        except Exception:
            continue
        if isinstance(a.get("pitch_jitter"), (int, float)):
            jitters.append(float(a["pitch_jitter"]))
    if not jitters:
        return None
    return sum(jitters) / len(jitters)


def _aggregate_analysis_count(dialogues, key: str) -> int | None:
    """累计用户轮次中的非负计数事实；缺失时返回 ``None``。"""
    values: list[int] = []
    for dialogue in dialogues:
        if dialogue.role != "user" or not dialogue.analysis_json:
            continue
        try:
            analysis = json.loads(dialogue.analysis_json)
        except Exception:
            continue
        value = analysis.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            values.append(int(value))
    return sum(values) if values else None


def _aggregate_speech_duration(dialogues) -> float | None:
    """累计用户真实发言时长；旧数据或纯文本会话返回 ``None``。"""
    durations: list[float] = []
    for dialogue in dialogues:
        if dialogue.role != "user" or not dialogue.analysis_json:
            continue
        try:
            analysis = json.loads(dialogue.analysis_json)
        except Exception:
            continue
        value = analysis.get("speech_duration_sec")
        if isinstance(value, (int, float)) and value > 0:
            durations.append(float(value))
    if not durations:
        return None
    return round(sum(durations), 2)


def _elapsed_duration(session: InterviewSessionRow) -> float:
    """返回会话实际经过时间；缺少开始时间时返回 0。"""
    if session.started_at:
        from datetime import datetime

        return max(0.0, (datetime.now() - session.started_at).total_seconds())
    return 0.0


def _sample_state(
    total_chars: int,
    speech_duration: float | None,
    calibrated: bool,
) -> str:
    """描述本次报告实际拥有的数据类型，不做主观质量猜测。"""
    if total_chars <= 0:
        return "insufficient"
    if speech_duration is None:
        return "text_only"
    if not calibrated:
        return "voice_uncalibrated"
    return "voice_calibrated"


async def _llm_evaluate(
    db: AsyncSession,
    session: InterviewSessionRow,
    dialogues: list[InterviewDialogueRow],
    pack,
    signal_evidence: str = "",
) -> dict[str, Any]:
    """调用 LLM 做场景语义归因与建议。

    LLM 不打总分，只评价场景包声明的语义轴；每项结论必须携带证据。
    """
    dialogue_text = "\n".join(
        [f"{pack.role_name if d.role == 'ai' else '我'}：{d.text}" for d in dialogues]
    )

    semantic_axes = [axis for axis in pack.evaluation.axes if axis.source == "llm"]
    metrics_def = ",\n".join(
        f'    "{axis.key}": '
        f'{{"score": 0-100, "feedback": "{axis.description}", '
        f'"evidence": ["至少 {axis.min_evidence} 条训练记录原话"]}}'
        for axis in semantic_axes
    )
    rubric_def = "\n\n".join(
        "\n".join(
            [
                f"- {axis.key}（{axis.label}）：{axis.description}",
                f"  最少证据：{axis.min_evidence} 条",
                "  分数锚点：",
                *[
                    f"  - {anchor.score} 分：{anchor.description}"
                    for anchor in sorted(
                        axis.anchors,
                        key=lambda item: item.score,
                        reverse=True,
                    )
                ],
            ]
        )
        for axis in semantic_axes
    )
    semantic_keys = "、".join(axis.key for axis in semantic_axes)
    advice_guide = "\n".join(
        f"- {section}" for section in pack.evaluation.advice_sections
    )

    duration_note = (
        f"预设时长：{session.duration_limit} 分钟"
        "（仅作事实说明和训练建议参考；当前没有校准时间阈值，不得计入任何语义轴评分）"
        if pack.timed and session.duration_limit
        else ""
    )

    prompt = f"""请基于以下{pack.name}训练记录，对表现给出评估。

主题/岗位：{session.position}
{duration_note}

训练记录：
{dialogue_text[:6000]}

【本次可观察表达事实（仅供事实说明和训练建议使用，不得替代语义轴证据）】
{signal_evidence or '（无语音信号）'}

评估要求：
- 客观公正：先找亮点再指问题；表现确实好就给高分（85+），不要一律从严。
- 只依据记录中真实出现的内容评分，不臆测未发生的问题。
- {semantic_keys} 的 evidence 必须逐条引用用户在训练记录中的连续原文片段；不得改写、用省略号拼接，也不得引用 AI 发言、主题说明或上述表达信号代替原话证据。
- 每个语义轴至少提供其要求的有效 evidence；证据不足时不要给出虚构证据。
- 依据相邻锚点判断分数，落在锚点之间时再按证据插值，不要使用统一印象分。

【结构化评分规则】
{rubric_def}

请返回严格的 JSON，字段如下：
{{
  "summary": "一句话总评（先肯定亮点，再点主要问题）",
{metrics_def},
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
        report_options: dict[str, Any] = {
            "temperature": 0.3,
            "max_tokens": REPORT_LLM_MAX_TOKENS,
            "read_timeout": REPORT_LLM_READ_TIMEOUT_SECONDS,
        }
        # DeepSeek V4 默认开启思考，思考过程与最终 JSON 共用 max_tokens。
        # 报告是已有明确 rubric 的结构化归纳任务，关闭思考可避免预算在正文前耗尽。
        # 只对明确配置为 DeepSeek 的适配器发送厂商字段，保持其他兼容端点不变。
        if getattr(cfg, "provider", "") == "deepseek":
            report_options["thinking"] = False
        raw = await provider.chat(
            [
                {"role": "system", "content": pack.evaluation.reviewer_prompt},
                {"role": "user", "content": prompt},
            ],
            **report_options,
        )
        result = _safe_json(raw)
        return _validate_semantic_evidence(
            result,
            [axis.key for axis in semantic_axes],
            [dialogue.text for dialogue in dialogues if dialogue.role == "user"],
        )
    except Exception as e:
        logger.exception("LLM 报告评估失败")
        return {
            "summary": (
                "语义评价暂未完成，请稍后重试生成报告；"
                "下方客观表达数据仍可参考。"
            )
        }


def _safe_json(raw: str) -> dict[str, Any]:
    """安全解析模型返回的首个完整 JSON 对象。

    模型偶尔会在 JSON 前附加简短说明或 reasoning。这里仅使用标准库
    解码器，不执行、修补或猜测任何内容；数组、标量和残缺对象均视为失败。
    """
    cleaned = raw.strip()

    # 最常见且最明确的协议响应优先，避免在合法顶层数组中抽取其内部对象。
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    else:
        if isinstance(parsed, dict):
            return parsed
        return _json_parse_fallback(raw, f"top_level_{type(parsed).__name__}")

    # 保留原有“响应整体是 Markdown 代码围栏”的兼容路径。
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            fenced = "\n".join(
                lines[1:-1] if lines[-1].startswith("```") else lines[1:]
            ).strip()
            try:
                parsed = json.loads(fenced)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed, dict):
                    return parsed
                return _json_parse_fallback(
                    raw,
                    f"fenced_top_level_{type(parsed).__name__}",
                )

    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(cleaned, index)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(candidate, dict):
            return candidate

    reason = type(last_error).__name__ if last_error else "no_json_object"
    return _json_parse_fallback(raw, reason)


def _json_parse_fallback(raw: str, reason: str) -> dict[str, Any]:
    """返回固定降级结果，并只记录不含响应内容的安全诊断。"""
    logger.warning(
        "LLM 报告 JSON 解析失败：response_length=%d reason=%s",
        len(raw),
        reason,
    )
    return {
        "summary": (
            "语义评价暂未完成，请稍后重试生成报告；"
            "下方客观表达数据仍可参考。"
        )
    }


def _validate_semantic_evidence(
    result: dict[str, Any],
    axis_keys: list[str],
    user_utterances: list[str],
) -> dict[str, Any]:
    """仅保留能在用户转写中逐字核对的连续原文证据。

    允许忽略首尾引号和空白差异，但不接受改写、跨句拼接或过短片段。
    该校验与具体场景无关，防止模型用看似合理但不存在的引文支撑分数。
    """
    normalized_utterances = [
        _normalize_evidence_text(item)
        for item in user_utterances
        if isinstance(item, str) and item.strip()
    ]
    for axis_key in axis_keys:
        detail = result.get(axis_key)
        if not isinstance(detail, dict):
            continue
        evidence = detail.get("evidence")
        if not isinstance(evidence, list):
            detail["evidence"] = []
            continue
        verified: list[str] = []
        for item in evidence:
            if not isinstance(item, str):
                continue
            quote = item.strip().strip('"\'“”‘’').strip()
            normalized_quote = _normalize_evidence_text(quote)
            if len(normalized_quote) < 4:
                continue
            if any(normalized_quote in utterance for utterance in normalized_utterances):
                verified.append(quote)
        detail["evidence"] = verified
    return result


def _normalize_evidence_text(text: str) -> str:
    """压缩空白并忽略大小写，保留标点以确保引用没有被改写。"""
    return " ".join(text.split()).casefold()
