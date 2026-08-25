"""上传材料格式边界测试。"""

import io

import pytest
from reportlab.pdfgen import canvas

from app.core.exceptions import ResumeParseError
from app.modules.resume import SUPPORTED_EXT, extract_text


def test_legacy_doc_is_not_claimed_as_supported() -> None:
    assert ".doc" not in SUPPORTED_EXT
    with pytest.raises(ResumeParseError, match="不支持"):
        extract_text(b"legacy word content", "resume.doc")


def test_plain_text_supports_utf8() -> None:
    assert extract_text("中文材料".encode(), "material.txt") == "中文材料"


def test_pdf_extracts_text_with_pypdf() -> None:
    output = io.BytesIO()
    document = canvas.Canvas(output)
    document.drawString(72, 720, "Speech Trainer resume")
    document.save()

    extracted = extract_text(output.getvalue(), "resume.pdf")

    assert "Speech Trainer resume" in extracted


def test_invalid_pdf_is_reported_as_resume_parse_error() -> None:
    with pytest.raises(ResumeParseError, match="PDF 解析失败"):
        extract_text(b"not a PDF", "resume.pdf")
