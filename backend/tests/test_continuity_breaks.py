"""局部表达断裂与节奏评分的回归契约。

这些用例描述的是听众可感知的结果，而不是某一种 ASR 或实现方式：

* “原来审计。原来采购。”在语义上是一次局部重启，即使说话人随后纠正，
  也已经造成了短暂的理解断裂；
* “不了解审计，也不了解采购业务”是正常并列，不应被误报；
* 纠正后恢复比未恢复、连续多次重启的影响轻；
* 同一断裂事件的重复词和停顿证据不能重复扣分；
* 平均语速适中不代表整体节奏没有问题。

``break_events`` 是评分层的最小结构化契约。事件至少应有稳定的
``event_id``、``kind`` 和 ``recovered`` 字段；具体检测器可以继续提供
更多展示字段（例如原词、替换词、起止时间和置信度）。
"""

from __future__ import annotations

from app.modules.analysis.text_rules import analyze_text
from app.modules.report.scoring import score_continuity, score_pacing


def _base_continuity_score() -> float:
    """没有局部断裂时的对照分，避免把口癖/重复信号混入本组断言。"""
    score = score_continuity(
        filler_weighted=0,
        total_chars=100,
        repetition_rate=0.0,
        break_events=[],
    )
    assert score is not None
    return score


def test_semantic_substitution_followed_by_correction_is_a_local_break() -> None:
    """“审计→采购”虽已纠正，听者仍经历了一次句子断裂。"""
    text = "只有你了解到了，原来审计。原来采购。采购会有很多特殊情况。"
    result = analyze_text(text)

    breaks = result.expression_breaks
    assert breaks, "应识别出‘原来审计。原来采购。’这一局部表达断裂"
    assert any(
        "审计" in str(event) and "采购" in str(event)
        for event in breaks
    )

    broken_score = score_continuity(
        filler_weighted=0,
        total_chars=len(text),
        repetition_rate=0.0,
        break_events=breaks,
    )
    assert broken_score is not None
    assert broken_score < _base_continuity_score()


def test_parallel_contrast_is_not_a_local_break() -> None:
    """两个并列宾语都由“不了解”支配，不是说到一半改口。"""
    result = analyze_text("如果不了解审计，也不了解采购业务，就很难判断问题。")

    assert result.expression_breaks == []
    assert score_continuity(
        filler_weighted=0,
        total_chars=100,
        repetition_rate=0.0,
        break_events=result.expression_breaks,
    ) == _base_continuity_score()


def test_recovered_correction_is_lighter_than_unrecovered_restarts() -> None:
    """恢复后的口误仍扣分，但应轻于未恢复或连续重启。"""
    recovered = [
        {
            "event_id": "repair-1",
            "kind": "self_correction",
            "recovered": True,
        }
    ]
    unrecovered = [
        {
            "event_id": "restart-1",
            "kind": "restart",
            "recovered": False,
        },
        {
            "event_id": "restart-2",
            "kind": "restart",
            "recovered": False,
        },
    ]

    recovered_score = score_continuity(0, 100, 0.0, break_events=recovered)
    unrecovered_score = score_continuity(0, 100, 0.0, break_events=unrecovered)
    assert recovered_score is not None
    assert unrecovered_score is not None
    assert recovered_score < _base_continuity_score()
    assert recovered_score > unrecovered_score


def test_one_break_is_not_penalized_again_for_repeat_and_pause_evidence() -> None:
    """同一断裂的文本、重复和停顿证据只能贡献一次影响。"""
    one_event = [
        {
            "event_id": "break-1",
            "kind": "self_correction",
            "recovered": True,
        }
    ]
    same_event_with_supporting_evidence = [
        *one_event,
        {
            "event_id": "break-1",
            "kind": "consecutive_repeat",
            "recovered": True,
        },
        {
            "event_id": "break-1",
            "kind": "pause",
            "recovered": True,
        },
    ]

    one_score = score_continuity(0, 100, 0.0, break_events=one_event)
    deduplicated_score = score_continuity(
        0,
        100,
        0.0,
        break_events=same_event_with_supporting_evidence,
    )
    assert one_score is not None
    assert deduplicated_score == one_score


def test_appropriate_average_rate_does_not_hide_rhythm_disruption() -> None:
    """平均 180 字/分钟仍可能因停顿过密而节奏不稳。"""
    assert score_pacing(rate_cpm=180) == 100

    confirmed_break = [{
        "event_id": "break-1",
        "kind": "fragmented_clause",
        "recovered": True,
    }]
    smooth = score_pacing(
        rate_cpm=180,
        pause_rate=3.0,
        break_events=confirmed_break,
    )
    disrupted = score_pacing(
        rate_cpm=180,
        pause_rate=7.0,
        break_events=confirmed_break,
    )
    assert smooth is not None
    assert disrupted is not None
    assert disrupted < smooth
