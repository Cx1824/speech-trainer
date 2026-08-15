"""简历解析模块。"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any

from app.core.exceptions import ResumeParseError

logger = logging.getLogger(__name__)

SUPPORTED_EXT = {".pdf", ".docx", ".doc", ".txt"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def extract_text(content: bytes, filename: str) -> str:
    """从文件二进制提取纯文本。"""
    ext = Path(filename).suffix.lower()
    if ext == ".txt":
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("gbk", errors="ignore")

    if ext == ".pdf":
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(content))
            return "\n".join([p.extract_text() or "" for p in reader.pages])
        except Exception as e:
            raise ResumeParseError(f"PDF 解析失败：{e}") from e

    if ext in (".docx", ".doc"):
        try:
            from docx import Document
            doc = Document(io.BytesIO(content))
            return "\n".join([p.text for p in doc.paragraphs if p.text])
        except Exception as e:
            raise ResumeParseError(f"DOCX 解析失败：{e}") from e

    raise ResumeParseError(f"不支持的文件格式：{ext}")


async def parse_with_llm(text: str, llm_chat) -> dict[str, Any]:
    """调用 LLM 把简历文本解析为结构化 JSON。

    Args:
        text: 简历纯文本
        llm_chat: 异步函数 (messages: list[dict]) -> str
    """
    from app.modules.interview.prompts import RESUME_PARSE_PROMPT

    if len(text.strip()) < 30:
        raise ResumeParseError("简历文本过短，可能解析失败")

    messages = [
        {"role": "system", "content": "你是一个简历解析器，只返回严格的 JSON。"},
        {"role": "user", "content": RESUME_PARSE_PROMPT + text[:8000]},  # 截断
    ]
    raw = await llm_chat(messages)
    return _safe_parse_json(raw)


def _safe_parse_json(raw: str) -> dict[str, Any]:
    """容错解析 LLM 返回的 JSON。"""
    # 移除可能的 markdown 代码块
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2:
            cleaned = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("返回非对象")
        # 标准化字段
        return {
            "basics": data.get("basics", {}) if isinstance(data.get("basics"), dict) else {},
            "education": data.get("education", []) if isinstance(data.get("education"), list) else [],
            "work": data.get("work", []) if isinstance(data.get("work"), list) else [],
            "projects": data.get("projects", []) if isinstance(data.get("projects"), list) else [],
            "skills": data.get("skills", []) if isinstance(data.get("skills"), list) else [],
            "position_guess": str(data.get("position_guess", "")),
            "level_guess": str(data.get("level_guess", "")),
        }
    except Exception as e:
        logger.warning("LLM 简历 JSON 解析失败：%s\n原始返回：%s", e, raw[:500])
        raise ResumeParseError(f"简历结构化解析失败：{e}") from e
