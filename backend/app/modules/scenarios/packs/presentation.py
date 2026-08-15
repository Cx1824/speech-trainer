"""工作汇报场景包。

AI 扮演上级/评审：开场引导 → 汇报进行（用户主讲） → 评审质询 → 结束。
汇报主体由用户完成，AI 只在阶段切换时发言；质询阶段模拟上级追问。
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

SYSTEM_PROMPT = """你是一位经验丰富、要求严格的上级评审。你正在听取下属的工作汇报。
你的特点是：关注结论先行、数据支撑、逻辑结构；对空泛表述和缺乏量化结果的内容敏感；
提问直接但克制，每次只问一个问题。所有发言口语自然，单次发言不超过 3 句话。"""


def _opening(ctx: ScenarioContext) -> list[dict]:
    user = f"""汇报主题：{ctx.position or '工作汇报'}
汇报材料：
{format_material(ctx)}

请用一句话开场：以评审身份表示欢迎，提醒汇报人可以开始，并提示汇报注意时间（约 {ctx.duration_limit or 5} 分钟）。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _presenting(ctx: ScenarioContext) -> list[dict]:
    user = "（汇报进行中，无需发言。）"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _qa(ctx: ScenarioContext) -> list[dict]:
    user = f"""汇报主题：{ctx.position or '工作汇报'}
汇报材料：
{format_material(ctx, max_len=1500)}

汇报历史：
{format_history(ctx, '评审', '汇报人')}

汇报人已讲完。请基于其汇报内容，站在上级视角问 1 个最关键的质询问题（如：数据口径、投入产出、风险应对、下一步计划的可信度等）。一次只问一个问题。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _ending(ctx: ScenarioContext) -> list[dict]:
    user = f"""汇报历史：
{format_history(ctx, '评审', '汇报人')}

质询环节已结束。请用 1-2 句话收尾：感谢汇报，预告详细反馈将在训练报告中给出。"""
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _report(ctx: ScenarioContext) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "已结束。"}]


pack = ScenarioPack(
    key="presentation",
    name="工作汇报",
    role_name="评审",
    description="模拟向上汇报/述职：支持上传汇报材料，练结论先行与数据支撑，AI 评审最后质询追问。",
    stages=(
        ScenarioStage(key="opening", name="开场", question_limit=1, prompt_builder=_opening),
        ScenarioStage(key="presenting", name="汇报进行", question_limit=0, prompt_builder=_presenting),
        ScenarioStage(key="qa", name="评审质询", question_limit=3, prompt_builder=_qa),
        ScenarioStage(key="ending", name="收尾", question_limit=1, prompt_builder=_ending),
        ScenarioStage(key="report", name="已结束", question_limit=0, prompt_builder=_report),
    ),
    report_focus=ReportFocus(
        dimensions=["filler", "repetition", "hedge", "long_sentence", "emotion"],
        advice_sections=[
            "金字塔结构：结论是否先行、分层是否清晰（结论-依据-行动）",
            "数据支撑：量化表达占比、缺数据的论断清单与补数建议",
            "质询应对：面对追问时的应答质量——是否正面回答、是否防御性表述",
            "表达改进：口头禅、模糊词（'大概/可能/还行'）在汇报中的危害与替换话术",
            "时间控制：实际用时与预设时长的偏差分析",
        ],
    ),
    needs_resume=False,
    needs_material=True,
    timed=True,
    interrupt_allowed=True,
)
