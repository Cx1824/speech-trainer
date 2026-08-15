"""场景包公共定义。

场景包（ScenarioPack）是「面试/工作汇报/演讲」等训练场景的配置单元。
核心链路（收音-实时反馈-报告）复用，场景差异全部收敛在本包内：
- stages：阶段流转（状态机）
- prompts：LLM prompt 构建
- report_focus：报告分析侧重与专业建议模板
- 分析维度侧重：交给 analysis 模块按 scenario 加权

新增场景 = 新增一个 ScenarioPack 并注册，核心模块零改动（开闭原则）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ScenarioStage:
    """场景阶段定义。

    prompt_builder: (ctx: ScenarioContext) -> list[dict] LLM messages
    """

    key: str                                  # 阶段标识（存 DB current_stage）
    name: str                                 # 中文名（前端展示）
    question_limit: int                       # 本阶段 AI 发言次数上限（0=不生成）
    prompt_builder: Callable[["ScenarioContext"], list[dict]]


@dataclass(frozen=True)
class ReportFocus:
    """报告分析侧重点。

    dimensions: 该场景重点关注的表达维度
                （key 需与 analysis/text_rules.py 输出对齐：
                 filler/repetition/hedge/uncertain/long_sentence/emotion）
    advice_sections: 报告专业建议章节框架（LLM 生成时的指引）
    """

    dimensions: list[str]
    advice_sections: list[str]


@dataclass
class ScenarioContext:
    """构建 prompt 的上下文（从会话行提取）。"""

    position: str = ""            # 主题/岗位（场景不同含义不同）
    level: str = ""
    company: str = ""
    jd_content: str = ""
    duration_limit: int = 0       # 分钟，0=不限
    resume: dict[str, Any] | None = None
    material_text: str = ""       # 汇报/演讲材料
    history: list[dict] = field(default_factory=list)  # [{"role": "ai"/"user", "text": ""}]


@dataclass(frozen=True)
class ScenarioPack:
    """一个训练场景的完整配置。"""

    key: str                          # interview / presentation / speech
    name: str                         # 中文名
    role_name: str                    # AI 角色名（面试官/评审/主持人）
    description: str                  # 场景说明（前端卡片文案）
    stages: tuple[ScenarioStage, ...]
    report_focus: ReportFocus
    needs_resume: bool = True         # 是否需要简历
    needs_material: bool = False      # 是否支持上传材料
    timed: bool = False               # 是否为限时演讲型场景（用户自选时长+计时器）
    interrupt_allowed: bool = True    # AI 是否中途发言（演讲场景 False）


# ---- 通用工具（供各场景 prompt_builder 复用） ----

def format_history(ctx: ScenarioContext, speaker_ai: str, speaker_user: str, limit: int = 10) -> str:
    if not ctx.history:
        return "（无）"
    return "\n".join(
        [f"{speaker_ai if d['role']=='ai' else speaker_user}：{d['text']}" for d in ctx.history[-limit:]]
    )


def format_material(ctx: ScenarioContext, max_len: int = 3000) -> str:
    """格式化上传材料文本。"""
    if not ctx.material_text:
        return "（未上传材料）"
    text = ctx.material_text
    if len(text) > max_len:
        text = text[:max_len] + f"...（已截断，共{len(ctx.material_text)}字）"
    return text
