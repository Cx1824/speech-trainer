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


@router.websocket("/voice/calibrate")
async def voice_calibrate_ws(websocket: WebSocket):
    """声音校准通道：读一段校准文本 → 采集 PCM + ASR 字数 → 个人声学基线。

    ⚠ 必须注册在 /voice/{sid} 之前，否则 "calibrate" 会被当成 sid 吞掉。

    协议：
    - 客户端 → 服务端：二进制 PCM 帧（同 /voice/{sid}）
    - JSON {"type":"finish"}：客户端读完了，触发基线计算并落库
    - 服务端 → 客户端：
      * {"type":"calibration_result","payload":{...}}（ok/message/baseline）
    """
    await websocket.accept()
    logger.info("Calibration WS connected")

    from app.modules.analysis import CALIBRATION_TEXT, PcmFeatureBuffer, build_baseline
    from app.modules.config.store import save_voice_baseline

    session_factory = await get_db_session()
    async with session_factory as db:
        # 与面试通道同款 ASR 配置
        try:
            asr_cfg = await _get_asr_config(db)
            if not asr_cfg.api_key:
                raise ValueError("未配置 ASR API Key")
        except Exception as e:
            await websocket.send_text(json.dumps({
                "type": "calibration_result",
                "payload": {"ok": False, "message": f"校准需要语音识别服务：{e}"},
            }))
            await websocket.close()
            return

        calib_sentences: list[str] = []   # ASR 定稿句子（算字数）
        pcm_buffer = PcmFeatureBuffer()
        # 校准不按 ASR 断句切特征（断句粒度不可控），改为按时间窗切段：
        # 每满 5s 的 PCM flush 一次特征，最终聚合
        feats_list = []
        pushed_samples = 0
        SEGMENT_SAMPLES = 16000 * 5  # 5s 一段

        def _on_final(t: str) -> None:
            calib_sentences.append(t)

        asr: RealtimeASRSession | None = None
        try:
            asr = RealtimeASRSession(asr_cfg.api_key, "paraformer-realtime-v2")
            asr.on_final = _on_final
            await asr.start()
        except Exception as e:
            await websocket.send_text(json.dumps({
                "type": "calibration_result",
                "payload": {"ok": False, "message": f"语音识别启动失败：{e}"},
            }))
            await websocket.close()
            return

        async def _finish() -> None:
            """计算基线并落库，推送结果。"""
            try:
                # 剩余不足 5s 的 PCM 也算一段（音频 ≥0.5s 才出特征）
                rest = pcm_buffer.flush()
                if rest.duration_sec > 0:
                    feats_list.append(rest)
                char_count = sum(len(s) for s in calib_sentences)
                bl = build_baseline(feats_list, char_count)
                if not bl.is_valid():
                    await websocket.send_text(json.dumps({
                        "type": "calibration_result",
                        "payload": {
                            "ok": False,
                            "message": f"朗读时长不足（{bl.sample_sec:.0f} 秒，需 ≥10 秒），请完整读完校准文本再试",
                        },
                    }))
                    return
                await save_voice_baseline(db, bl.to_dict())
                await websocket.send_text(json.dumps({
                    "type": "calibration_result",
                    "payload": {
                        "ok": True,
                        "message": "校准完成，后续训练将按你的个人基线评估",
                        "baseline": bl.to_dict(),
                    },
                }))
                logger.info("声学基线已保存：%s", bl.to_dict())
            except Exception:
                logger.exception("基线计算失败")
                await websocket.send_text(json.dumps({
                    "type": "calibration_result",
                    "payload": {"ok": False, "message": "基线计算失败，请重试"},
                }))

        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.receive":
                    if "bytes" in msg and msg["bytes"]:
                        await asr.push_audio(msg["bytes"])
                        pcm_buffer.push(msg["bytes"])
                        pushed_samples += len(msg["bytes"]) // 2
                        # 每满 5s 切一段特征（滑动累积，不重叠）
                        if pushed_samples >= SEGMENT_SAMPLES:
                            feats_list.append(pcm_buffer.flush())
                            pushed_samples = 0
                    elif "text" in msg and msg["text"]:
                        try:
                            m = json.loads(msg["text"])
                        except json.JSONDecodeError:
                            continue
                        if m.get("type") == "finish":
                            await _finish()
                            break
                elif msg.get("type") == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("calibration ws 异常")
        finally:
            await asr.close()
            logger.info("Calibration WS disconnected")


@router.websocket("/voice/{sid}")
async def voice_ws(websocket: WebSocket, sid: str):
    await websocket.accept()
    logger.info("Voice WS connected: sid=%s", sid)

    # 情绪 2.0：个人基线（校准朗读产物）+ 会话级平滑器
    from app.modules.analysis import EmotionSmoother, VoiceBaseline
    from app.modules.config.store import load_voice_baseline

    session_factory = await get_db_session()
    baseline_dict = None
    emotion_smoother = EmotionSmoother()

    async with session_factory as db:
        # 校验会话
        try:
            await interview.get_session(db, sid)
        except Exception as e:
            await websocket.send_text(envelope(ServerMsgType.ERROR, message=f"会话不存在：{e}"))
            await websocket.close()
            return

        # 个人声学基线（情绪 2.0）：校准过才有
        try:
            baseline_dict = await load_voice_baseline(db)
        except Exception:
            logger.exception("读取声学基线失败（按默认基线运行）")
        baseline = VoiceBaseline.from_dict(baseline_dict) if baseline_dict else None

        # 本轮回答的累计状态（每句实时分析 + 累计文本）
        turn_sentences: list[str] = []
        # 声学特征旁路缓冲：与 ASR 同一份 PCM，句子定稿时算真实紧张度
        from app.modules.analysis.pcm_features import LivePcmTracker, PcmFeatureBuffer
        pcm_buffer = PcmFeatureBuffer()
        # 实时追踪：说话中滚动刷新语速/紧张度（不等定稿）
        live_tracker = LivePcmTracker(window_sec=8.0)
        live_chars_holder = [0]   # 当前句已识别字数（partial 回调写入，节奏检测读取）
        from app.modules.analysis.live_feedback import LiveFeedbackEngine
        live_engine = LiveFeedbackEngine()
        import time as _time
        last_metrics_push = 0.0  # live_metrics 节流
        last_rhythm_check = 0.0  # 节奏检测节流

        def _on_asr_partial(t: str) -> None:
            """增量字幕 + 实时指标 + 词级即时反馈（全部不等句子定稿）。"""
            nonlocal last_metrics_push
            _safe_send(websocket, envelope(
                ServerMsgType.SPEECH_RECOGNIZED, text=t, is_final=False))
            try:
                # ① 词级即时反馈：口癖/模糊词说出口 ~1s 内提示（含冷却）
                for fb in live_engine.on_partial(t):
                    _safe_send(websocket, envelope(
                        ServerMsgType.LIVE_FEEDBACK, **fb))
                live_chars_holder[0] = len(t)  # 节奏检测用（当前句字数）
                # ② 实时指标（300ms 节流）：语速（字/发音秒）+ 实时紧张度
                now = _time.monotonic()
                if now - last_metrics_push >= 0.3:
                    last_metrics_push = now
                    speech_sec = live_tracker.speech_sec
                    rate = (len(t) / speech_sec) if speech_sec >= 1.0 else None
                    _push_live_metrics(rate)
            except Exception:
                pass

        def _push_live_metrics(rate: float | None) -> None:
            """实时语速 + 窗口紧张度推送（EMA 平滑在 analyze_emotion 内）。"""
            try:
                feats = live_tracker.snapshot(5.0)
                tension = None
                if feats.duration_sec >= 2.0:
                    from app.modules.analysis.voice_features import compute_tension_v2
                    score, _ = compute_tension_v2(feats, baseline, rate)
                    # 会话级平滑（与定稿分析共用 smoother，分数连续）
                    tension = emotion_smoother.update(score)
                _safe_send(websocket, envelope(
                    ServerMsgType.LIVE_METRICS,
                    speech_rate=round(rate * 60, 1) if rate else None,  # 字/分
                    speech_rate_level=_rate_level(rate),
                    tension_score=round(tension, 1) if tension is not None else None,
                    speech_sec=round(live_tracker.speech_sec, 1),
                ))
            except Exception:
                logger.debug("live_metrics 推送失败", exc_info=True)

        def _rate_level(rate: float | None) -> str:
            """语速相对基线的快慢标签。"""
            base = baseline.speech_rate if baseline else 4.2
            if rate is None:
                return "unknown"
            if rate > base * 1.15:
                return "fast"
            if rate < base * 0.85:
                return "slow"
            return "normal"

        def _on_asr_final(t: str) -> None:
            """句子定稿：①推送字幕 ②完整多维分析（口癖/模糊/不自信/重复/长句/情绪）。

            情绪融合真实声学信号：旁路 PCM 缓冲计算基频抖动/能量起伏/停顿 → 紧张度。
            """
            turn_sentences.append(t)
            live_engine.reset_sentence()      # 句子级实时状态复位（超长句/重复扫描）
            live_tracker.reset_speech_stats() # 语速统计归零（下一句重新计）
            live_chars_holder[0] = 0
            _safe_send(websocket, envelope(
                ServerMsgType.SPEECH_RECOGNIZED, text=t, is_final=True))
            try:
                text_res = analyze_text(t)
                # 声学特征（音频不足 0.5s 时 feats=None，回落纯文本判定）
                _, voice_feats = pcm_buffer.tension()
                # 情绪 2.0：语速信号（ASR 字数 ÷ 本句音频时长）
                speech_rate = None
                if voice_feats and voice_feats.duration_sec > 0:
                    speech_rate = len(t) / voice_feats.duration_sec
                emotion = analyze_emotion(
                    text_res, voice_feats,
                    baseline=baseline, speech_rate=speech_rate,
                    smoother=emotion_smoother,
                )
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
                    "calibrated": emotion.calibrated,
                    "factors": emotion.factors,
                    # 声学明细（前端可展示"为什么紧张"）
                    **({
                        "voice_signal": True,
                        "pitch_jitter": round(voice_feats.pitch_jitter, 4),
                        "pause_count": voice_feats.pause_count,
                        "speech_rate_estimate": round(speech_rate * 60, 1) if speech_rate else 0,
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

        # 限时场景：到点自动结束。
        # 计时零点=开场白播完（前端发 begin_timer），而非连接时刻——
        # 避免 LLM 生成 + TTS 播报的十几秒被计入限时，用户还没开口倒计时已跑掉。
        # finish_stage / commit_answer 会兜底补启动（开场白没播就跳阶段的极端情况）。
        timer_task: asyncio.Task | None = None
        timer_started = False
        timer_limit_min = 0

        def _start_timer() -> bool:
            """惰性启动倒计时 task（幂等：已启动则跳过）。返回是否为限时场景。"""
            nonlocal timer_task, timer_started
            if timer_limit_min <= 0:
                return False
            if timer_started:
                return True
            timer_started = True

            async def _on_time_up() -> None:
                await asyncio.sleep(timer_limit_min * 60)
                _safe_send(websocket, envelope(ServerMsgType.TIME_UP,
                                               limit_minutes=timer_limit_min))

            timer_task = asyncio.create_task(_on_time_up())
            return True

        try:
            from sqlalchemy import select as _select
            _res = await db.execute(_select(InterviewSessionRow).where(InterviewSessionRow.id == sid))
            _row = _res.scalar_one_or_none()
            if _row and _row.duration_limit and _row.duration_limit > 0 and get_pack(_row.scenario).timed:
                timer_limit_min = _row.duration_limit
        except Exception:
            logger.exception("读取限时配置失败（不影响会话）")

        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.receive":
                    if "bytes" in msg and msg["bytes"]:
                        await asr.push_audio(msg["bytes"])
                        pcm_buffer.push(msg["bytes"])   # 旁路：句子定稿声学特征
                        live_tracker.push(msg["bytes"]) # 实时：说话中滚动指标
                        # 节奏检测（250ms 节流）：快说/换气/冷场
                        now = _time.monotonic()
                        if now - last_rhythm_check >= 0.25:
                            last_rhythm_check = now
                            base_rate = baseline.speech_rate if baseline else 4.2
                            speech_sec = live_tracker.speech_sec
                            cur_rate = (live_chars_holder[0] / speech_sec) if speech_sec >= 1.0 else None
                            for fb in live_engine.on_rhythm(
                                speech_run_sec=live_tracker.current_speech_run_sec(),
                                silence_sec=live_tracker.current_silence_sec(),
                                speech_rate=cur_rate,
                                base_rate=base_rate,
                                speaking=True,
                            ):
                                _safe_send(websocket, envelope(ServerMsgType.LIVE_FEEDBACK, **fb))
                    elif "text" in msg and msg["text"]:
                        await _handle_json(
                            websocket, db, sid, msg["text"], turn_sentences, pcm_buffer,
                            baseline=baseline, smoother=emotion_smoother,
                            start_timer=_start_timer,
                        )
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


async def _handle_json(websocket: WebSocket, db, sid: str, raw: str, turn_sentences: list[str], pcm_buffer=None, baseline=None, smoother=None, start_timer=None) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    mtype = msg.get("type", "")

    if mtype == "commit_answer":
        text = msg.get("payload", {}).get("text", "")
        # 兜底：限时场景用户先开口（开场白还在播/没播完就跳阶段），从此刻起计时
        if start_timer:
            start_timer()
        await _commit_answer(websocket, db, sid, text, pcm_buffer=pcm_buffer, baseline=baseline, smoother=smoother)
        turn_sentences.clear()
    elif mtype == "start_stage":
        await _auto_next_question(websocket, db, sid)
    elif mtype == "begin_timer":
        # 前端开场白播完：计时零点确认，从此刻起倒计时（非限时场景不回执）
        if start_timer and start_timer():
            await websocket.send_text(envelope(ServerMsgType.TIMER_STARTED))
    elif mtype == "finish_stage":
        # 限时场景：用户讲完本阶段（或计时器到点）→ 推进到下一阶段
        if start_timer:
            start_timer()  # 兜底：开场白没播完就点了「讲完了」
        await _finish_stage(websocket, db, sid, turn_sentences, pcm_buffer, baseline=baseline, smoother=smoother)
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


async def _finish_stage(websocket: WebSocket, db, sid: str, turn_sentences: list[str], pcm_buffer=None, baseline=None, smoother=None) -> None:
    """限时场景结束当前阶段：先落库本轮已说的内容，再推进阶段。"""
    if turn_sentences:
        text = "".join(turn_sentences)
        await _commit_answer(websocket, db, sid, text, advance=False, pcm_buffer=pcm_buffer, baseline=baseline, smoother=smoother)
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
    advance: bool = True, pcm_buffer=None, baseline=None, smoother=None,
) -> None:
    """提交回答（分析已逐句推送过）。advance=True 时自动生成下一题。"""
    if not text.strip():
        return

    # 整轮分析（用于落库；前端展示已由句子级推送完成）
    text_res = analyze_text(text)
    voice_feats = None
    if pcm_buffer is not None:
        _, voice_feats = pcm_buffer.tension()  # 冲掉缓冲里最后一段（<0.5s 返回 None）
    # 情绪 2.0：整轮语速 + 基线 + 平滑
    speech_rate = None
    if voice_feats and voice_feats.duration_sec > 0:
        speech_rate = len(text) / voice_feats.duration_sec
    emotion = analyze_emotion(
        text_res, voice_feats,
        baseline=baseline, speech_rate=speech_rate, smoother=smoother,
    )
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
        "calibrated": emotion.calibrated,
        "factors": emotion.factors,
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
    """分句合成推送：首句先行播放，其余并发合成、按原句顺序发送。

    串行合成时句间有"合成等待"空隙（AI 发言听起来一顿一顿）；
    并发起跑 + 按序发送让音频帧几乎无缝接续，总耗时≈首句合成时间。
    """
    try:
        cfg = await load_provider_config(db, "tts")
        provider = get_tts(cfg)
        fmt = "mp3" if getattr(provider, "name", "") == "edge" else "wav"
        sentences = _split_sentences(text)
        if not sentences:
            return
        # 首句：立即合成立即发（最低首响延迟）
        first_audio = await provider.synthesize(sentences[0])
        await websocket.send_text(envelope(
            ServerMsgType.TTS_AUDIO,
            audio=encode_audio(first_audio), format=fmt,
            seq=0, total=len(sentences), text=sentences[0],
        ))
        if len(sentences) == 1:
            return
        # 其余句：并发合成（任务同时起跑），按原句顺序发送
        rest_tasks = [
            asyncio.create_task(provider.synthesize(s))
            for s in sentences[1:]
        ]
        for i, task in enumerate(rest_tasks, start=1):
            try:
                audio = await task
                await websocket.send_text(envelope(
                    ServerMsgType.TTS_AUDIO,
                    audio=encode_audio(audio), format=fmt,
                    seq=i, total=len(sentences), text=sentences[i],
                ))
            except Exception:
                logger.warning("TTS 第 %d 句合成失败（跳过该句）", i, exc_info=True)
    except Exception as e:
        logger.warning("流式 TTS 失败（不影响文字流程）：%s", e)

