"""结构化评分规则与关键任务约束回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules import config as config_module
from app.modules.report import generator as report_generator
from app.modules.report.generator import (
    _axis_from_llm,
    _decode_report,
    _safe_json,
    _validate_semantic_evidence,
)
from app.modules.report.scoring import apply_score_gates, compose_total
from app.modules.scenarios import get_pack
from app.modules.scenarios.base import (
    EvaluationAxis,
    EvaluationProfile,
    RubricAnchor,
    ScoreGate,
)


ANCHORS = (
    RubricAnchor(90, "任务完成充分且证据完整"),
    RubricAnchor(70, "任务基本完成但仍有明显缺口"),
    RubricAnchor(40, "关键任务基本没有完成"),
)


class TestSafeJson:
    FALLBACK_TEXT = "语义评价暂未完成"

    def test_accepts_plain_json_object(self) -> None:
        assert _safe_json('{"summary": "完成", "score": 90}') == {
            "summary": "完成",
            "score": 90,
        }

    def test_accepts_whole_markdown_fence(self) -> None:
        assert _safe_json('```json\n{"summary": "完成"}\n```') == {
            "summary": "完成"
        }

    def test_extracts_object_from_preface_and_markdown_fence(self) -> None:
        raw = '以下是评估结果：\n```json\n{"summary": "完成"}\n```\n希望有帮助。'
        assert _safe_json(raw) == {"summary": "完成"}

    def test_skips_invalid_reasoning_braces_before_valid_object(self) -> None:
        raw = 'reasoning: {这不是 JSON}，最终结果：{"summary": "完成"}'
        assert _safe_json(raw) == {"summary": "完成"}

    @pytest.mark.parametrize(
        "raw",
        [
            '[{"summary": "不应被抽取"}]',
            "42",
            '"scalar"',
            "null",
        ],
    )
    def test_rejects_top_level_array_and_scalars(self, raw: str) -> None:
        result = _safe_json(raw)
        assert self.FALLBACK_TEXT in result["summary"]

    @pytest.mark.parametrize(
        "raw",
        [
            '{"summary": "被截断"',
            "模型没有返回结构化结果",
        ],
    )
    def test_rejects_truncated_or_missing_json(self, raw: str) -> None:
        result = _safe_json(raw)
        assert self.FALLBACK_TEXT in result["summary"]

    def test_never_executes_surrounding_text(self, tmp_path) -> None:
        marker = tmp_path / "must-not-exist"
        raw = (
            f'__import__("pathlib").Path("{marker}").write_text("executed")\n'
            '{"summary": "安全解析"}'
        )

        assert _safe_json(raw) == {"summary": "安全解析"}
        assert not marker.exists()

    def test_failure_log_contains_only_safe_diagnostics(self, caplog) -> None:
        secret = "private-transcript-and-key"

        result = _safe_json(secret)

        assert self.FALLBACK_TEXT in result["summary"]
        assert secret not in caplog.text
        assert f"response_length={len(secret)}" in caplog.text
        assert "reason=no_json_object" in caplog.text


def _signal_axis() -> EvaluationAxis:
    return EvaluationAxis(
        "continuity",
        "表达连贯性",
        40,
        "signal",
        "共享表达事实",
        "continuity",
    )


def _llm_axis(**overrides) -> EvaluationAxis:
    values = {
        "key": "task_completion",
        "label": "任务完成度",
        "weight": 60,
        "source": "llm",
        "description": "是否完成本场景关键任务",
        "anchors": ANCHORS,
        "min_evidence": 2,
    }
    values.update(overrides)
    return EvaluationAxis(**values)


def _profile(*, gates: tuple[ScoreGate, ...] = ()) -> EvaluationProfile:
    return EvaluationProfile(
        version="test-v4",
        reviewer_prompt="只依据记录评价",
        axes=(_signal_axis(), _llm_axis()),
        advice_sections=("建议",),
        score_gates=gates,
    )


class TestRubricValidation:
    @pytest.mark.parametrize("score", [-1, 101, 80.5, True])
    def test_anchor_rejects_invalid_score(self, score) -> None:
        with pytest.raises(ValueError):
            RubricAnchor(score, "有效说明")

    def test_anchor_rejects_blank_description(self) -> None:
        with pytest.raises(ValueError):
            RubricAnchor(80, "   ")

    def test_signal_axis_rejects_anchors(self) -> None:
        with pytest.raises(ValueError, match="不应声明评分锚点"):
            EvaluationAxis(
                "continuity",
                "表达连贯性",
                40,
                "signal",
                "共享表达事实",
                "continuity",
                anchors=ANCHORS,
            )

    def test_llm_axis_requires_three_distinct_anchor_scores(self) -> None:
        with pytest.raises(ValueError, match="至少 3 个不同分值锚点"):
            _llm_axis(
                anchors=(
                    RubricAnchor(90, "充分"),
                    RubricAnchor(90, "同分的另一描述"),
                    RubricAnchor(60, "不足"),
                )
            )

    @pytest.mark.parametrize("min_evidence", [0, -1, 1.5, True])
    def test_axis_rejects_invalid_min_evidence(self, min_evidence) -> None:
        with pytest.raises(ValueError):
            _llm_axis(min_evidence=min_evidence)

    def test_profile_gate_must_reference_own_llm_axis(self) -> None:
        with pytest.raises(ValueError, match="不属于本策略的语义评价轴"):
            _profile(
                gates=(
                    ScoreGate("continuity", 60, 75, "信号轴不能作为关键任务轴"),
                )
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"axis_key": "", "below": 60, "max_overall": 75, "reason": "原因"},
            {"axis_key": "task", "below": -1, "max_overall": 75, "reason": "原因"},
            {"axis_key": "task", "below": float("nan"), "max_overall": 75, "reason": "原因"},
            {"axis_key": "task", "below": 60, "max_overall": 101, "reason": "原因"},
            {"axis_key": "task", "below": 60, "max_overall": float("inf"), "reason": "原因"},
            {"axis_key": "task", "below": 60, "max_overall": 75, "reason": " "},
        ],
    )
    def test_gate_rejects_invalid_fields(self, kwargs) -> None:
        with pytest.raises(ValueError):
            ScoreGate(**kwargs)


class TestEvidenceRequirement:
    def test_only_keeps_verbatim_user_quotes(self) -> None:
        result = {
            "task_completion": {
                "score": 92,
                "evidence": [
                    "“技术应该服务教师”",
                    "技术应当服务教师",
                    "技术",
                    "来自主持人的话",
                ],
            }
        }

        validated = _validate_semantic_evidence(
            result,
            ["task_completion"],
            ["我的核心观点是技术应该服务教师。"],
        )

        assert validated["task_completion"]["evidence"] == ["技术应该服务教师"]

    def test_invalid_evidence_makes_axis_score_unavailable(self) -> None:
        result = {
            "task_completion": {
                "score": 92,
                "evidence": ["模型改写出的证据"],
            }
        }
        validated = _validate_semantic_evidence(
            result,
            ["task_completion"],
            ["用户没有说过这句话。"],
        )

        assert _axis_from_llm(validated, "task_completion") is None

    def test_score_is_missing_when_evidence_is_insufficient(self) -> None:
        result = {
            "task_completion": {
                "score": 92,
                "evidence": ["只有一条有效原话", "   ", 123],
            }
        }
        assert _axis_from_llm(result, "task_completion", min_evidence=2) is None

    def test_score_is_accepted_when_evidence_requirement_is_met(self) -> None:
        result = {
            "task_completion": {
                "score": 92,
                "evidence": ["原话一", "原话二"],
            }
        }
        assert _axis_from_llm(result, "task_completion", min_evidence=2) == 92

    def test_boolean_is_not_accepted_as_numeric_score(self) -> None:
        result = {
            "task_completion": {
                "score": True,
                "evidence": ["原话一", "原话二"],
            }
        }
        assert _axis_from_llm(result, "task_completion", min_evidence=2) is None


def test_old_report_snapshot_defaults_to_no_constraints() -> None:
    row = SimpleNamespace(id="old-report", report_json='{"overall_score": 80}')
    assert _decode_report(row)["score_constraints"] == []


def test_non_finite_llm_score_is_not_accepted() -> None:
    result = {
        "task_completion": {
            "score": float("nan"),
            "evidence": ["原话一", "原话二"],
        }
    }
    assert _axis_from_llm(result, "task_completion", min_evidence=2) is None


async def test_llm_prompt_separates_timing_and_signal_facts_from_semantic_evidence(
    monkeypatch,
) -> None:
    captured_messages: list[dict] = []
    captured_options: dict = {}

    class _Provider:
        async def chat(self, messages, **options):
            captured_messages.extend(messages)
            captured_options.update(options)
            return '{"summary": "测试"}'

    async def fake_load_provider_config(db, provider_type):
        return SimpleNamespace(provider="deepseek")

    monkeypatch.setattr(
        config_module,
        "load_provider_config",
        fake_load_provider_config,
    )
    monkeypatch.setattr(report_generator, "get_llm", lambda cfg: _Provider())

    session = SimpleNamespace(position="人工智能与教育", duration_limit=5)
    dialogues = [
        SimpleNamespace(role="ai", text="请开始演讲。"),
        SimpleNamespace(role="user", text="我的核心观点是技术应该服务教师。"),
    ]
    await report_generator._llm_evaluate(
        None,
        session,
        dialogues,
        get_pack("speech"),
        "- 语速：180 字/分",
    )

    prompt = captured_messages[1]["content"]
    assert "预设时长：5 分钟" in prompt
    assert "当前没有校准时间阈值，不得计入任何语义轴评分" in prompt
    assert "请评估时间掌控" not in prompt
    assert "evidence 必须逐条引用用户在训练记录中的连续原文片段" in prompt
    assert "不得改写、用省略号拼接" in prompt
    assert "不得引用 AI 发言、主题说明或上述表达信号代替原话证据" in prompt
    assert "仅供事实说明和训练建议使用，不得替代语义轴证据" in prompt
    assert captured_options == {
        "temperature": 0.3,
        "max_tokens": report_generator.REPORT_LLM_MAX_TOKENS,
        "read_timeout": report_generator.REPORT_LLM_READ_TIMEOUT_SECONDS,
        "thinking": False,
    }


class TestScoreGates:
    GATE = ScoreGate(
        "task_completion",
        below=60,
        max_overall=75,
        reason="关键任务未完成，其他表达优势不能完全补偿",
    )

    def test_gate_triggers_and_caps_overall(self) -> None:
        final_score, constraints = apply_score_gates(
            91.0,
            {"continuity": 100, "task_completion": 59.9},
            (self.GATE,),
        )

        assert final_score == 75.0
        assert constraints == [
            {
                "axis_key": "task_completion",
                "axis_score": 59.9,
                "below": 60.0,
                "max_overall": 75.0,
                "reason": "关键任务未完成，其他表达优势不能完全补偿",
            }
        ]

    def test_gate_does_not_trigger_at_threshold(self) -> None:
        final_score, constraints = apply_score_gates(
            91.0,
            {"task_completion": 60},
            (self.GATE,),
        )
        assert final_score == 91.0
        assert constraints == []

    def test_missing_axis_does_not_manufacture_constraint(self) -> None:
        assert apply_score_gates(91.0, {}, (self.GATE,)) == (91.0, [])

    def test_no_total_does_not_apply_gate_to_partial_report(self) -> None:
        assert apply_score_gates(
            None,
            {"task_completion": 20},
            (self.GATE,),
        ) == (None, [])

    def test_non_compensatory_cap_after_weighted_composition(self) -> None:
        scores = {"continuity": 100, "task_completion": 50}
        uncapped = compose_total(scores, {"continuity": 80, "task_completion": 20})
        assert uncapped == 90.0

        final_score, _ = apply_score_gates(uncapped, scores, (self.GATE,))
        assert final_score == 75.0
