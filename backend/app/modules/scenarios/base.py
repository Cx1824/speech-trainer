"""场景包公共定义。

场景包（ScenarioPack）是「面试/工作汇报/演讲」等训练场景的配置单元。
核心链路（收音-实时反馈-事实分析）复用，场景差异全部收敛在本包内：
- stages：阶段流转（状态机）
- prompts：LLM prompt 构建
- evaluation：场景评价维度、权重、评审口径与建议模板

新增场景 = 新增一个 ScenarioPack 并注册，核心模块零改动（开闭原则）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Callable, Literal


@dataclass(frozen=True)
class RubricAnchor:
    """语义评价轴的一个可审计分数锚点。"""

    score: int
    description: str

    def __post_init__(self) -> None:
        if isinstance(self.score, bool) or not isinstance(self.score, int):
            raise ValueError("评分锚点的 score 必须是整数")
        if not 0 <= self.score <= 100:
            raise ValueError("评分锚点的 score 必须在 0 到 100 之间")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("评分锚点的 description 不能为空")


@dataclass(frozen=True)
class ScoreGate:
    """关键任务失败时对综合分施加的非补偿性上限。"""

    axis_key: str
    below: float
    max_overall: float
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.axis_key, str) or not self.axis_key.strip():
            raise ValueError("评分约束的 axis_key 不能为空")
        if isinstance(self.below, bool) or not isinstance(self.below, (int, float)):
            raise ValueError("评分约束的 below 必须是数字")
        if not isfinite(float(self.below)) or not 0 <= self.below <= 100:
            raise ValueError("评分约束的 below 必须在 0 到 100 之间")
        if isinstance(self.max_overall, bool) or not isinstance(
            self.max_overall, (int, float)
        ):
            raise ValueError("评分约束的 max_overall 必须是数字")
        if not isfinite(float(self.max_overall)) or not 0 <= self.max_overall <= 100:
            raise ValueError("评分约束的 max_overall 必须在 0 到 100 之间")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("评分约束的 reason 不能为空")


@dataclass(frozen=True)
class ScenarioStage:
    """场景阶段定义。

    prompt_builder: (ctx: ScenarioContext) -> list[dict] LLM messages
    """

    key: str                                  # 阶段标识（存 DB current_stage）
    name: str                                 # 中文名（前端展示）
    question_limit: int                       # 本阶段 AI 发言次数上限（0=不生成）
    prompt_builder: Callable[["ScenarioContext"], list[dict]]
    fallback_builder: Callable[["ScenarioContext"], str] | None = None


@dataclass(frozen=True)
class EvaluationAxis:
    """单条场景评价轴。

    ``signal`` 轴读取共享分析层的确定性指标；``llm`` 轴只负责需要
    语义理解的场景评价。``signal_key`` 仅对 signal 轴有意义。
    """

    key: str
    label: str
    weight: int
    source: Literal["signal", "llm"]
    description: str
    signal_key: str = ""
    anchors: tuple[RubricAnchor, ...] = ()
    min_evidence: int = 1

    def __post_init__(self) -> None:
        if not self.key or not self.label or not self.description:
            raise ValueError("评价轴的 key、label 和 description 不能为空")
        if self.weight <= 0:
            raise ValueError(f"评价轴 {self.key} 的权重必须大于 0")
        if self.source not in ("signal", "llm"):
            raise ValueError(f"评价轴 {self.key} 的 source 非法：{self.source}")
        if self.source == "signal" and not self.signal_key:
            raise ValueError(f"信号评价轴 {self.key} 必须声明 signal_key")
        if self.source == "llm" and self.signal_key:
            raise ValueError(f"语义评价轴 {self.key} 不应声明 signal_key")
        if isinstance(self.min_evidence, bool) or not isinstance(self.min_evidence, int):
            raise ValueError(f"评价轴 {self.key} 的 min_evidence 必须是整数")
        if self.min_evidence < 1:
            raise ValueError(f"评价轴 {self.key} 的 min_evidence 必须至少为 1")
        if self.source == "signal" and self.anchors:
            raise ValueError(f"信号评价轴 {self.key} 不应声明评分锚点")
        if self.source == "llm":
            if not all(isinstance(anchor, RubricAnchor) for anchor in self.anchors):
                raise ValueError(f"语义评价轴 {self.key} 的 anchors 类型非法")
            distinct_scores = {anchor.score for anchor in self.anchors}
            if len(distinct_scores) < 3:
                raise ValueError(f"语义评价轴 {self.key} 必须声明至少 3 个不同分值锚点")


@dataclass(frozen=True)
class EvaluationProfile:
    """一个场景的完整评价策略。

    共享分析层只产出事实；本配置决定这些事实如何参与当前场景的评价，
    以及 LLM 需要补充哪些语义维度。评价配置带版本号，便于报告复现。
    """

    version: str
    reviewer_prompt: str
    axes: tuple[EvaluationAxis, ...]
    advice_sections: tuple[str, ...]
    score_gates: tuple[ScoreGate, ...] = ()

    def __post_init__(self) -> None:
        if not self.version or not self.reviewer_prompt:
            raise ValueError("评价策略必须声明版本号和评审口径")
        if not self.axes:
            raise ValueError("评价策略至少需要一个评价轴")
        keys = [axis.key for axis in self.axes]
        if len(keys) != len(set(keys)):
            raise ValueError("同一评价策略中的轴 key 不能重复")
        if sum(axis.weight for axis in self.axes) != 100:
            raise ValueError("评价策略的轴权重之和必须为 100")
        if not any(axis.source == "signal" for axis in self.axes):
            raise ValueError("评价策略至少需要一个共享信号轴")
        if not any(axis.source == "llm" for axis in self.axes):
            raise ValueError("评价策略至少需要一个语义评价轴")
        llm_keys = {axis.key for axis in self.axes if axis.source == "llm"}
        for gate in self.score_gates:
            if not isinstance(gate, ScoreGate):
                raise ValueError("评价策略的 score_gates 类型非法")
            if gate.axis_key not in llm_keys:
                raise ValueError(
                    f"评分约束引用的轴 {gate.axis_key} 不属于本策略的语义评价轴"
                )


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
    evaluation: EvaluationProfile
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
