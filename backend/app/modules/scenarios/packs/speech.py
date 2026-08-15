"""演讲场景包。

纯演讲训练：AI 主持人只做开场引入与结束致谢，中途不打断、不提问。
核心价值在于限时表达训练 + 实时表达反馈 + 演讲专业维度报告。
"""

from __future__ import annotations

from app.modules.scenarios.base import (
    ReportFocus,
    ScenarioContext,
    ScenarioPack,
    ScenarioStage,
    format_history,
    format_material,
)

SYSTEM_PROMPT = """你是一位专业的演讲活动主持人。台风稳、语言精炼。
你只负责引入演讲者和结束致谢，绝不打断演讲过程、不提问。单次发言不超过 3 句话。"""


def _opening(ctx: ScenarioContext) -> list[dict]:
    user = f"""演讲主题：{ctx.position or '自由演讲'}
演讲时长要求：{ctx.duration_limit or 5} 分钟
演讲材料/大纲：
{format_material(ctx)}

请用 2 句话开场：介绍今天的演讲主题与演讲者，宣布演讲开始并提示计时。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _presenting(ctx: ScenarioContext) -> list[dict]:
    user = "（演讲进行中，主持人和观众都在聆听，无需发言。）"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _ending(ctx: ScenarioContext) -> list[dict]:
    user = f"""演讲主题：{ctx.position or '自由演讲'}
演讲历史：
{format_history(ctx, '主持人', '演讲者')}

演讲已结束。请用 2 句话收尾：感谢演讲者，预告详细的表现分析报告。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _report(ctx: ScenarioContext) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "已结束。"}]


pack = ScenarioPack(
    key="speech",
    name="演讲训练",
    role_name="主持人",
    description="限时演讲实战：自选时长倒计时，AI 主持人开场收尾不打断，练节奏、感染力与金句表达。",
    stages=(
        ScenarioStage(key="opening", name="开场", question_limit=1, prompt_builder=_opening),
        ScenarioStage(key="presenting", name="演讲进行", question_limit=0, prompt_builder=_presenting),
        ScenarioStage(key="ending", name="收尾", question_limit=1, prompt_builder=_ending),
        ScenarioStage(key="report", name="已结束", question_limit=0, prompt_builder=_report),
    ),
    report_focus=ReportFocus(
        dimensions=["filler", "repetition", "hedge", "long_sentence", "emotion"],
        advice_sections=[
            "感染力：情绪能量曲线、语气起伏，与观众建立连接的手法",
            "节奏控制：语速、停顿使用、长短句搭配是否形成韵律",
            "结构设计：开场钩子-主体层次-结尾升华是否完整，转折是否自然",
            "金句打造：提炼演讲中可复用的 2-3 句核心表达并给出打磨建议",
            "时间掌控：实际用时 vs 预设时长，超时/过早结束的改善策略",
        ],
    ),
    needs_resume=False,
    needs_material=True,
    timed=True,
    interrupt_allowed=False,
)
