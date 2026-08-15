"""训练报告路由（三场景通用：面试/汇报/演讲）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.modules.report import generate_report
from app.modules.scenarios import get_pack

logger = logging.getLogger(__name__)
router = APIRouter()


def _scenario_labels(report: dict) -> tuple[str, str]:
    """（场景名，AI 角色名）：报告标题与对话角色按场景取。"""
    pack = get_pack(report.get("scenario", "interview"))
    return pack.name, pack.role_name


@router.post("/{sid}")
async def create_report(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """生成报告（JSON）。"""
    return await generate_report(db, sid)


@router.post("/{sid}/pdf")
async def export_pdf(
    sid: str,
    db: AsyncSession = Depends(get_session),
) -> Response:
    """导出 PDF（HTML 转 PDF 简化版）。"""
    report = await generate_report(db, sid)
    html = _render_html(report)

    # 用 ReportLab 或 weasyprint 转 PDF；MVP 简化先用 reportlab 基础版
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib import colors
        import io as _io

        # 注册中文字体（macOS 自带 PingFang）
        try:
            pdfmetrics.registerFont(TTFont("CN", "/System/Library/Fonts/PingFang.ttc"))
            font_name = "CN"
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
        story.append(Paragraph(f"{sc_name}报告 - {report['position']} ({report['level']})", h1))
        story.append(Spacer(1, 12))
        story.append(Paragraph(f"<b>综合评分：</b>{report['overall_score']} / 100", body))
        story.append(Paragraph(f"<b>总评：</b>{report.get('summary', '')}", body))
        story.append(Spacer(1, 12))

        story.append(Paragraph("表达维度", h2))
        em = report.get("expression_metrics", {})
        story.append(Paragraph(f"语速：{em.get('speech_rate', 0)} 字/分（{em.get('speech_rate_level', '未知')}）", body))
        story.append(Paragraph(f"总字数：{em.get('total_words', 0)}，口癖总次数：{em.get('filler_total', 0)}", body))
        story.append(Paragraph(f"用词重复率：{em.get('repetition_rate', 0):.2f}", body))
        story.append(Spacer(1, 8))

        story.append(Paragraph("情绪维度", h2))
        emo = report.get("emotion_metrics", {})
        story.append(Paragraph(f"紧张度：{emo.get('tension_score', 0)}（{emo.get('tension_level', '')}）", body))
        story.append(Paragraph(f"自信度：{emo.get('confidence_score', 0)}（{emo.get('confidence_level', '')}）", body))
        story.append(Spacer(1, 8))

        story.append(Paragraph("强化建议", h2))
        sug = report.get("suggestions", {})
        for s in sug.get("short_term", []):
            story.append(Paragraph(f"· 短期：{s}", body))
        for s in sug.get("mid_term", []):
            story.append(Paragraph(f"· 中期：{s}", body))
        story.append(PageBreak())

        story.append(Paragraph("完整对话记录", h2))
        _, ai_role = _scenario_labels(report)
        for d in report.get("dialogues", []):
            role = ai_role if d["role"] == "ai" else "我"
            story.append(Paragraph(f"<b>{role}：</b>{d['text']}", body))

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
    emo = report.get("emotion_metrics", {})
    sug = report.get("suggestions", {})
    dialogues = report.get("dialogues", [])
    sc_name, ai_role = _scenario_labels(report)

    def _list(items): return "".join(f"<li>{i}</li>" for i in items)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{sc_name}报告</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", sans-serif; max-width: 720px; margin: 40px auto; color: #222; line-height: 1.6; }}
h1 {{ color: #534ab7; }}
h2 {{ border-bottom: 2px solid #534ab7; padding-bottom: 6px; margin-top: 32px; }}
.score {{ font-size: 48px; font-weight: bold; color: #534ab7; }}
.dialogue {{ margin: 8px 0; }}
.dialogue .role {{ font-weight: bold; }}
.dialogue.ai {{ color: #555; }}
.dialogue.user {{ color: #1d9e75; }}
</style></head>
<body>
<h1>{sc_name}报告</h1>
<p>{report.get('position', '')} · {report.get('level', '')}</p>
<div class="score">{report.get('overall_score', '-')}</div>
<p>{report.get('summary', '')}</p>

<h2>表达维度</h2>
<ul>
<li>语速：{em.get('speech_rate', 0)} 字/分（{em.get('speech_rate_level', '')}）</li>
<li>总字数：{em.get('total_words', 0)}</li>
<li>口癖总次数：{em.get('filler_total', 0)}</li>
<li>用词重复率：{em.get('repetition_rate', 0):.2f}</li>
</ul>

<h2>情绪维度</h2>
<ul>
<li>紧张度：{emo.get('tension_score', 0)}（{emo.get('tension_level', '')}）</li>
<li>自信度：{emo.get('confidence_score', 0)}（{emo.get('confidence_level', '')}）</li>
</ul>

<h2>强化建议</h2>
<h3>短期改进</h3>
<ul>{_list(sug.get('short_term', []))}</ul>
<h3>中期方向</h3>
<ul>{_list(sug.get('mid_term', []))}</ul>

<h2>完整对话记录</h2>
{''.join(f'<div class="dialogue {d["role"]}"><span class="role">{ai_role if d["role"]=="ai" else "我"}：</span>{d["text"]}</div>' for d in dialogues)}

</body></html>"""
