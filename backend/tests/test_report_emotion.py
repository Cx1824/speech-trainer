"""报告表达信号聚合与场景评审配置测试。"""

import json

from app.modules.report.generator import _aggregate_emotion


class _Row:
    """模拟 InterviewDialogueRow（只含聚合用字段）。"""

    def __init__(self, role: str, analysis_json: str = "") -> None:
        self.role = role
        self.analysis_json = analysis_json


def test_aggregate_emotion_uses_session_acoustic():
    """有逐句声学记录时，报告复用会话聚合值，不从全文措辞猜测声音状态。"""
    rows = [
        _Row("ai", ""),
        _Row("user", json.dumps({"tension_score": 22.5, "confidence_score": 68.0})),
        _Row("user", json.dumps({"tension_score": 28.1, "confidence_score": 72.0})),
        _Row("user", ""),  # 空记录忽略
    ]
    e = _aggregate_emotion(rows, "一篇含可能应该大概然后的文章" * 50)
    assert e.tension_score == 25.3
    assert e.tension_level == "接近平时"
    assert e.confidence_score == 70.0


def test_aggregate_emotion_partial_fields():
    """只有 tension 没有 confidence 的记录：各自独立聚合。"""
    rows = [
        _Row("user", json.dumps({"tension_score": 30.0})),
        _Row("user", json.dumps({"tension_score": 50.0})),
    ]
    e = _aggregate_emotion(rows, "text")
    assert e.tension_score == 40.0
    assert e.confidence_score == 0.0


def test_aggregate_emotion_fallback_to_text():
    """无逐句记录的旧会话仍可读取兼容代理值。"""
    e = _aggregate_emotion([_Row("user", "")], "可能大概应该然后可能大概")
    assert e.tension_score >= 40
    assert e.confidence_score < 40


def test_aggregate_emotion_bad_json_ignored():
    """坏 JSON 记录跳过，不抛异常。"""
    rows = [
        _Row("user", "{broken json"),
        _Row("user", json.dumps({"tension_score": 10.0})),
    ]
    e = _aggregate_emotion(rows, "t")
    assert e.tension_score == 10.0
