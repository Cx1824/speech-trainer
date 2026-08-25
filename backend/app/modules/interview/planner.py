"""结构化面试计划与覆盖控制。

面试模式决定“练什么”，强度决定“练多深”，风格只决定“怎么问”。
所有自动结束和追问都由本模块的确定性边界控制，避免模型在单一主题上无限深挖。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any


PLAN_VERSION = "interview-plan-v1"

MODE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "full": {
        "name": "全流程模拟",
        "description": "依次练习 HR 筛选、项目经历、专业能力、行为问题和候选人反问。",
        "recommended": True,
        "estimates": {"quick": (12, 18), "standard": (25, 35), "deep": (40, 55)},
    },
    "hr": {
        "name": "HR 初面",
        "description": "集中练习基础筛选、求职动机、离职原因、稳定性、自我认知和现实条件。",
        "estimates": {"quick": (6, 8), "standard": (10, 15), "deep": (18, 25)},
    },
    "professional": {
        "name": "专业 / 业务面",
        "description": "根据岗位、JD 和经历覆盖多个专业能力维度与业务情境。",
        "estimates": {"quick": (10, 15), "standard": (18, 25), "deep": (30, 40)},
    },
    "project": {
        "name": "项目深挖",
        "description": "围绕最多两个项目练角色、决策、结果和失败复盘，深挖但不死磕单点。",
        "estimates": {"quick": (8, 12), "standard": (12, 18), "deep": (20, 30)},
    },
    "behavioral": {
        "name": "行为 / 管理面",
        "description": "练习冲突、失败、反馈、领导力、跨部门协作和职业判断。",
        "estimates": {"quick": (8, 12), "standard": (12, 18), "deep": (20, 25)},
    },
    "weakness": {
        "name": "上次报告补弱",
        "description": "根据最近一次报告选择薄弱维度，进行短时、针对性的重复训练。",
        "estimates": {"quick": (6, 8), "standard": (8, 12), "deep": (15, 20)},
    },
}

INTENSITY_DEFINITIONS: dict[str, dict[str, Any]] = {
    "quick": {"name": "快速", "description": "覆盖核心问题，基本不追问。", "followup_budget": 1},
    "standard": {"name": "标准", "description": "重要维度各问一次，必要时追问。", "followup_budget": 4},
    "deep": {"name": "深度", "description": "增加情境与证据追问，同时守住覆盖边界。", "followup_budget": 7},
}


def _item(
    key: str,
    stage: str,
    label: str,
    intent: str,
    goal: str,
    *,
    evidence_required: bool = False,
    followup: bool = False,
) -> dict[str, Any]:
    return {
        "id": key,
        "stage": stage,
        "label": label,
        "intent": intent,
        "goal": goal,
        "evidence_required": evidence_required,
        "followup_eligible": followup,
    }


ITEMS: dict[str, dict[str, Any]] = {
    "opening": _item("opening", "opening", "开场", "opening", "简短说明本次面试范围，不要求候选人回答。"),
    "self_intro": _item("self_intro", "self_intro", "自我介绍", "self_intro", "要求候选人用岗位相关经历和核心价值完成简洁自我介绍。"),
    "hr_motivation": _item("hr_motivation", "hr_screen", "求职动机", "motivation", "了解为什么选择当前岗位、公司或职业方向。", followup=True),
    "hr_foundation": _item(
        "hr_foundation",
        "hr_screen",
        "基础筛选",
        "hr_foundation",
        "检查候选人是否了解公司、岗位核心职责和自身匹配点；使用真实招聘中的基础判断题，不出脑筋急转弯或羞辱性问题。",
        followup=True,
    ),
    "hr_change": _item("hr_change", "hr_screen", "离职与稳定性", "career_change", "了解离职原因、职业连续性和下一份工作的真实期待。", followup=True),
    "hr_self_awareness": _item("hr_self_awareness", "hr_screen", "自我认知", "self_awareness", "了解优势、短板以及候选人如何降低短板对工作的影响。", followup=True),
    "hr_logistics": _item("hr_logistics", "hr_screen", "现实条件", "work_conditions", "确认到岗时间、薪酬预期、地点或出差等岗位相关条件；不要询问婚育、家庭等无关隐私。"),
    "hr_gap": _item("hr_gap", "hr_screen", "经历连续性", "career_gap", "针对空档、短任期或明显转向做中性澄清，不预设负面结论。", followup=True),
    "project_scope": _item("project_scope", "project", "项目角色", "project_scope", "选择最相关项目，讲清背景、本人角色、范围和目标。", evidence_required=True, followup=True),
    "project_impact": _item("project_impact", "project", "项目成果", "project_impact", "选择一个能体现岗位匹配的项目，说明本人行动和可核查结果。", evidence_required=True, followup=True),
    "project_decision": _item("project_decision", "project", "关键决策", "project_decision", "考察关键取舍、备选方案、判断依据和承担的责任。", evidence_required=True, followup=True),
    "project_results": _item("project_results", "project", "结果口径", "project_results", "考察结果如何衡量；没有精确数字时接受范围、验证方式或明确说明无数据。", evidence_required=True, followup=True),
    "project_failure": _item("project_failure", "project", "失败复盘", "project_failure", "考察项目中的失误、修正动作和后续方法变化。", evidence_required=True, followup=True),
    "project_second": _item("project_second", "project", "第二段经历", "project_second", "优先切换到另一个项目；若只有一个项目，必须换能力角度，不重复追同一事实。", evidence_required=True, followup=True),
    "professional_core": _item("professional_core", "professional", "核心专业能力", "role_core", "根据 JD 与职级选择尚未覆盖的第一项核心能力。", evidence_required=True, followup=True),
    "professional_scenario": _item("professional_scenario", "professional", "业务情境", "role_scenario", "给出贴近岗位的情境题，考察分析、行动顺序和判断标准。", evidence_required=True, followup=True),
    "professional_judgment": _item("professional_judgment", "professional", "专业判断", "role_judgment", "考察边界条件、风险权衡和升级机制，不重复前一能力点。", evidence_required=True, followup=True),
    "professional_cross": _item("professional_cross", "professional", "协同落地", "cross_function", "考察专业方案如何与业务、管理层或跨部门协同落地。", evidence_required=True, followup=True),
    "professional_foundation": _item("professional_foundation", "professional", "基础判断", "role_foundation", "用一道岗位基础题检查核心常识和准备程度，不出脑筋急转弯。", followup=True),
    "behavior_conflict": _item("behavior_conflict", "behavioral", "冲突处理", "conflict", "要求用真实经历说明分歧、沟通动作和最终结果。", evidence_required=True, followup=True),
    "behavior_failure": _item("behavior_failure", "behavioral", "失败与复盘", "failure", "要求说明一次未达预期的经历、责任归因和行为变化。", evidence_required=True, followup=True),
    "behavior_feedback": _item("behavior_feedback", "behavioral", "反馈处理", "feedback", "考察面对负面反馈时如何验证、调整并形成结果。", evidence_required=True, followup=True),
    "behavior_leadership": _item("behavior_leadership", "behavioral", "领导与推动", "leadership", "考察无权威影响、团队推动或艰难决策。", evidence_required=True, followup=True),
    "behavior_ethics": _item("behavior_ethics", "behavioral", "职业底线", "ethics", "用岗位相关情境考察诚信、合规和升级判断，避免道德羞辱。", followup=True),
    "qa": _item("qa", "qa", "候选人反问", "candidate_qa", "邀请候选人提出一个最关心的问题并简短回应。"),
}


STANDARD_ITEM_IDS: dict[str, list[str]] = {
    "full": [
        "self_intro", "hr_foundation", "hr_motivation", "hr_change", "project_impact", "project_second",
        "professional_core", "professional_scenario", "professional_judgment", "behavior_conflict",
        "behavior_failure", "qa",
    ],
    "hr": [
        "self_intro", "hr_foundation", "hr_motivation", "hr_change", "hr_self_awareness", "behavior_conflict",
        "hr_logistics", "qa",
    ],
    "professional": [
        "self_intro", "professional_foundation", "professional_core", "professional_scenario",
        "professional_judgment", "project_impact", "professional_cross", "qa",
    ],
    "project": [
        "self_intro", "project_scope", "project_decision", "project_results", "project_failure",
        "project_second", "qa",
    ],
    "behavioral": [
        "self_intro", "behavior_conflict", "behavior_failure", "behavior_feedback",
        "behavior_leadership", "behavior_ethics", "qa",
    ],
    "weakness": ["self_intro", "weakness_1", "weakness_2", "weakness_3", "qa"],
}

QUICK_ITEM_IDS: dict[str, list[str]] = {
    "full": ["self_intro", "hr_motivation", "project_impact", "professional_core", "behavior_conflict", "qa"],
    "hr": ["self_intro", "hr_motivation", "hr_change", "qa"],
    "professional": ["self_intro", "professional_foundation", "professional_core", "professional_scenario", "qa"],
    "project": ["self_intro", "project_scope", "project_decision", "project_second", "qa"],
    "behavioral": ["self_intro", "behavior_conflict", "behavior_failure", "behavior_ethics", "qa"],
    "weakness": ["self_intro", "weakness_1", "weakness_2", "qa"],
}

DEEP_EXTRAS: dict[str, list[str]] = {
    "full": ["hr_self_awareness", "project_decision", "project_failure", "professional_cross", "behavior_leadership", "behavior_ethics"],
    "hr": ["hr_gap", "behavior_feedback", "behavior_ethics"],
    "professional": ["project_decision", "project_failure", "behavior_conflict", "behavior_ethics"],
    "project": ["project_impact", "professional_cross", "behavior_conflict"],
    "behavioral": ["hr_self_awareness", "professional_cross", "project_failure"],
    "weakness": ["weakness_4", "behavior_feedback"],
}


def list_modes() -> dict[str, list[dict[str, Any]]]:
    modes = []
    for key, definition in MODE_DEFINITIONS.items():
        modes.append({
            "key": key,
            "name": definition["name"],
            "description": definition["description"],
            "recommended": bool(definition.get("recommended")),
            "estimates": {
                intensity: {"min": value[0], "max": value[1]}
                for intensity, value in definition["estimates"].items()
            },
            "question_counts": {
                intensity: len(build_plan(key, intensity)["items"])
                for intensity in INTENSITY_DEFINITIONS
            },
        })
    intensities = [
        {"key": key, **value}
        for key, value in INTENSITY_DEFINITIONS.items()
    ]
    return {"modes": modes, "intensities": intensities}


def normalize_mode(value: str | None) -> str:
    return value if value in MODE_DEFINITIONS else "full"


def normalize_intensity(value: str | None) -> str:
    return value if value in INTENSITY_DEFINITIONS else "standard"


def _weakness_item(index: int, label: str, goal: str) -> dict[str, Any]:
    return _item(
        f"weakness_{index}",
        "weakness",
        label,
        f"weakness_{index}",
        goal,
        evidence_required=True,
        followup=True,
    )


def build_plan(
    mode: str,
    intensity: str,
    *,
    weakness_focus: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    mode = normalize_mode(mode)
    intensity = normalize_intensity(intensity)
    if intensity == "quick":
        item_ids = list(QUICK_ITEM_IDS[mode])
    else:
        item_ids = list(STANDARD_ITEM_IDS[mode])
        if intensity == "deep":
            item_ids[-1:-1] = DEEP_EXTRAS[mode]

    weakness_focus = weakness_focus or [
        {"label": "回答结构", "goal": "针对薄弱回答练习先结论、再事实、最后结果的完整结构。"},
        {"label": "案例证据", "goal": "针对薄弱回答补充本人行动、范围、结果和验证方式。"},
        {"label": "岗位匹配", "goal": "把已有经历与目标岗位的职责和价值建立清楚联系。"},
        {"label": "追问回应", "goal": "在追问中补充新证据并与前述回答保持一致。"},
    ]
    dynamic_items = {
        f"weakness_{index}": _weakness_item(index, focus["label"], focus["goal"])
        for index, focus in enumerate(weakness_focus[:4], start=1)
    }
    item_lookup = {**ITEMS, **dynamic_items}
    items = [deepcopy(item_lookup[item_id]) for item_id in item_ids if item_id in item_lookup]

    per_item_limit = 0 if intensity == "quick" else 1
    for item in items:
        if not item.pop("followup_eligible", False):
            item["followup_limit"] = 0
        else:
            item["followup_limit"] = per_item_limit

    estimate = MODE_DEFINITIONS[mode]["estimates"][intensity]
    return {
        "version": PLAN_VERSION,
        "mode": mode,
        "mode_label": MODE_DEFINITIONS[mode]["name"],
        "intensity": intensity,
        "intensity_label": INTENSITY_DEFINITIONS[intensity]["name"],
        "estimated_minutes": {"min": estimate[0], "max": estimate[1]},
        "followup_budget": INTENSITY_DEFINITIONS[intensity]["followup_budget"],
        "items": items,
        "state": {
            "current_index": 0,
            "asked": {},
            "covered_item_ids": [],
            "skipped_item_ids": [],
            "followups_used": 0,
            "used_question_bank_ids": [],
        },
    }


def load_plan(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        plan = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(plan, dict) or plan.get("version") != PLAN_VERSION:
        return None
    return plan


def dump_plan(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False)


def current_item(plan: dict[str, Any]) -> dict[str, Any] | None:
    items = plan.get("items") or []
    index = int((plan.get("state") or {}).get("current_index") or 0)
    return items[index] if 0 <= index < len(items) else None


def is_followup_turn(plan: dict[str, Any], item: dict[str, Any]) -> bool:
    asked = (plan.get("state") or {}).get("asked") or {}
    return int((asked.get(item["id"]) or {}).get("primary") or 0) > 0


def record_question(plan: dict[str, Any], item: dict[str, Any], *, question_bank_id: str = "") -> None:
    state = plan["state"]
    asked = state.setdefault("asked", {})
    item_state = asked.setdefault(item["id"], {"primary": 0, "followups": 0})
    if item_state["primary"] == 0:
        item_state["primary"] = 1
    else:
        item_state["followups"] += 1
        state["followups_used"] = int(state.get("followups_used") or 0) + 1
    used_question_ids = state.setdefault("used_question_bank_ids", [])
    if question_bank_id and question_bank_id not in used_question_ids:
        used_question_ids.append(question_bank_id)


def _answer_declines_precision(text: str) -> bool:
    return bool(re.search(r"(?:没有|暂无|无法|不知道|不清楚|不确定|记不清|没统计|无数据).{0,8}(?:数据|数字|记录|案例|经历|信息)?", text))


def answer_needs_followup(answer: str, item: dict[str, Any]) -> bool:
    text = (answer or "").strip()
    if not text or _answer_declines_precision(text):
        return False
    if len(text) < 45:
        return True
    if not item.get("evidence_required"):
        return False
    has_number = bool(re.search(r"\d|[一二三四五六七八九十百千万亿两]+(?:个|次|年|月|天|人|项|%|％)", text))
    has_action = bool(re.search(r"我(?:负责|主导|推动|选择|决定|协调|发现|制定|复盘|改进|验证)", text))
    has_result = bool(re.search(r"(?:最终|结果|因此|后来|落地|提升|降低|完成|挽回|避免|达到)", text))
    return sum((has_number, has_action, has_result)) < 2


def should_advance(plan: dict[str, Any], answer: str) -> bool:
    item = current_item(plan)
    if item is None:
        return True
    state = plan["state"]
    asked = state["asked"].get(item["id"], {"primary": 0, "followups": 0})
    if int(asked.get("primary") or 0) == 0:
        return False
    if not (answer or "").strip():
        return False
    if int(asked.get("followups") or 0) >= int(item.get("followup_limit") or 0):
        return True
    if int(state.get("followups_used") or 0) >= int(plan.get("followup_budget") or 0):
        return True
    return not answer_needs_followup(answer, item)


def advance(plan: dict[str, Any], *, mark_covered: bool = True) -> dict[str, Any] | None:
    state = plan["state"]
    item = current_item(plan)
    covered_ids = state.setdefault("covered_item_ids", [])
    skipped_ids = state.setdefault("skipped_item_ids", [])
    if item and mark_covered and item["id"] not in covered_ids:
        covered_ids.append(item["id"])
    if item and not mark_covered and item["id"] not in skipped_ids:
        skipped_ids.append(item["id"])
    state["current_index"] += 1
    return current_item(plan)


def progress(plan: dict[str, Any]) -> dict[str, Any]:
    items = plan.get("items") or []
    state = plan.get("state") or {}
    item = current_item(plan)
    covered_ids = set(state.get("covered_item_ids") or [])
    skipped_ids = set(state.get("skipped_item_ids") or [])
    assessable = [candidate for candidate in items if candidate["stage"] not in {"opening", "qa"}]
    covered = [candidate for candidate in assessable if candidate["id"] in covered_ids]
    return {
        "mode": plan["mode"],
        "mode_label": plan["mode_label"],
        "intensity": plan["intensity"],
        "intensity_label": plan["intensity_label"],
        "estimated_minutes": plan["estimated_minutes"],
        "current_label": item["label"] if item else "已完成",
        # 面向候选人的回答目标；不暴露 item id / intent 等内部规划字段。
        "current_goal": (item.get("goal") or "")[:120] if item else "",
        "covered": len(covered),
        "total": len(assessable),
        "covered_labels": [candidate["label"] for candidate in covered],
        "remaining_labels": [
            candidate["label"] for candidate in assessable
            if candidate["id"] not in covered_ids and candidate["id"] not in skipped_ids
        ],
        "skipped_labels": [candidate["label"] for candidate in assessable if candidate["id"] in skipped_ids],
        "followups_used": int(state.get("followups_used") or 0),
        "followup_budget": int(plan.get("followup_budget") or 0),
    }
