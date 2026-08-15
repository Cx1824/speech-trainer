"""WebSocket 路由：/ws/interview/{sid}"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.modules import interview
from app.modules.analysis import analyze_emotion, analyze_text
from app.modules.config import load_provider_config
from app.modules.interview.ws_protocol import (
    ClientMessage,
    ClientMsgType,
    ServerMsgType,
    encode_audio,
    envelope,
)
from app.providers import get_tts

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/interview/{sid}")
async def interview_ws(websocket: WebSocket, sid: str):
    await websocket.accept()
    logger.info("WebSocket connected: sid=%s", sid)

    session_factory = await get_db_session()
    async with session_factory as db:
        try:
            try:
                await interview.get_session(db, sid)
            except Exception as e:
                await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"会话不存在：{e}"))
                await websocket.close()
                return

            while True:
                raw = await websocket.receive_text()
                try:
                    msg = ClientMessage.model_validate_json(raw)
                except Exception as e:
                    await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"协议错误：{e}"))
                    continue

                if msg.type == ClientMsgType.START_STAGE:
                    await _handle_start_stage(websocket, db, sid)
                elif msg.type == ClientMsgType.USER_ANSWER:
                    await _handle_user_answer(websocket, db, sid, msg.payload)
                elif msg.type == ClientMsgType.USER_SPEECH:
                    await _handle_user_speech(websocket, db, sid, msg.payload)
                elif msg.type == ClientMsgType.FINISH_STAGE:
                    await _handle_finish_stage(websocket, db, sid)
                elif msg.type == ClientMsgType.END_INTERVIEW:
                    await _handle_end(websocket, db, sid)
                elif msg.type == ClientMsgType.REQUEST_TTS:
                    await _handle_tts(websocket, db, msg.payload.get("text", ""))

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected: sid=%s", sid)


async def _handle_start_stage(websocket: WebSocket, db: AsyncSession, sid: str) -> None:
    try:
        if await interview.should_advance(db, sid):
            session = await interview.advance_stage(db, sid)
            await websocket.send_text(
                envelope(ServerMsgType.STAGE_CHANGED, stage=session.current_stage)
            )
            if session.status == "completed":
                await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))
                return

        text = await interview.generate_next(db, sid)
        session = await interview.get_session(db, sid)
        await websocket.send_text(
            envelope(ServerMsgType.AI_QUESTION, stage=session.current_stage, text=text)
        )
    except Exception as e:
        logger.exception("生成问题失败")
        await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"生成失败：{e}"))


async def _handle_user_answer(
    websocket: WebSocket, db: AsyncSession, sid: str, payload: dict
) -> None:
    """用户提交最终回答（文字或语音 ASR 结果）。"""
    text = payload.get("text", "")
    if not text.strip():
        return

    # 实时分析
    text_res = analyze_text(text)
    from app.modules.analysis.emotion import analyze_emotion
    emotion = analyze_emotion(text_res, None)

    analysis_payload = {
        "text": text,
        "warning_level": text_res.warning_level,
        "filler_hits": text_res.filler_hits,
        "repeated_words": text_res.repeated_words,
        "repetition_rate": text_res.repetition_rate,
        "tension_score": emotion.tension_score,
        "tension_level": emotion.tension_level,
        "confidence_score": emotion.confidence_score,
        "confidence_level": emotion.confidence_level,
    }
    await websocket.send_text(
        envelope(ServerMsgType.ANALYSIS_UPDATE, **analysis_payload)
    )

    await interview.save_user_message(db, sid, text, analysis_payload)


async def _handle_user_speech(
    websocket: WebSocket, db: AsyncSession, sid: str, payload: dict
) -> None:
    """浏览器 ASR 实时识别结果（interim + final）。

    推送给前端用作弹幕显示。
    """
    text = payload.get("text", "")
    is_final = payload.get("is_final", False)
    if not text:
        return
    await websocket.send_text(
        envelope(
            ServerMsgType.SPEECH_RECOGNIZED,
            text=text,
            is_final=is_final,
        )
    )
    # 仅实时文本不落库，等 final 时由 user_answer 落库


async def _handle_finish_stage(websocket: WebSocket, db: AsyncSession, sid: str) -> None:
    session = await interview.advance_stage(db, sid)
    await websocket.send_text(envelope(ServerMsgType.STAGE_CHANGED, stage=session.current_stage))
    if session.status == "completed":
        await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))


async def _handle_end(websocket: WebSocket, db: AsyncSession, sid: str) -> None:
    from app.models.interview import InterviewSessionRow
    from sqlalchemy import select

    res = await db.execute(select(InterviewSessionRow).where(InterviewSessionRow.id == sid))
    row = res.scalar_one_or_none()
    if row:
        row.status = "completed"
        row.current_stage = "report"
        await db.commit()
    await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))


async def _handle_tts(websocket: WebSocket, db: AsyncSession, text: str) -> None:
    if not text.strip():
        return
    try:
        cfg = await load_provider_config(db, "tts")
        provider = get_tts(cfg)
        audio = await provider.synthesize(text)
        await websocket.send_text(
            envelope(ServerMsgType.TTS_AUDIO, audio=encode_audio(audio), format="mp3")
        )
    except Exception as e:
        logger.exception("TTS 失败")
        await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"TTS 失败：{e}"))


async def _handle_tts(websocket: WebSocket, db: AsyncSession, text: str) -> None:
    if not text.strip():
        return
    try:
        cfg = await load_provider_config(db, "tts")
        provider = get_tts(cfg)
        audio = await provider.synthesize(text)
        await websocket.send_text(
            envelope(ServerMsgType.TTS_AUDIO, audio=encode_audio(audio), format="mp3")
        )
    except Exception as e:
        logger.exception("TTS 失败")
        await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"TTS 失败：{e}"))
