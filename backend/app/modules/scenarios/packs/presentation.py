"""工作汇报场景包。

AI 扮演上级/评审：开场引导 → 汇报进行（用户主讲） → 评审质询 → 结束。
汇报主体由用户完成，AI 只在阶段切换时发言；质询阶段模拟上级追问。
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


def _fallback_opening(ctx: ScenarioContext) -> str:
    minutes = ctx.duration_limit or 5
    return f"准备好后，请开始本次工作汇报。建议先说结论，并留意 {minutes} 分钟的训练时长。"


def _fallback_qa(ctx: ScenarioContext) -> str:
    questions = (
        "你刚才的核心结论，有哪些可以核对的数据或事实支持？",
        "下一步准备怎样推进？请说明负责人、时间节点和衡量标准。",
        "这项工作的主要风险是什么，你准备采取什么应对动作？",
    )
    previous_ai_turns = sum(1 for item in ctx.history if item.get("role") == "ai")
    return questions[max(0, previous_ai_turns - 1) % len(questions)]


def _fallback_ending(ctx: ScenarioContext) -> str:
    return "本次工作汇报训练已结束。你的完整记录和本地表达分析已保存。"


pack = ScenarioPack(
    key="presentation",
    name="工作汇报",
    role_name="评审",
    description="模拟向上汇报/述职：支持上传汇报材料，练结论先行与数据支撑，AI 评审最后质询追问。",
    stages=(
        ScenarioStage(
            key="opening", name="开场", question_limit=1,
            prompt_builder=_opening, fallback_builder=_fallback_opening,
        ),
        ScenarioStage(key="presenting", name="汇报进行", question_limit=0, prompt_builder=_presenting),
        ScenarioStage(
            key="qa", name="评审质询", question_limit=3,
            prompt_builder=_qa, fallback_builder=_fallback_qa,
        ),
        ScenarioStage(
            key="ending", name="收尾", question_limit=1,
            prompt_builder=_ending, fallback_builder=_fallback_ending,
        ),
        ScenarioStage(key="report", name="已结束", question_limit=0, prompt_builder=_report),
    ),
    evaluation=EvaluationProfile(
        version="presentation-v4",
        reviewer_prompt=(
            "你是一名资深管理沟通教练。只依据系统提供的汇报训练记录、汇报主题和给定的表达事实进行评价，"
            "严格按照各评价轴的四档评分锚点给分，并为每个语义轴引用可核对的原文证据；"
            "没有足够文本证据时降低置信度，不得补写或臆测。重点判断汇报目标、结论、证据、行动风险和质询回应（若训练记录中有质询），"
            "不推测汇报人的心理状态，也不把预设时长或主观听感假装成已校准的评分。"
            "如果内容与汇报目标完全无关、完全没有回应关键任务或无法找到任何任务关联，必须评为低于最低 40 分锚点，"
            "不能为了贴合锚点而给 40 分。"
        ),
        axes=(
            EvaluationAxis(
                "continuity", "表达连贯性", 10, "signal",
                "依据明确口癖、紧邻重复和句子未说完整便停住或重来的事实评价，不推断紧张或心理状态。",
                "continuity",
            ),
            EvaluationAxis(
                "pacing", "语速与节奏", 10, "signal",
                "依据真实发言速率，以及有文本断裂佐证的停顿密度评价；不把正常修辞停顿直接当作卡顿。",
                "pacing",
            ),
            EvaluationAxis(
                "conclusion_structure", "结论与结构", 25, "llm",
                "是否回应汇报目标并结论先行，主体是否按清晰层次组织，结尾是否回到结论和目标。",
                anchors=(
                    RubricAnchor(90, "开头直接回答汇报目标并给出明确结论，主体按层次展开依据，结尾回到目标、结论和下一步，结构可快速复述。"),
                    RubricAnchor(75, "能够识别结论和汇报目标，主体大体有层次，部分关键信息出现较晚或层次衔接偏弱，但基本回应了目标。"),
                    RubricAnchor(60, "涉及汇报目标并包含若干内容段落，但没有稳定的结论先行结构，重点和层次需要听者自行整理。"),
                    RubricAnchor(40, "仍能从少量内容中找到与汇报目标或任务的关联，但没有形成结论先行结构，主体大多是零散叙述或过程流水账。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "evidence_quality", "数据与论据", 25, "llm",
                "关键结论是否由具体数据、事实、对比、来源或可核查行动结果支撑，并区分事实与判断。",
                anchors=(
                    RubricAnchor(90, "关键结论均有具体数据、事实、对比、来源或行动结果支撑，能说明口径并区分事实、判断和推测。"),
                    RubricAnchor(75, "有多处具体数据、案例或结果支撑主要结论，但部分口径、来源或关键论断仍不完整。"),
                    RubricAnchor(60, "以定性描述为主，只有零散数字或例子，证据与结论的对应关系不稳定，难以核验主要判断。"),
                    RubricAnchor(40, "几乎没有可核查的数据、事实或结果证据，主要依靠空泛判断，无法支撑汇报结论。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "action_risk", "行动、风险与下一步", 15, "llm",
                "下一步是否具体可执行，是否说明负责人、节点或衡量方式，并识别风险及应对动作。",
                anchors=(
                    RubricAnchor(90, "明确说明下一步行动、负责人、节点或衡量方式，同时识别主要风险并给出具体应对动作，执行路径清楚。"),
                    RubricAnchor(75, "下一步行动基本明确，至少给出部分节点、负责人或衡量方式，也提到主要风险，但操作细节仍有缺口。"),
                    RubricAnchor(60, "提到后续计划或风险，但行动缺少负责人、节点和衡量方式，风险多为罗列，缺少对应应对动作。"),
                    RubricAnchor(40, "没有可执行的下一步或风险意识，无法从汇报中判断后续如何推进和控制不确定性。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "qa_response", "质询回应", 15, "llm",
                "面对评审质询是否正面作答，是否基于汇报事实澄清口径、承认不确定性并补充证据或下一步。",
                anchors=(
                    RubricAnchor(90, "逐一正面回答质询，口径与汇报事实一致，主动澄清不确定性，并补充证据、限制条件或明确下一步。"),
                    RubricAnchor(75, "大多数质询都得到直接回答，少量回答存在重复、细节缺口或证据不足，但没有明显回避核心问题。"),
                    RubricAnchor(60, "只部分回应质询，较多复述原汇报或绕开问题，新增证据和口径澄清有限。"),
                    RubricAnchor(40, "未正面回答质询、持续回避关键问题，或回答与既有汇报相矛盾却没有解释。"),
                ),
                min_evidence=1,
            ),
        ),
        advice_sections=(
            "表达连贯性：根据口癖、紧邻重复和局部表达断裂，指出最值得重练的汇报片段。",
            "语速与节奏：报告实际发言速率，并在文本同时出现断裂时分析短停顿和长停顿。",
            "结论与结构：检查是否回应汇报目标、结论先行、层次清楚，并给出最优先的重排建议。",
            "数据与论据：列出缺少数据、口径或来源的关键论断，并给出可核验的补证建议。",
            "行动、风险与下一步：把空泛计划改写为包含负责人、节点、衡量方式和风险应对的行动项。",
            "质询回应：指出没有正面回答的问题，并给出基于事实、限制条件和下一步的替换话术。",
        ),
        score_gates=(
            ScoreGate(
                axis_key="conclusion_structure",
                below=40,
                max_overall=59,
                reason="汇报没有清楚回应目标或形成可识别的结论结构，当前综合评分最高为 59 分；请先明确目标与结论，再组织依据和行动。",
            ),
        ),
    ),
    needs_resume=False,
    needs_material=True,
    timed=True,
    interrupt_allowed=True,
)
