"""面试场景包。

包装现有 interview/prompts.py 的构建逻辑，行为与改造前完全一致，
保证存量面试会话不受影响。
"""

from __future__ import annotations

from app.modules.interview.prompts import build_messages as build_interview_messages
from app.modules.interview.stages import Stage
from app.modules.interview.styles import get_style
from app.modules.scenarios.base import (
    ReportFocus,
    ScenarioContext,
    ScenarioPack,
    ScenarioStage,
)


def _build(stage: Stage):
    def builder(ctx: ScenarioContext) -> list[dict]:
        return build_interview_messages(
            stage,
            position=ctx.position,
            level=ctx.level,
            resume=ctx.resume,
            dialogues=[{"role": d["role"], "text": d["text"]} for d in ctx.history],
            company=ctx.company,
            jd_content=ctx.jd_content,
        )
    return builder


# 风格 system prompt 沿用 interview/styles.py
pack = ScenarioPack(
    key="interview",
    name="模拟面试",
    role_name="面试官",
    description="全流程模拟面试：自我介绍、项目追问、岗位能力题、反问环节，AI 面试官实时提问。",
    stages=(
        ScenarioStage(key="opening", name="开场", question_limit=1, prompt_builder=_build(Stage.OPENING)),
        ScenarioStage(key="self_intro", name="自我介绍", question_limit=1, prompt_builder=_build(Stage.SELF_INTRO)),
        ScenarioStage(key="project", name="项目追问", question_limit=3, prompt_builder=_build(Stage.PROJECT)),
        ScenarioStage(key="position", name="岗位能力题", question_limit=3, prompt_builder=_build(Stage.POSITION)),
        ScenarioStage(key="qa", name="反问环节", question_limit=1, prompt_builder=_build(Stage.QA)),
        ScenarioStage(key="report", name="已结束", question_limit=0, prompt_builder=_build(Stage.REPORT)),
    ),
    report_focus=ReportFocus(
        dimensions=["filler", "repetition", "hedge", "uncertain", "long_sentence", "emotion"],
        advice_sections=[
            "岗位能力匹配度：回答内容与目标岗位要求的契合情况，亮点与短板",
            "表达改进：口头禅、重复用词、模糊表述的具体削减建议",
            "结构化建议：STAR 法则（情境-任务-行动-结果）的应用情况与改进",
            "自信度与情绪：紧张信号识别与心态调整建议",
            "下一步练习重点",
        ],
    ),
    needs_resume=True,
    needs_material=False,
    timed=False,
    interrupt_allowed=True,
)
