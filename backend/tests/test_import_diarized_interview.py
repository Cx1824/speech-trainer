from evals.import_diarized_interview import group_speaker_turns


def test_group_speaker_turns_preserves_question_answer_boundaries() -> None:
    sentences = [
        {"speaker_id": 1, "begin_time": 0, "end_time": 1000, "text": "问题一。"},
        {"speaker_id": 1, "begin_time": 1000, "end_time": 1800, "text": "请举例。"},
        {"speaker_id": 0, "begin_time": 2000, "end_time": 3400, "text": "回答一。"},
        {"speaker_id": 1, "begin_time": 3600, "end_time": 4200, "text": "追问。"},
        {"speaker_id": 0, "begin_time": 4500, "end_time": 5200, "text": "回答二。"},
    ]

    turns = group_speaker_turns(sentences)

    assert [(turn.speaker_id, turn.text) for turn in turns] == [
        (1, "问题一。请举例。"),
        (0, "回答一。"),
        (1, "追问。"),
        (0, "回答二。"),
    ]
    assert turns[0].begin_ms == 0
    assert turns[0].end_ms == 1800


def test_group_speaker_turns_ignores_empty_or_invalid_segments() -> None:
    sentences = [
        {"speaker_id": 0, "begin_time": 0, "end_time": 0, "text": "无效"},
        {"speaker_id": 0, "begin_time": 1, "end_time": 10, "text": "  "},
        {"speaker_id": 1, "begin_time": 10, "end_time": 20, "text": "有效"},
    ]

    turns = group_speaker_turns(sentences)

    assert len(turns) == 1
    assert turns[0].speaker_id == 1
    assert turns[0].text == "有效"
