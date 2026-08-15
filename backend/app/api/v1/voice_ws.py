"""语音面试 WebSocket：/ws/voice/{sid}

豆包/GPT 语音模式式的全双工语音通道：
- 客户端 → 服务端：
  * 二进制帧：16kHz/16bit/mono PCM 音频（前端 AudioWorklet 采集）
  * JSON {"type":"commit_answer","payload":{"text":"..."}}：前端 VAD 判定说完，提交回答
  * JSON {"type":"start_stage"} / {"type":"end_interview"} / {"type":"skip_tts"}
- 服务端 → 客户端：
  * speech_partial / speech_final：实时字幕（DashScope Paraformer 增量/定稿）
  * analysis_update：**每句定稿即推送**实时分析（口癖/重复/情绪），不等提交
  * ai_question（文字先行）+ tts_audio（分句流式合成，首句延迟低）
  * stage_changed / interview_completed / error
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.database import get_db_session
from app.models.interview import InterviewSessionRow
from app.modules import interview
from app.modules.analysis import analyze_emotion, analyze_text
from app.modules.config import load_provider_config
from app.modules.interview.ws_protocol import (
    ServerMsgType,
    encode_audio,
    envelope,
)
from app.modules.scenarios import get_pack
from app.providers import get_tts
from app.providers.asr.dashscope_realtime import RealtimeASRSession
from app.schemas import ProviderConfigIn
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_asr_config(db) -> ProviderConfigIn:
    """优先读 ASR 配置；未配置时借用 DashScope 系 TTS 的 key。"""
    cfg = await load_provider_config(db, "asr")
    if cfg.api_key:
        return cfg
    tts_cfg = await load_provider_config(db, "tts")
    if tts_cfg.api_key and tts_cfg.provider in ("qwen_audio", "aliyun", "cosyvoice", "aliyun_tts"):
        return ProviderConfigIn(
            provider="dashscope",
            base_url="paraformer-realtime-v2",
            api_key=tts_cfg.api_key,
            model="paraformer-realtime-v2",
        )
    return cfg  # 空 key，由 RealtimeASRSession 抛出友好错误


def _split_sentences(text: str) -> list[str]:
    """按中英文标点切句（保留标点），过滤空句。"""
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    return [p.strip() for p in parts if p.strip()]


def detect_filler_words(text: str) -> list[dict]:
    """轻量口癖检测（partial 阶段用，零依赖纯字符串匹配）。"""
    from app.modules.analysis.text_rules import FILLER_WORDS
    hits = []
    for word, weight in FILLER_WORDS.items():
        count = text.count(word)
        if count > 0:
            hits.append({"word": word, "count": count, "weight": weight})
    return hits


@router.websocket("/voice/{sid}")
async def voice_ws(websocket: WebSocket, sid: str):
    await websocket.accept()
    logger.info("Voice WS connected: sid=%s", sid)

    session_factory = await get_db_session()
    async with session_factory as db:
        # 校验会话
        try:
            await interview.get_session(db, sid)
        except Exception as e:
            await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"会话不存在：{e}"))
            await websocket.close()
            return

        # 本轮回答的累计状态（每句实时分析 + 累计文本）
        turn_sentences: list[str] = []
        # partial 阶段已提示过的词（ASR partial 是句内累积文本，防止同一词反复推送）
        partial_notified: set[str] = set()
        # 声学特征旁路缓冲：与 ASR 同一份 PCM，句子定稿时算真实紧张度
        from app.modules.analysis.pcm_features import PcmFeatureBuffer
        pcm_buffer = PcmFeatureBuffer()

        def _on_asr_partial(t: str) -> None:
            """增量字幕：立即推送；首次出现的口癖词即时提示一次（不累计计数）。"""
            _safe_send(websocket, envelope(
                ServerMsgType.SPEECH_RECOGNIZED, text=t, is_final=False))
            # partial 阶段：仅对"首次出现"的口癖/模糊词做一次提示（计数以 final 为准）
            try:
                from app.modules.analysis.text_rules import FILLER_WORDS, HEDGING_WORDS
                first_words = [
                    w for w in {**FILLER_WORDS, **HEDGING_WORDS}
                    if w in t and w not in partial_notified
                ]
                if first_words:
                    for w in first_words:
                        partial_notified.add(w)
                    _safe_send(websocket, envelope(ServerMsgType.ANALYSIS_UPDATE, **{
                        "partial_check": True,
                        "sentence": t,
                        "warning_level": "filler",
                        "filler_hits": [{"word": w, "count": 1, "weight": 2} for w in first_words],
                        "hedge_hits": [],
                        "uncertain_hits": [],
                        "repeated_words": [],
                        "long_sentences": [],
                        "repetition_rate": 0.0,
                    }))
            except Exception:
                pass

        def _on_asr_final(t: str) -> None:
            """句子定稿：①推送字幕 ②完整多维分析（口癖/模糊/不自信/重复/长句/情绪）。

            情绪融合真实声学信号：旁路 PCM 缓冲计算基频抖动/能量起伏/停顿 → 紧张度。
            """
            turn_sentences.append(t)
            partial_notified.clear()
            _safe_send(websocket, envelope(
                ServerMsgType.SPEECH_RECOGNIZED, text=t, is_final=True))
            try:
                text_res = analyze_text(t)
                # 声学特征（音频不足 0.5s 时 feats=None，回落纯文本判定）
                _, voice_feats = pcm_buffer.tension()
                emotion = analyze_emotion(text_res, voice_feats)
                _safe_send(websocket, envelope(ServerMsgType.ANALYSIS_UPDATE, **{
                    "sentence": t,
                    "warning_level": text_res.warning_level,
                    "filler_hits": text_res.filler_hits,
                    "hedge_hits": text_res.hedge_hits,
                    "uncertain_hits": text_res.uncertain_hits,
                    "repeated_words": text_res.repeated_words,
                    "long_sentences": text_res.long_sentences,
                    "repetition_rate": text_res.repetition_rate,
                    "tension_score": emotion.tension_score,
                    "tension_level": emotion.tension_level,
                    "confidence_score": emotion.confidence_score,
                    "confidence_level": emotion.confidence_level,
                    # 声学明细（前端可展示"为什么紧张"）
                    **({
                        "voice_signal": True,
                        "pitch_jitter": round(voice_feats.pitch_jitter, 4),
                        "pause_count": voice_feats.pause_count,
                        "speech_rate_estimate": round(
                            len(t) / voice_feats.duration_sec * 60, 1
                        ) if voice_feats.duration_sec > 0 else 0,
                    } if voice_feats else {"voice_signal": False}),
                }))
            except Exception:
                logger.exception("句子级分析失败")

        # 建 ASR 流式会话（带会话级热词表：简历技能/材料/JD 专有名词优先匹配）
        try:
            asr_cfg = await _get_asr_config(db)
            vocab_id = ""
            try:
                from app.modules.asr_hotwords import create_vocabulary, extract_hotwords
                _srow = (await db.execute(
                    select(InterviewSessionRow).where(InterviewSessionRow.id == sid)
                )).scalar_one_or_none()
                if _srow is not None:
                    _resume = json.loads(_srow.resume_parsed_json) if _srow.resume_parsed_json else None
                    hotwords = extract_hotwords(
                        resume=_resume,
                        material_text=_srow.material_text or "",
                        jd_content=_srow.jd_content or "",
                        position=_srow.position if _srow.position != "未指定" else "",
                        company=_srow.company or "",
                    )
                    if hotwords:
                        vocab_id = await create_vocabulary(asr_cfg.api_key, hotwords) or ""
            except Exception:
                logger.exception("热词准备失败（ASR 将无热词运行）")
            asr = RealtimeASRSession(asr_cfg.api_key, "paraformer-realtime-v2", vocabulary_id=vocab_id)
            asr.on_partial = _on_asr_partial
            asr.on_final = _on_asr_final
            asr.on_error = lambda m: _safe_send(
                websocket, envelope(ServerMsgType.ERROR, message=f"ASR 错误：{m}"))
            await asr.start()
        except Exception as e:
            await websocket.send_text(envelope(
                ServerMsgType.ERROR,
                message=f"语音识别启动失败：{e}。请在设置页配置 ASR（或 TTS 用阿里云 qwen_audio）的 API Key",
            ))
            await websocket.close()
            return

        # 限时场景：到点自动结束（推送 time_up，并推进阶段）
        timer_task: asyncio.Task | None = None
        try:
            from sqlalchemy import select as _select
            _res = await db.execute(_select(InterviewSessionRow).where(InterviewSessionRow.id == sid))
            _row = _res.scalar_one_or_none()
            if _row and _row.duration_limit and _row.duration_limit > 0 and get_pack(_row.scenario).timed:

                async def _on_time_up() -> None:
                    await asyncio.sleep(_row.duration_limit * 60)
                    _safe_send(websocket, envelope(ServerMsgType.TIME_UP,
                                                   limit_minutes=_row.duration_limit))

                timer_task = asyncio.create_task(_on_time_up())
        except Exception:
            logger.exception("启动计时器失败（不影响会话）")

        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.receive":
                    if "bytes" in msg and msg["bytes"]:
                        await asr.push_audio(msg["bytes"])
                        pcm_buffer.push(msg["bytes"])  # 旁路：声学紧张度用
                    elif "text" in msg and msg["text"]:
                        await _handle_json(websocket, db, sid, msg["text"], turn_sentences, pcm_buffer)
                elif msg.get("type") == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("voice ws 异常")
        finally:
            if timer_task and not timer_task.done():
                timer_task.cancel()
            await asr.close()
            logger.info("Voice WS disconnected: sid=%s", sid)


def _safe_send(websocket: WebSocket, text: str) -> None:
    """从 ASR 回调（同步上下文）异步发送消息。"""
    try:
        asyncio.create_task(websocket.send_text(text))
    except RuntimeError:
        pass


async def _handle_json(websocket: WebSocket, db, sid: str, raw: str, turn_sentences: list[str], pcm_buffer=None) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    mtype = msg.get("type", "")

    if mtype == "commit_answer":
        text = msg.get("payload", {}).get("text", "")
        await _commit_answer(websocket, db, sid, text, pcm_buffer=pcm_buffer)
        turn_sentences.clear()
    elif mtype == "start_stage":
        await _auto_next_question(websocket, db, sid)
    elif mtype == "finish_stage":
        # 限时场景：用户讲完本阶段（或计时器到点）→ 推进到下一阶段
        await _finish_stage(websocket, db, sid, turn_sentences, pcm_buffer)
    elif mtype == "end_interview":
        from sqlalchemy import select
        res = await db.execute(select(InterviewSessionRow).where(InterviewSessionRow.id == sid))
        row = res.scalar_one_or_none()
        if row:
            row.status = "completed"
            row.current_stage = "report"
            await db.commit()
        await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))
    # skip_tts 等其他消息暂不处理


async def _finish_stage(websocket: WebSocket, db, sid: str, turn_sentences: list[str], pcm_buffer=None) -> None:
    """限时场景结束当前阶段：先落库本轮已说的内容，再推进阶段。"""
    if turn_sentences:
        text = "".join(turn_sentences)
        await _commit_answer(websocket, db, sid, text, advance=False, pcm_buffer=pcm_buffer)
        turn_sentences.clear()
    session = await interview.advance_stage(db, sid)
    await websocket.send_text(envelope(ServerMsgType.STAGE_CHANGED, stage=session.current_stage))
    if session.status == "completed":
        await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))
        return
    # 推进后自动生成下一阶段的 AI 发言（如评审质询/主持人收尾）
    await _auto_next_question(websocket, db, sid)


async def _commit_answer(
    websocket: WebSocket, db, sid: str, text: str,
    advance: bool = True, pcm_buffer=None,
) -> None:
    """提交回答（分析已逐句推送过）。advance=True 时自动生成下一题。"""
    if not text.strip():
        return

    # 整轮分析（用于落库；前端展示已由句子级推送完成）
    text_res = analyze_text(text)
    voice_feats = None
    if pcm_buffer is not None:
        _, voice_feats = pcm_buffer.tension()  # 冲掉缓冲里最后一段（<0.5s 返回 None）
    emotion = analyze_emotion(text_res, voice_feats)
    analysis_payload = {
        "text": text,
        "warning_level": text_res.warning_level,
        "filler_hits": text_res.filler_hits,
        "hedge_hits": text_res.hedge_hits,
        "uncertain_hits": text_res.uncertain_hits,
        "repeated_words": text_res.repeated_words,
        "long_sentences": text_res.long_sentences,
        "repetition_rate": text_res.repetition_rate,
        "tension_score": emotion.tension_score,
        "tension_level": emotion.tension_level,
        "confidence_score": emotion.confidence_score,
        "confidence_level": emotion.confidence_level,
        "voice_signal": voice_feats is not None,
        **({
            "pitch_jitter": round(voice_feats.pitch_jitter, 4),
            "pause_count": voice_feats.pause_count,
            "avg_pause_duration": round(voice_feats.avg_pause_duration, 2),
        } if voice_feats else {}),
    }
    await interview.save_user_message(db, sid, text, analysis_payload)

    if not advance:
        return
    # 自动追问（核心：无需人工点下一题）
    await _auto_next_question(websocket, db, sid)


async def _auto_next_question(websocket: WebSocket, db, sid: str) -> None:
    try:
        if await interview.should_advance(db, sid):
            session = await interview.advance_stage(db, sid)
            await websocket.send_text(envelope(ServerMsgType.STAGE_CHANGED, stage=session.current_stage))
            if session.status == "completed":
                await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))
                return

        # 当前阶段不生成 AI 发言（如演讲 presenting 阶段）→ 只广播阶段，等用户讲完 finish_stage
        session = await interview.get_session(db, sid)
        from sqlalchemy import select
        row = (await db.execute(select(InterviewSessionRow).where(InterviewSessionRow.id == sid))).scalar_one_or_none()
        if row is not None:
            pack = get_pack(row.scenario)
            if pack.key != "interview":
                stage_def = next((s for s in pack.stages if s.key == row.current_stage), None)
                if stage_def is not None and stage_def.question_limit == 0:
                    return

        text = await interview.generate_next(db, sid)
        session = await interview.get_session(db, sid)
        # ① 文字先行（前端立即显示，消除"等语音"的感知延迟）
        await websocket.send_text(
            envelope(ServerMsgType.AI_QUESTION, stage=session.current_stage, text=text)
        )
        # ② TTS 分句流式合成：首句一到就能播，大幅降低语音延迟
        asyncio.create_task(_stream_tts(websocket, db, text))
    except Exception as e:
        logger.exception("自动追问失败")
        await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"生成失败：{e}"))


async def _stream_tts(websocket: WebSocket, db, text: str) -> None:
    """分句流式合成：每合成一句就推一句，前端逐句接续播放。"""
    try:
        cfg = await load_provider_config(db, "tts")
        provider = get_tts(cfg)
        sentences = _split_sentences(text)
        if not sentences:
            return
        for i, sent in enumerate(sentences):
            audio = await provider.synthesize(sent)
            await websocket.send_text(envelope(
                ServerMsgType.TTS_AUDIO,
                audio=encode_audio(audio), format="wav",
                seq=i, total=len(sentences), text=sent,
            ))
    except Exception as e:
        logger.warning("流式 TTS 失败（不影响文字流程）：%s", e)
