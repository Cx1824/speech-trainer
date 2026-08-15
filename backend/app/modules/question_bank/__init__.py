"""题库模块对外接口。"""

from app.modules.question_bank.manager import get_questions, save_questions

__all__ = ["get_questions", "save_questions"]
