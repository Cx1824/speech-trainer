"""自定义异常体系。

所有业务异常继承 AppError，统一异常处理器转为 JSON 响应。
"""

from __future__ import annotations


class AppError(Exception):
    """业务异常基类。"""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class ConfigError(AppError):
    status_code = 400
    code = "config_error"


class ProviderError(AppError):
    """AI 提供商调用失败。"""

    status_code = 502
    code = "provider_error"


class InterviewError(AppError):
    status_code = 400
    code = "interview_error"


class ResumeParseError(AppError):
    status_code = 422
    code = "resume_parse_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
