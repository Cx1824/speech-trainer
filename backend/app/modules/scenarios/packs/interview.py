"""面试场景包。

包装现有 interview/prompts.py 的构建逻辑，行为与改造前完全一致，
保证存量面试会话不受影响。
"""

from __future__ import annotations

from app.modules.interview.prompts import build_messages as build_interview_messages
from app.modules.interview.stages import Stage
from app.modules.interview.styles import get_style
from app.modules.scenarios.base import (
    EvaluationAxis,
    EvaluationProfile,
    RubricAnchor,
    ScenarioContext,
    ScenarioPack,
    ScenarioStage,
    ScoreGate,
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
    evaluation=EvaluationProfile(
        version="interview-v4",
        reviewer_prompt=(
            "你是一名资深面试教练。只依据系统提供的面试训练记录、给定的当前岗位/岗位方向和表达事实进行评价，"
            "严格按照各评价轴的四档评分锚点给分，并为每个语义轴引用可核对的回答原文证据；"
            "没有足够文本证据时降低置信度，不得补写或臆测。重点判断回答是否回应训练记录中的当前问题、是否提供本人行动与结果、"
            "以及与目标岗位的可见关联；不推测候选人的心理状态、性格或真实工作能力。"
            "如果回答与当前问题和目标岗位完全无关、完全没有回应关键任务或无法找到任何任务关联，必须评为低于最低 40 分锚点，"
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
                "response_structure", "回答结构", 25, "llm",
                "是否直接回应当前问题，并以 STAR 或等价结构组织背景、任务、行动和结果。",
                anchors=(
                    RubricAnchor(90, "先直接回答问题，再用清楚的背景/任务、本人行动和结果形成完整因果链，重点突出且容易复述。"),
                    RubricAnchor(75, "回答了当前问题，整体有结论和案例结构，大部分 STAR 要素齐全，但有一项较薄或细节顺序略乱。"),
                    RubricAnchor(60, "能够触及问题，但结构不完整，背景、行动和结果散落在叙述中，听者需要自行整理重点。"),
                    RubricAnchor(40, "回避或误解当前问题，主要罗列经历或泛泛表态，无法从回答中还原清楚的行动和结果链条。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "evidence_results", "案例、行动与结果证据", 20, "llm",
                "是否提供本人承担的具体行动和可核查结果，并说明范围、难点、影响或衡量方式。",
                anchors=(
                    RubricAnchor(90, "给出与问题直接相关的具体案例，清楚区分本人责任和团队工作，说明关键行动、难点及可核查结果或指标。"),
                    RubricAnchor(75, "有具体案例和本人行动，也说明了结果，但量化程度、背景范围或个人贡献边界仍有部分缺口。"),
                    RubricAnchor(60, "主要描述职责、过程或观点，行动细节和结果证据较少，个人贡献与实际影响不够清楚。"),
                    RubricAnchor(40, "没有具体案例或本人行动结果，无法用回答中的事实支撑能力判断，主要是空泛自我评价。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "job_relevance", "岗位相关性", 25, "llm",
                "回答是否回应当前问题，并与系统给定的目标岗位或岗位方向建立可见关联；只依据训练记录中出现的内容评价。",
                anchors=(
                    RubricAnchor(90, "直接回应问题，并明确把回答中的能力、行动和结果连接到给定目标岗位或岗位方向，关联具体且可从训练记录核对。"),
                    RubricAnchor(75, "回答与问题和目标岗位大部分相关，能看出能力迁移关系，但部分关联需要听者自行补全或夹杂少量泛化内容。"),
                    RubricAnchor(60, "回答与问题有一定联系，但与目标岗位的关联较弱或证据有限，更多是在讲通用经历，匹配点不够清楚。"),
                    RubricAnchor(40, "仍能从少量内容中找到与当前问题或目标岗位的关联，但回答大多泛泛而谈，未形成清楚的匹配证据。"),
                ),
                min_evidence=1,
            ),
            EvaluationAxis(
                "followup_response", "追问回应", 10, "llm",
                "面对面试官追问是否补充新证据、澄清前述答案并保持前后一致。",
                anchors=(
                    RubricAnchor(90, "直接回应追问，补充了新的具体证据或限制条件，能与前述答案保持一致并把问题闭环。"),
                    RubricAnchor(75, "基本回应追问，提供了部分补充信息，只有轻微重复、遗漏或前后衔接问题。"),
                    RubricAnchor(60, "只部分回应追问，主要重复原答案或给出间接表述，仍未解决追问关注的事实。"),
                    RubricAnchor(40, "忽略、回避或答非所问，或者与前述答案明显冲突却没有解释。"),
                ),
                min_evidence=1,
            ),
        ),
        advice_sections=(
            "表达连贯性：根据口癖、紧邻重复和局部表达断裂，指出最值得重练的回答片段。",
            "语速与节奏：报告实际发言速率，并在文本同时出现断裂时分析短停顿和长停顿。",
            "回答结构：指出结论、背景/任务、行动和结果缺失的位置，并给出 STAR 或等价结构的重写示例。",
            "案例、行动与结果证据：区分个人贡献、具体行动和可核查结果，列出需要补充的事实。",
            "岗位相关性：说明回答与当前问题、给定目标岗位或岗位方向的对应关系；信息不足时明确标注证据边界。",
            "追问回应：指出没有补充证据或没有闭环的问题，并给出直接回答的替换话术。",
        ),
        score_gates=(
            ScoreGate(
                axis_key="job_relevance",
                below=40,
                max_overall=59,
                reason="回答与当前问题或目标岗位的相关性不足，当前综合评分最高为 59 分；请先正面回答问题，再用相关经历和结果建立岗位关联。",
            ),
        ),
    ),
    needs_resume=True,
    needs_material=False,
    timed=False,
    interrupt_allowed=True,
)
