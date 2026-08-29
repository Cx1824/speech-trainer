"""训练报告路由（三场景通用：面试/汇报/演讲）。"""

from __future__ import annotations

import logging
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.modules.report import (
    build_manual_analysis_package,
    generate_report,
    get_report,
)
from app.modules.scenarios import get_pack

logger = logging.getLogger(__name__)
router = APIRouter()


def _escape(value: object) -> str:
    """转义用户与模型文本，供 ReportLab Paragraph 和 HTML 安全展示。"""
    return escape(str(value or ""), quote=True)


def _scenario_labels(report: dict) -> tuple[str, str]:
    """（场景名，AI 角色名）：报告标题与对话角色按场景取。"""
    pack = get_pack(report.get("scenario", "interview"))
    return pack.name, pack.role_name


def _constraint_reasons(report: dict) -> list[str]:
    """提取面向用户的约束原因；忽略轴编码等内部实现字段。"""
    constraints = report.get("score_constraints")
    if not isinstance(constraints, list):
        return []
    reasons: list[str] = []
    for item in constraints:
        if not isinstance(item, dict):
            continue
        reason = item.get("reason")
        if isinstance(reason, str) and reason.strip():
            reasons.append(reason.strip())
    return reasons


def _voice_reference(report: dict) -> dict | None:
    """读取可展示的声音预估；旧快照和不完整数据返回 ``None``。"""
    raw = report.get("voice_reference")
    if not isinstance(raw, dict) or raw.get("available") is not True:
        return None
    dimensions = [
        item
        for item in raw.get("dimensions", [])
        if isinstance(item, dict)
        and isinstance(item.get("label"), str)
        and isinstance(item.get("value"), str)
    ]
    basis = [item for item in raw.get("basis", []) if isinstance(item, str)]
    return {
        "summary": str(raw.get("summary") or ""),
        "confidence": str(raw.get("confidence") or "较低"),
        "confidence_note": str(raw.get("confidence_note") or ""),
        "dimensions": dimensions,
        "basis": basis,
    }


def _interview_coverage(report: dict) -> dict | None:
    """读取面向用户的面试覆盖摘要，不暴露计划内部字段。"""
    raw = report.get("interview_coverage")
    if report.get("scenario") != "interview" or not isinstance(raw, dict):
        return None
    covered = [item for item in raw.get("covered_labels", []) if isinstance(item, str)]
    remaining = [item for item in raw.get("remaining_labels", []) if isinstance(item, str)]
    skipped = [item for item in raw.get("skipped_labels", []) if isinstance(item, str)]
    return {
        "mode_label": str(raw.get("mode_label") or "面试训练"),
        "intensity_label": str(raw.get("intensity_label") or ""),
        "covered": covered,
        "unassessed": [*remaining, *[item for item in skipped if item not in remaining]],
        "followups_used": max(0, int(raw.get("followups_used") or 0)),
    }


@router.post("/{sid}")
async def create_report(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """首次生成报告；重复调用返回同一份持久化快照。"""
    return await generate_report(db, sid)


@router.get("/{sid}")
async def read_report(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """读取最新报告快照，不触发重新评分。"""
    return await get_report(db, sid)


@router.post("/{sid}/regenerate")
async def regenerate_report(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """显式创建一个新的报告版本。"""
    return await generate_report(db, sid, regenerate=True)


@router.get("/{sid}/manual-analysis")
async def manual_analysis_package(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """导出对话记录和可提交给任意语言模型的分析提示词。"""
    report = await get_report(db, sid)
    return build_manual_analysis_package(report)


@router.get("/{sid}/pdf")
@router.post("/{sid}/pdf", include_in_schema=False)
async def export_pdf(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> Response:
    """从已持久化的报告快照导出 PDF。"""
    report = await get_report(db, sid)
    html = _render_html(report)

    # 用 ReportLab 或 weasyprint 转 PDF；MVP 简化先用 reportlab 基础版
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.lib import colors
        import io as _io

        # ReportLab 内置中文 CID 字体，避免依赖某个操作系统的字体路径。
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            font_name = "STSong-Light"
        except Exception:
            font_name = "Helvetica"

        buf = _io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName=font_name, fontSize=20)
        h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=font_name, fontSize=14)
        body = ParagraphStyle("body", parent=styles["BodyText"], fontName=font_name, fontSize=11, leading=18)

        story = []
        sc_name, ai_role = _scenario_labels(report)
        story.append(Paragraph(
            f"{_escape(sc_name)}报告 - {_escape(report['position'])} "
            f"({_escape(report['level'])})",
            h1,
        ))
        story.append(Spacer(1, 12))
        overall = report.get("overall_score")
        overall_text = f"{overall} / 100" if overall is not None else "评估未完成"
        story.append(Paragraph(f"<b>本场景综合评分：</b>{overall_text}", body))
        for reason in _constraint_reasons(report):
            story.append(Paragraph(f"<b>关键任务约束：</b>{_escape(reason)}", body))
        story.append(Paragraph(f"<b>总评：</b>{_escape(report.get('summary'))}", body))
        story.append(Spacer(1, 12))

        interview_coverage = _interview_coverage(report)
        if interview_coverage:
            story.append(Paragraph("本次面试覆盖", h2))
            profile = interview_coverage["mode_label"]
            if interview_coverage["intensity_label"]:
                profile += f" · {interview_coverage['intensity_label']}强度"
            story.append(Paragraph(f"<b>训练方式：</b>{_escape(profile)}", body))
            story.append(Paragraph(
                f"<b>已练习：</b>{_escape('、'.join(interview_coverage['covered']) or '暂无')}",
                body,
            ))
            if interview_coverage["unassessed"]:
                story.append(Paragraph(
                    f"<b>未评估：</b>{_escape('、'.join(interview_coverage['unassessed']))}",
                    body,
                ))
            story.append(Paragraph(
                f"<b>有效追问：</b>{interview_coverage['followups_used']} 次",
                body,
            ))
            story.append(Spacer(1, 8))

        story.append(Paragraph("能力维度", h2))
        for ax in report.get("axes", []):
            sc = f"{ax['score']:.0f}" if ax.get("score") is not None else "—"
            line = f"{_escape(ax['label'])}：{sc} / 100（权重 {ax['weight']}%）"
            if ax.get("feedback"):
                line += f" —— {_escape(ax['feedback'])}"
            story.append(Paragraph(line, body))
            for evidence in ax.get("evidence", []):
                if isinstance(evidence, str) and evidence.strip():
                    story.append(Paragraph(f"· 判断依据：{_escape(evidence)}", body))
        story.append(Spacer(1, 8))

        story.append(Paragraph("表达维度", h2))
        em = report.get("expression_metrics", {})
        story.append(Paragraph(f"语速：{em.get('speech_rate', 0)} 字/分（{em.get('speech_rate_level', '未知')}）", body))
        story.append(Paragraph(f"有效字数：{em.get('total_words', 0)}，明确口癖：{em.get('filler_total', 0)} 次", body))
        story.append(Paragraph(f"紧邻用词重复率：{em.get('repetition_rate', 0):.2f}", body))
        story.append(Paragraph(f"局部表达断裂：{em.get('expression_break_count', 0)} 处", body))
        for item in em.get("expression_break_examples", []):
            excerpt = item.get("excerpt") if isinstance(item, dict) else ""
            if isinstance(excerpt, str) and excerpt.strip():
                story.append(Paragraph(f"断裂片段：{_escape(excerpt)}", body))
        short_pauses = em.get("short_pause_count")
        long_pauses = em.get("long_pause_count")
        story.append(Paragraph(
            f"正文短停顿：{short_pauses if short_pauses is not None else '数据不足'}；"
            f"正文长停顿：{long_pauses if long_pauses is not None else '数据不足'}",
            body,
        ))
        story.append(Spacer(1, 8))

        voice_reference = _voice_reference(report)
        if voice_reference:
            story.append(Paragraph("声音与节奏参考（不计分）", h2))
            story.append(Paragraph(_escape(voice_reference["summary"]), body))
            for dimension in voice_reference["dimensions"]:
                detail = dimension.get("detail")
                line = f"<b>{_escape(dimension['label'])}：</b>{_escape(dimension['value'])}"
                if isinstance(detail, str) and detail:
                    line += f" —— {_escape(detail)}"
                story.append(Paragraph(line, body))
            story.append(Paragraph(
                f"<b>可信度：</b>{_escape(voice_reference['confidence'])}。"
                f"{_escape(voice_reference['confidence_note'])}",
                body,
            ))
            if voice_reference["basis"]:
                story.append(Paragraph(
                    f"<b>判断依据：</b>{_escape('；'.join(voice_reference['basis']))}",
                    body,
                ))
            story.append(Spacer(1, 8))

        story.append(Paragraph("强化建议", h2))
        sug = report.get("suggestions", {})
        for s in sug.get("short_term", []):
            story.append(Paragraph(f"· 短期：{_escape(s)}", body))
        for s in sug.get("mid_term", []):
            story.append(Paragraph(f"· 中期：{_escape(s)}", body))
        story.append(PageBreak())

        story.append(Paragraph("完整对话记录", h2))
        _, ai_role = _scenario_labels(report)
        for d in report.get("dialogues", []):
            role = ai_role if d["role"] == "ai" else "我"
            story.append(Paragraph(
                f"<b>{_escape(role)}：</b>{_escape(d['text'])}",
                body,
            ))

        doc.build(story)
        pdf_bytes = buf.getvalue()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=report_{sid}.pdf"},
        )
    except Exception as e:
        logger.exception("PDF 生成失败")
        # 降级：返回 HTML
        return Response(
            content=html.encode("utf-8"),
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename=report_{sid}.html"},
        )


def _render_html(report: dict) -> str:
    """简单 HTML 报告（PDF 失败时降级）。"""
    em = report.get("expression_metrics", {})
    sug = report.get("suggestions", {})
    voice_reference = _voice_reference(report)
    interview_coverage = _interview_coverage(report)
    dialogues = report.get("dialogues", [])
    sc_name, ai_role = _scenario_labels(report)

    def _list(items):
        return "".join(f"<li>{_escape(item)}</li>" for item in items)

    axes_html = "".join(
        "<li>"
        f"{_escape(axis['label'])}："
        f"{axis['score'] if axis.get('score') is not None else '—'} / 100"
        f"（权重 {axis['weight']}%）"
        f"{' — ' + _escape(axis['feedback']) if axis.get('feedback') else ''}"
        + (
            "<ul>"
            + "".join(
                f"<li>判断依据：{_escape(item)}</li>"
                for item in axis.get("evidence", [])
                if isinstance(item, str) and item.strip()
            )
            + "</ul>"
            if axis.get("evidence")
            else ""
        )
        + "</li>"
        for axis in report.get("axes", [])
    )
    constraints_html = "".join(
        f"<li>{_escape(reason)}</li>" for reason in _constraint_reasons(report)
    )
    constraints_section = (
        '<section class="score-constraints"><strong>关键任务约束</strong>'
        f"<ul>{constraints_html}</ul></section>"
        if constraints_html
        else ""
    )
    coverage_section = ""
    if interview_coverage:
        profile = interview_coverage["mode_label"]
        if interview_coverage["intensity_label"]:
            profile += f" · {interview_coverage['intensity_label']}强度"
        unassessed_html = (
            f"<p><strong>未评估：</strong>{_escape('、'.join(interview_coverage['unassessed']))}</p>"
            if interview_coverage["unassessed"]
            else ""
        )
        coverage_section = (
            "<h2>本次面试覆盖</h2>"
            f"<p><strong>训练方式：</strong>{_escape(profile)}</p>"
            f"<p><strong>已练习：</strong>{_escape('、'.join(interview_coverage['covered']) or '暂无')}</p>"
            f"{unassessed_html}"
            f"<p><strong>有效追问：</strong>{interview_coverage['followups_used']} 次</p>"
        )
    voice_section = ""
    if voice_reference:
        dimension_items = "".join(
            "<li>"
            f"{_escape(item['label'])}：{_escape(item['value'])}"
            f"{' — ' + _escape(item.get('detail')) if item.get('detail') else ''}"
            "</li>"
            for item in voice_reference["dimensions"]
        )
        basis_items = "".join(
            f"<li>{_escape(item)}</li>" for item in voice_reference["basis"]
        )
        voice_section = (
            "<h2>声音与节奏参考（不计分）</h2>"
            f"<p>{_escape(voice_reference['summary'])}</p>"
            f"<ul>{dimension_items}</ul>"
            f"<p>可信度：{_escape(voice_reference['confidence'])}。"
            f"{_escape(voice_reference['confidence_note'])}</p>"
            f"<p>判断依据：</p><ul>{basis_items}</ul>"
        )
    dialogues_html = "".join(
        f'<div class="dialogue {_escape(dialogue["role"])}">'
        f'<span class="role">'
        f'{_escape(ai_role if dialogue["role"] == "ai" else "我")}：'
        f'</span>{_escape(dialogue["text"])}</div>'
        for dialogue in dialogues
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{_escape(sc_name)}报告</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", sans-serif; max-width: 720px; margin: 40px auto; color: #222; line-height: 1.6; }}
h1 {{ color: #534ab7; }}
h2 {{ border-bottom: 2px solid #534ab7; padding-bottom: 6px; margin-top: 32px; }}
.score {{ font-size: 48px; font-weight: bold; color: #534ab7; }}
.score-constraints {{ margin: 12px 0; padding: 12px 16px; border-left: 4px solid #d97706; background: #fff7ed; }}
.score-constraints ul {{ margin: 6px 0 0; padding-left: 20px; }}
.dialogue {{ margin: 8px 0; }}
.dialogue .role {{ font-weight: bold; }}
.dialogue.ai {{ color: #555; }}
.dialogue.user {{ color: #1d9e75; }}
</style></head>
<body>
<h1>{_escape(sc_name)}报告</h1>
<p>{_escape(report.get('position'))} · {_escape(report.get('level'))}</p>
<div class="score">{report.get('overall_score') if report.get('overall_score') is not None else '—'}</div>
{constraints_section}
<p>{_escape(report.get('summary'))}</p>

{coverage_section}

<h2>能力维度</h2>
<ul>
{axes_html}
</ul>

<h2>表达维度</h2>
<ul>
<li>语速：{em.get('speech_rate', 0)} 字/分（{em.get('speech_rate_level', '')}）</li>
<li>有效字数：{em.get('total_words', 0)}</li>
<li>口癖总次数：{em.get('filler_total', 0)}</li>
<li>紧邻用词重复率：{em.get('repetition_rate', 0):.2f}</li>
<li>局部表达断裂：{em.get('expression_break_count', 0)} 处</li>
<li>正文短停顿：{em.get('short_pause_count') if em.get('short_pause_count') is not None else '数据不足'}</li>
<li>正文长停顿：{em.get('long_pause_count') if em.get('long_pause_count') is not None else '数据不足'}</li>
</ul>
{voice_section}

<h2>强化建议</h2>
<h3>短期改进</h3>
<ul>{_list(sug.get('short_term', []))}</ul>
<h3>中期方向</h3>
<ul>{_list(sug.get('mid_term', []))}</ul>

<h2>完整对话记录</h2>
{dialogues_html}

</body></html>"""
