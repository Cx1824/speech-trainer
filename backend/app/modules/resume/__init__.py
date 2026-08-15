"""简历模块对外接口。"""

from app.modules.resume.parser import (
    MAX_FILE_SIZE,
    SUPPORTED_EXT,
    extract_text,
    parse_with_llm,
)

__all__ = ["extract_text", "parse_with_llm", "SUPPORTED_EXT", "MAX_FILE_SIZE"]
