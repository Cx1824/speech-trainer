"""面试阶段与状态机。"""

from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    """面试阶段。"""

    OPENING = "opening"          # 开场
    SELF_INTRO = "self_intro"    # 自我介绍
    PROJECT = "project"          # 项目追问
    POSITION = "position"        # 岗位能力题
    QA = "qa"                    # 反问环节
    REPORT = "report"            # 已结束，进入报告


# 阶段流转顺序
STAGE_ORDER: list[Stage] = [
    Stage.OPENING,
    Stage.SELF_INTRO,
    Stage.PROJECT,
    Stage.POSITION,
    Stage.QA,
    Stage.REPORT,
]

# 每个阶段的问题数量上限（不含追问）
STAGE_QUESTION_LIMIT: dict[Stage, int] = {
    Stage.OPENING: 1,
    Stage.SELF_INTRO: 1,
    Stage.PROJECT: 3,
    Stage.POSITION: 3,
    Stage.QA: 1,
    Stage.REPORT: 0,
}


def next_stage(current: Stage) -> Stage:
    """获取下一阶段。"""
    idx = STAGE_ORDER.index(current)
    if idx + 1 >= len(STAGE_ORDER):
        return Stage.REPORT
    return STAGE_ORDER[idx + 1]
