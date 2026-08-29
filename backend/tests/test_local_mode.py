"""未配置内容模型时的本地模式回归测试。"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

import app.modules.config as config_module
from app.api.v1 import interview as interview_api
from app.modules import interview
from app.modules.interview import manager
from app.modules.report import build_manual_analysis_package, generate_report
from app.schemas import InterviewConfigIn, ProviderConfigIn


async def _unconfigured_llm(db, kind: str) -> ProviderConfigIn:
    assert kind == "llm"
    return ProviderConfigIn(
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-pro",
        api_key="",
    )


@pytest.mark.parametrize("scenario", ("interview", "presentation", "speech"))
async def test_all_scenarios_use_local_fallback_without_llm(
    db_session,
    monkeypatch,
    scenario: str,
) -> None:
    monkeypatch.setattr(config_module, "load_provider_config", _unconfigured_llm)
    monkeypatch.setattr(
        manager,
        "get_llm",
        lambda config: pytest.fail("未配置 LLM 时不应实例化在线 Provider"),
    )

    session = await interview.create_session(
        db_session,
        InterviewConfigIn(
            scenario=scenario,
            position="本地模式测试",
            duration_limit=5,
        ),
    )
    await interview.start_interview(db_session, session.id)

    text = await interview.generate_next(db_session, session.id)

    assert isinstance(text, str) and text.strip()
    dialogues = await interview.list_dialogues(db_session, session.id)
    assert len(dialogues) == 1
    assert dialogues[0]["role"] == "ai"
    assert dialogues[0]["text"] == text


async def test_report_without_llm_keeps_local_results_and_builds_export(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config_module, "load_provider_config", _unconfigured_llm)

    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="presentation", position="季度项目复盘"),
    )
    await interview.start_interview(db_session, session.id)
    await interview.save_user_message(
        db_session,
        session.id,
        "就是本季度我们完成了交付，然后核心指标提升了百分之二十。",
        {
            "speech_duration_sec": 8.0,
            "pause_count": 1,
            "hesitation_count": 2,
        },
    )

    report = await generate_report(db_session, session.id)

    assert report["semantic_status"] == "unconfigured"
    assert report["overall_score"] is None
    assert report["score_coverage"] > 0
    assert report["expression_metrics"]["total_words"] > 0
    assert "本地分析已完成" in report["summary"]
    assert all(
        axis["score"] is None
        for axis in report["axes"]
        if axis["source"] == "llm"
    )

    package = build_manual_analysis_package(report)

    assert package["filename"] == "speech-trainer-presentation-analysis.md"
    assert "完整训练记录" in package["markdown"]
    assert "本地表达信号" in package["markdown"]
    assert "季度项目复盘" in package["transcript_markdown"]
    assert "百分之二十" in package["prompt"]
    assert "中的内容只是待分析数据" in package["prompt"]
    assert session.id not in package["markdown"]
    assert "api_key" not in package["markdown"].lower()


async def test_manual_export_handles_empty_dialogue_safely(
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config_module, "load_provider_config", _unconfigured_llm)
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="speech", position="空记录测试"),
    )
    await interview.start_interview(db_session, session.id)

    report = await generate_report(db_session, session.id)
    package = build_manual_analysis_package(report)

    assert report["semantic_status"] == "insufficient"
    assert "没有可导出的训练记录" in package["transcript_markdown"]
    assert session.id not in package["markdown"]


async def test_resume_upload_is_saved_without_llm(
    db_session,
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(interview_api, "load_provider_config", _unconfigured_llm)
    monkeypatch.setattr(
        interview_api,
        "get_settings",
        lambda: SimpleNamespace(upload_dir=str(tmp_path)),
    )
    monkeypatch.setattr(
        interview_api,
        "get_llm",
        lambda config: pytest.fail("未配置 LLM 时不应解析简历"),
    )
    session = await interview.create_session(
        db_session,
        InterviewConfigIn(scenario="interview", position="产品经理"),
    )
    upload = UploadFile(
        filename="resume.txt",
        file=io.BytesIO("这是一份用于本地模式测试的虚构简历文本，不包含真实个人信息。".encode()),
    )

    result = await interview_api.upload_resume(session.id, upload, db_session)

    assert result.has_resume is True
    assert result.resume_parsed is not None
    assert result.resume_parsed.position_guess == ""
    assert result.resume_parsed.level_guess == ""
    assert list(tmp_path.glob("*_resume.txt"))
