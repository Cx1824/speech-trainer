"""报告 HTML/PDF 导出安全测试。"""

import reportlab.platypus

from app.api.v1 import report as report_api
from app.api.v1.report import _render_html


def test_render_html_escapes_user_and_model_content() -> None:
    report = {
        "scenario": "interview",
        "position": "<script>alert(1)</script>",
        "level": "高级",
        "overall_score": None,
        "score_constraints": [
            {
                "axis_key": "internal_topic_alignment_secret",
                "reason": '<script>alert("gate")</script>关键任务未完成',
            }
        ],
        "summary": "<b>模型内容</b>",
        "interview_coverage": {
            "mode_label": "全流程<script>alert(2)</script>",
            "intensity_label": "标准",
            "covered_labels": ["自我介绍", "<img src=x onerror=alert(2)>"],
            "remaining_labels": ["专业判断"],
            "skipped_labels": ["行为问题"],
            "followups_used": 2,
        },
        "axes": [
            {
                "label": "回答结构",
                "score": 80,
                "weight": 25,
                "feedback": "<img src=x onerror=alert(1)>",
            }
        ],
        "expression_metrics": {
            "duration_source": "voice",
            "short_pause_count": 4,
            "long_pause_count": 2,
        },
        "delivery_metrics": {
            "stability_score": None,
            "voice_signal": True,
            "calibrated": True,
            "pitch_jitter": 0.052,
            "note": "<em>说明</em>",
        },
        "voice_reference": {
            "available": True,
            "summary": "声音起伏明显，但表达流畅。",
            "confidence": "中等",
            "confidence_note": "请结合<回听>判断。",
            "dimensions": [
                {
                    "label": "声音起伏",
                    "value": "起伏较明显",
                    "detail": "可能来自<强调>",
                }
            ],
            "basis": ["语速：偏快", "口癖：3次"],
        },
        "suggestions": {"short_term": ["<svg onload=alert(1)>"]},
        "dialogues": [{"role": "user", "text": "<script>bad()</script>"}],
    }

    html = _render_html(report)

    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "<svg onload" not in html
    assert '<script>alert("gate")</script>' not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;b&gt;模型内容&lt;/b&gt;" in html
    assert "声音与节奏参考（不计分）" in html
    assert "声音起伏明显，但表达流畅。" in html
    assert "声音起伏：起伏较明显" in html
    assert "可能来自&lt;强调&gt;" in html
    assert "请结合&lt;回听&gt;判断" in html
    assert "判断依据" in html
    assert "0.0520" not in html
    assert "&lt;em&gt;说明&lt;/em&gt;" not in html
    assert "关键任务约束" in html
    assert "本次面试覆盖" in html
    assert "已练习" in html
    assert "未评估" in html
    assert "专业判断、行为问题" in html
    assert "有效追问：</strong>2 次" in html
    assert "全流程&lt;script&gt;alert(2)&lt;/script&gt;" in html
    assert "&lt;script&gt;alert(&quot;gate&quot;)&lt;/script&gt;关键任务未完成" in html
    assert "internal_topic_alignment_secret" not in html


async def test_export_pdf_displays_only_escaped_constraint_reason(monkeypatch) -> None:
    report = {
        "scenario": "speech",
        "position": "测试主题",
        "level": "",
        "overall_score": 59,
        "summary": "测试总评",
        "score_constraints": [
            {
                "axis_key": "internal_axis_secret",
                "reason": "<script>bad()</script>主题任务未完成",
            }
        ],
        "axes": [],
        "expression_metrics": {},
        "delivery_metrics": {},
        "suggestions": {},
        "dialogues": [],
    }

    async def fake_get_report(db, sid):
        return report

    captured_paragraphs: list[str] = []
    original_paragraph = reportlab.platypus.Paragraph

    def capture_paragraph(text, style):
        captured_paragraphs.append(text)
        return original_paragraph(text, style)

    monkeypatch.setattr(report_api, "get_report", fake_get_report)
    monkeypatch.setattr(reportlab.platypus, "Paragraph", capture_paragraph)

    response = await report_api.export_pdf("session-id", None)

    assert response.media_type == "application/pdf"
    constraint_line = next(
        line for line in captured_paragraphs if "关键任务约束" in line
    )
    assert "&lt;script&gt;bad()&lt;/script&gt;主题任务未完成" in constraint_line
    assert "<script>" not in constraint_line
    assert "internal_axis_secret" not in "".join(captured_paragraphs)
