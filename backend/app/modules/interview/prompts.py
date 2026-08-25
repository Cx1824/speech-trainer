"""面试 Prompt 模板。

每个阶段一个 Prompt，输入是上下文（岗位/职级/简历/历史对话），输出是 AI 的下一句话。
风格由 InterviewStyle 决定，整体替换 system prompt。
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.interview.stages import Stage
from app.modules.interview.styles import InterviewStyle, get_style

# 默认风格（兼容旧调用）
DEFAULT_STYLE = InterviewStyle.PROFESSIONAL


def _format_resume(resume: dict[str, Any] | None) -> str:
    if not resume:
        return "（候选人未上传简历，请基于岗位和职级出题）"
    parts = []
    basics = resume.get("basics") or {}
    if basics:
        parts.append(f"基本信息：{basics.get('name', '')} - {basics.get('summary', '')}")
    work = resume.get("work") or []
    if work:
        parts.append("工作经历：" + " | ".join([f"{w.get('company','')}:{w.get('title','')}" for w in work[:5]]))
    projects = resume.get("projects") or []
    if projects:
        parts.append("项目经历：" + " / ".join([f"{p.get('name','')}" for p in projects[:5]]))
    skills = resume.get("skills") or []
    if skills:
        parts.append("技能：" + ", ".join(skills[:15]))
    return "\n".join(parts) if parts else "（简历解析为空）"


def _format_history(dialogues: list[dict]) -> str:
    if not dialogues:
        return "（无）"
    return "\n".join([f"{'面试官' if d['role']=='ai' else '候选人'}：{d['text']}" for d in dialogues[-10:]])


def _format_extended_history(dialogues: list[dict]) -> str:
    """结构化面试保留更长历史，避免后半程遗忘已经覆盖的问题。"""
    if not dialogues:
        return "（无）"
    return "\n".join(
        f"{'面试官' if dialogue['role'] == 'ai' else '候选人'}：{dialogue['text']}"
        for dialogue in dialogues[-24:]
    )


def _format_jd(company: str, jd_content: str) -> str:
    """格式化 JD 信息。"""
    parts = []
    if company:
        parts.append(f"目标公司：{company}")
    if jd_content:
        # 截断过长的 JD
        content = jd_content[:2000] + ("..." if len(jd_content) > 2000 else "")
        parts.append(f"岗位 JD：\n{content}")
    return "\n".join(parts) if parts else ""


def build_messages(
    stage: Stage,
    *,
    position: str,
    level: str,
    resume: dict[str, Any] | None,
    dialogues: list[dict],
    style: InterviewStyle | str = DEFAULT_STYLE,
    company: str = "",
    jd_content: str = "",
) -> list[dict]:
    """根据阶段构建 LLM messages。"""
    style_profile = get_style(style)
    system_prompt = style_profile.system_prompt
    resume_text = _format_resume(resume)
    history_text = _format_history(dialogues)
    jd_text = _format_jd(company, jd_content)

    # JD 上下文（如果有）
    jd_section = f"\n{jd_text}\n" if jd_text else ""

    if stage == Stage.OPENING:
        user = f"""岗位：{position}
职级：{level}{jd_section}
候选人简历：
{resume_text}

请用一句话自然地开场（包含问候、自我介绍为面试官、说明今天的面试流程概要）。"""
    elif stage == Stage.SELF_INTRO:
        user = f"""岗位：{position}
职级：{level}{jd_section}

现在进入「自我介绍」环节。请要求候选人做一个 2-3 分钟的自我介绍。"""
    elif stage == Stage.PROJECT:
        user = f"""岗位：{position}
职级：{level}{jd_section}
候选人简历：
{resume_text}

面试历史：
{history_text}

现在进入「项目追问」环节。请基于候选人简历中最相关的 1 个项目，问一个深入的问题（如：技术选型原因、遇到的难点、量化成果等）。如果目标岗位 JD 中有明确的能力要求，请围绕这些要求选择项目。一次只问一个问题。"""
    elif stage == Stage.POSITION:
        user = f"""岗位：{position}
职级：{level}{jd_section}

面试历史：
{history_text}

现在进入「岗位能力题」环节。请针对「{position}」岗位的 {level} 级别问一个能力考察题。如果有 JD，请优先从 JD 中提取能力要求出题。一次只问一个问题。"""
    elif stage == Stage.QA:
        user = f"""面试历史：
{history_text}

现在进入「反问环节」。请邀请候选人提问，并表示会简短回答。"""
    else:
        user = "面试已结束。"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


def build_planned_messages(
    *,
    position: str,
    level: str,
    resume: dict[str, Any] | None,
    dialogues: list[dict],
    style: InterviewStyle | str,
    company: str,
    jd_content: str,
    item: dict[str, Any],
    is_followup: bool,
    covered_labels: list[str],
    remaining_labels: list[str],
    question_candidate: dict[str, Any] | None = None,
) -> list[dict]:
    """根据控制器指定的能力维度生成一道问题。

    模型只负责把既定意图写成自然问题；是否追问、何时换题和何时结束
    均由面试计划控制，不能由模型自由延长单一主题。
    """
    style_prompt = get_style(style).system_prompt
    resume_text = _format_resume(resume)
    history_text = _format_extended_history(dialogues)
    jd_text = _format_jd(company, jd_content) or "（未提供公司或 JD）"
    candidate_text = ""
    if question_candidate:
        candidate_text = (
            "\n岗位题库候选："
            f"{question_candidate.get('content', '')}\n"
            "可以结合上下文自然改写，但不得改变本轮能力维度。"
        )
    turn_instruction = (
        "这是当前主问题的唯一一次必要追问。只补齐一个最关键缺口；"
        "如果候选人已明确表示没有精确数据或没有相关经历，接受这个边界并换成经验范围、验证方式或复盘问题，禁止反复逼问同一数字。"
        if is_followup
        else (
            "这是新的主问题，必须切换到本轮指定能力维度，不得继续追问上一题的数字、措辞或同一事实。"
            + ("这是第一题，先用一句简短问候自然开场，再邀请候选人自我介绍。" if item.get("id") == "self_intro" else "")
        )
    )
    user = f"""目标岗位：{position}
职级：{level}
公司与 JD：
{jd_text}

候选人简历：
{resume_text}

已覆盖：{'、'.join(covered_labels) or '尚未覆盖'}
后续仍需覆盖：{'、'.join(remaining_labels) or '无'}
本轮能力维度：{item['label']}
本轮问题意图：{item['goal']}
问题类型：{'追问' if is_followup else '主问题'}

面试历史：
{history_text}
{candidate_text}

{turn_instruction}
只输出面试官此刻要说的一段自然中文。一次只问一个核心问题，不要加“面试官：”、阶段名称、评分、解释或 Markdown。"""
    return [
        {
            "role": "system",
            "content": (
                f"{style_prompt}"
                "面试计划的覆盖范围和换题边界高于风格要求：风格只能改变语气与追问力度，不能让你重复同一问题、跳过其他能力维度或自行结束面试。"
            ),
        },
        {"role": "user", "content": user},
    ]


RESUME_PARSE_PROMPT = """请解析以下简历文本，提取为结构化 JSON，字段如下：
{
  "basics": {"name": "", "summary": ""},
  "education": [{"school": "", "major": "", "degree": "", "period": ""}],
  "work": [{"company": "", "title": "", "period": "", "description": ""}],
  "projects": [{"name": "", "role": "", "description": "", "highlights": []}],
  "skills": ["技能1", "技能2"],
  "position_guess": "最匹配的岗位，如：产品经理",
  "level_guess": "最匹配的职级，如：中级"
}

只返回 JSON，不要解释。简历文本：
"""
