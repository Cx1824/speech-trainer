"""文本表达事实回归测试。"""

from __future__ import annotations

from app.modules.analysis.text_rules import (
    analyze_text,
    detect_consecutive_repetitions,
    detect_semantic_repetition,
    detect_semantic_repetitions,
)


def _filler_counts(text: str) -> dict[str, int]:
    return {
        hit["word"]: hit["count"]
        for hit in analyze_text(text).filler_hits
    }


def test_referential_demonstratives_are_not_fillers():
    text = "这个问题需要结合这个过程分析，这个理论不能解释这个现象。"
    assert _filler_counts(text) == {}


def test_filled_pauses_and_contextual_restarts_are_counted():
    text = "我觉得，那个，嗯，呃，这个这个问题需要重新讨论。"
    assert _filler_counts(text) == {
        "那个": 1,
        "这个": 2,
        "嗯": 1,
        "呃": 1,
    }


def test_sentence_final_modal_particle_is_not_a_filler():
    assert _filler_counts("这是好事啊。大家一起做啊。") == {}


def test_near_repetition_rate_does_not_drift_with_transcript_length():
    paragraphs = [
        "首先介绍项目背景和目标，说明用户遇到的具体困难。",
        "接着分析数据来源和判断方法，区分事实、假设与结论。",
        "随后展开解决方案，列出责任人、时间点与验收标准。",
        "最后回顾执行结果，解释主要收益、剩余风险和后续计划。",
    ]
    short = "".join(paragraphs * 3)
    long = "".join(paragraphs * 12)
    short_rate = analyze_text(short).repetition_rate
    long_rate = analyze_text(long).repetition_rate
    assert abs(short_rate - long_rate) < 0.04


def test_near_repetition_rate_detects_immediate_restarts():
    fluent = "我们今天讨论家庭教育中的责任分配，也分析不同年龄孩子的成长需要。"
    repetitive = "我们我们今天今天讨论讨论，才才才能得出结论。"
    assert analyze_text(repetitive).repetition_rate > analyze_text(fluent).repetition_rate


def test_normal_non_adjacent_reuse_is_not_a_strong_warning():
    result = analyze_text("我负责项目规划，项目规划需要先明确目标和范围。")
    assert result.repeated_words  # 客观文本事实仍保留
    assert result.consecutive_repetition_hits == []
    assert result.warning_level == "normal"
    assert result.has_warning is False


def test_consecutive_repetition_only_reports_final_like_stutter_signal():
    assert detect_consecutive_repetitions("我、我、我今天完成了项目") == [
        {"word": "我", "count": 3, "start": 0, "end": 5, "excerpt": "我、我、我"}
    ]
    assert detect_consecutive_repetitions("然后然后继续说明") == [
        {"word": "然后", "count": 2, "start": 0, "end": 4, "excerpt": "然后然后"}
    ]
    assert detect_consecutive_repetitions("看看这个方案，慢慢推进") == []
    assert detect_consecutive_repetitions("高高兴兴回家，认认真真工作，清清楚楚表达") == []


def test_consecutive_repetition_ignores_asr_inserted_sentence_punctuation():
    hits = detect_consecutive_repetitions("因为。只有在。只有在。有效的审计过程中，我们才能发现风险。")
    assert hits == [
        {"word": "只有在", "count": 2, "start": 3, "end": 10, "excerpt": "只有在。只有在"}
    ]


def test_semantic_repetition_returns_sentence_pair_but_rejects_new_evidence():
    first = "我成功推动了供应链项目落地。"
    same = "我成功推动了供应链项目落地。"
    assert detect_semantic_repetition(same, [first]) == {
        "current_sentence": same,
        "previous_sentence": first,
        "similarity": 1.0,
        "shared_phrases": ["了供应链", "供应链项", "功推动了", "动了供应"],
        "new_information_ratio": 0.0,
    }
    # 新数字和新增结果属于证据，不应被标作重复意思。
    assert detect_semantic_repetition(
        "我成功推动了供应链项目落地，并让交付周期缩短了20%。",
        [first],
    ) is None


def test_semantic_repetition_detects_duplicate_sentences_inside_one_asr_final():
    sentence = "我成功推动了供应链项目落地。"
    assert detect_semantic_repetitions(sentence + sentence, []) == [{
        "current_sentence": sentence,
        "previous_sentence": sentence,
        "similarity": 1.0,
        "shared_phrases": ["了供应链", "供应链项", "功推动了", "动了供应"],
        "new_information_ratio": 0.0,
    }]


def test_semantic_repetition_still_detects_numeric_claim_after_two_sentences():
    first = "这次项目最终为公司挽回损失1000万元。"
    history = [
        first,
        "随后我们复盘了项目执行过程。",
        "团队也补充了后续治理措施。",
    ]

    match = detect_semantic_repetition(
        "这个项目成功挽回了1000万元损失。",
        history,
    )

    assert match is not None
    assert match["previous_sentence"] == first
    assert detect_semantic_repetition(
        "项目最终造成损失1000万元。",
        history,
    ) is None
