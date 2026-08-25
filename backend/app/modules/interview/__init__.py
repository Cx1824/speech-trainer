"""面试模块对外接口。"""

from app.modules.interview.manager import (
    advance_stage,
    complete_interview,
    create_session,
    generate_next,
    get_interview_progress,
    get_session,
    list_dialogues,
    list_sessions,
    save_material,
    save_resume,
    save_user_message,
    skip_interview_item,
    should_advance,
    start_interview,
    update_session,
)
from app.modules.interview.stages import Stage, next_stage

__all__ = [
    "Stage",
    "next_stage",
    "create_session",
    "get_session",
    "list_sessions",
    "save_resume",
    "save_material",
    "update_session",
    "start_interview",
    "generate_next",
    "get_interview_progress",
    "advance_stage",
    "complete_interview",
    "should_advance",
    "save_user_message",
    "skip_interview_item",
    "list_dialogues",
]
