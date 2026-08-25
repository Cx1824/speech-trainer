"""演讲场景包。

纯演讲训练：AI 主持人只做开场引入与结束致谢，中途不打断、不提问。
核心价值在于限时表达训练 + 实时表达反馈 + 演讲专业维度报告。
"""

from __future__ import annotations

from app.modules.scenarios.base import (
    EvaluationAxis,
    EvaluationProfile,
    RubricAnchor,
    ScenarioContext,
    ScenarioPack,
    ScenarioStage,
    ScoreGate,
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
    description="限时演讲实战：自选时长倒计时，AI 主持人开场收尾不打断，练主题主张、结构推进与修辞表达。",
    stages=(
        ScenarioStage(key="opening", name="开场", question_limit=1, prompt_builder=_opening),
        ScenarioStage(key="presenting", name="演讲进行", question_limit=0, prompt_builder=_presenting),
        ScenarioStage(key="ending", name="收尾", question_limit=1, prompt_builder=_ending),
        ScenarioStage(key="report", name="已结束", question_limit=0, prompt_builder=_report),
    ),
    evaluation=EvaluationProfile(
        version="speech-v4",
        reviewer_prompt=(
            "你是一名资深中文演讲教练。只依据系统提供的演讲训练记录、演讲主题和给定的表达事实进行评价，"
            "严格按照各评价轴的四档评分锚点给分，并为每个语义轴引用可核对的原文证据；"
            "没有足够文本证据时降低置信度，不得补写或臆测。你不能评价看不到的观众反应、台风、"
            "感染效果或演讲者的心理状态；修辞表达只评价训练记录中可见的故事、例子、对比、回扣、号召等设计。"
            "如果内容与主题完全无关、没有任何可辨识的核心主张或完全没有完成关键任务，必须评为低于最低 40 分锚点，"
            "不能为了贴合锚点而给 40 分。"
        ),
        axes=(
            EvaluationAxis(
                "continuity", "表达连贯性", 15, "signal",
                "依据明确口癖、紧邻重复和句子未说完整便停住或重来的事实评价，不推断紧张或台风。",
                "continuity",
            ),
            EvaluationAxis(
                "pacing", "语速与节奏", 10, "signal",
                "依据真实发言速率，以及有文本断裂佐证的停顿密度评价；不把正常修辞停顿直接当作卡顿。",
                "pacing",
            ),
            EvaluationAxis(
                "topic_alignment", "主题契合与核心主张", 25, "llm",
                "核心主张是否明确，主体内容是否持续服务演讲主题，结尾是否完成主题回扣。",
                anchors=(
                    RubricAnchor(90, "提出明确、可复述的核心主张，主体始终服务于主张，例证和结尾都有效回扣主题，未见明显跑题段落。"),
                    RubricAnchor(75, "核心主张基本明确，大部分内容围绕主题，只有少量展开与主题关联较弱，但不影响听者理解主线。"),
                    RubricAnchor(60, "能看出主题方向，但主张不够集中，存在较多泛泛或旁支内容，听者需要自行归纳重点。"),
                    RubricAnchor(40, "仍能从少量内容中找到与主题或任务的关联，但核心主张没有形成，主体大部分偏离，结尾也没有完成主题回扣。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "speech_structure", "演讲结构", 30, "llm",
                "开场、主体层次、转折推进和结尾收束是否形成可跟随的演讲结构。",
                anchors=(
                    RubricAnchor(90, "有有效开场、清楚的主体层次、自然的转折推进和有功能的结尾收束，听者能顺着主线理解论述。"),
                    RubricAnchor(75, "整体具备开场、主体和结尾，大部分层次清楚，局部出现跳跃或转折生硬，但不破坏整体推进。"),
                    RubricAnchor(60, "能分辨若干内容段落，但组织松散，开头或结尾功能偏弱，段落之间的推进关系不稳定。"),
                    RubricAnchor(40, "缺少可辨识的演讲结构，内容主要堆叠或频繁跳跃，难以从文本中还原清晰主线。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "rhetorical_design", "修辞表达与听众导向", 20, "llm",
                "只评价文本中可见的故事、具体例子、对比、回扣、号召等表达设计及其与主张的组织关系，"
                "不把真实听众反应或主观感染力当作证据。",
                anchors=(
                    RubricAnchor(90, "文本中有组织地使用故事、具体例子、对比、回扣、号召等多种可见手法，且明确服务主张，形成可复用的表达记忆点。"),
                    RubricAnchor(75, "文本中有故事、例子、对比等可见表达手法，基本服务于主张，局部略显常规或衔接不够紧密。"),
                    RubricAnchor(60, "偶有例子或修辞，但使用零散，主体仍以说明和陈述为主，表达设计对主线的帮助有限。"),
                    RubricAnchor(40, "几乎只有抽象陈述或信息罗列，文本中未见可辨识的故事、例子、对比、回扣或号召设计。"),
                ),
                min_evidence=1,
            ),
        ),
        advice_sections=(
            "表达连贯性：根据口癖、紧邻重复和局部表达断裂，指出最值得重练的片段。",
            "语速与节奏：报告实际发言速率，并在文本同时出现断裂时分析短停顿和长停顿。",
            "主题契合与核心主张：复述核心主张，指出偏离主题或未完成回扣的原文片段。",
            "演讲结构：检查开场、主体层次、转折推进和结尾收束，并给出一处最优先的重排建议。",
            "修辞表达与听众导向：从故事、例子、对比、回扣、号召等文本证据中提炼可复用表达设计。",
        ),
        score_gates=(
            ScoreGate(
                axis_key="topic_alignment",
                below=40,
                max_overall=59,
                reason="核心主张与演讲主题的契合度不足，当前综合评分最高为 59 分；请先明确主张并让主体内容围绕它展开。",
            ),
        ),
    ),
    needs_resume=False,
    needs_material=True,
    timed=True,
    interrupt_allowed=False,
)
