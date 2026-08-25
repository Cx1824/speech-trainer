"""语音面试 WebSocket：/ws/voice/{sid}

豆包/GPT 语音模式式的全双工语音通道：
- 客户端 → 服务端：
  * 二进制帧：16kHz/16bit/mono PCM 音频（前端 AudioWorklet 采集）
  * JSON {"type":"finish_audio"}：固定录音发送结束，冲刷 ASR 最后一句
  * JSON {"type":"commit_answer","payload":{"text":"..."}}：前端 VAD 判定说完，提交回答
  * JSON {"type":"start_stage"} / {"type":"end_interview"} / {"type":"skip_tts"}
- 服务端 → 客户端：
  * speech_partial / speech_final：实时字幕（当前所选 ASR 的增量/定稿）
  * analysis_update：**每句定稿即推送**实时分析（口癖/重复/声音事实），不等提交
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
from app.core.local_access import accept_trusted_websocket
from app.models.interview import InterviewSessionRow
from app.modules import interview
from app.modules.analysis import (
    aggregate_sentence_analyses,
    analyze_emotion,
    analyze_text,
    detect_semantic_repetitions,
)
from app.modules.config import load_provider_config
from app.modules.interview.ws_protocol import (
    ServerMsgType,
    encode_audio,
    envelope,
)
from app.modules.scenarios import get_pack
from app.providers import get_tts
from app.providers.asr.realtime import (
    RealtimeSession,
    create_realtime_session,
    is_local_asr,
)
from app.schemas import ProviderConfigIn
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter()

# REST /end 与语音 WebSocket 会在浏览器结束训练时几乎同时到达。
# 活动连接用事件告知 REST：内存中的最后一句已经持久化，可以安全完成会话。
_voice_flush_events: dict[str, asyncio.Event] = {}
OVERTIME_GRACE_SECONDS = 10 * 60


def _register_voice_flush(sid: str) -> asyncio.Event:
    event = asyncio.Event()
    _voice_flush_events[sid] = event
    return event


def _mark_voice_flushed(sid: str, event: asyncio.Event) -> None:
    event.set()

    def _expire() -> None:
        if _voice_flush_events.get(sid) is event:
            _voice_flush_events.pop(sid, None)

    # REST 通常已在等待；保留一个短窗口也覆盖稍晚到达的兜底请求。
    asyncio.get_running_loop().call_later(30, _expire)


async def wait_for_voice_flush(sid: str, timeout: float = 2.0) -> bool:
    """等待活动语音连接冲刷最后一句；没有活动连接时立即返回。"""
    event = _voice_flush_events.get(sid)
    if event is None:
        return True
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except TimeoutError:
        logger.warning("等待语音字幕落库超时，REST 继续结束会话：sid=%s", sid)
        return False


async def _send_timed_deadlines(
    websocket: WebSocket,
    limit_minutes: int,
    *,
    sleep=asyncio.sleep,
) -> None:
    """到点先提醒，宽限期结束后再通知前端强制收尾。"""
    await sleep(limit_minutes * 60)
    await websocket.send_text(envelope(
        ServerMsgType.TIME_UP,
        limit_minutes=limit_minutes,
        grace_minutes=OVERTIME_GRACE_SECONDS // 60,
    ))
    await sleep(OVERTIME_GRACE_SECONDS)
    await websocket.send_text(envelope(
        ServerMsgType.HARD_TIME_UP,
        limit_minutes=limit_minutes,
        overtime_seconds=OVERTIME_GRACE_SECONDS,
    ))


async def _get_asr_config(db) -> ProviderConfigIn:
    """优先读 ASR 配置；未配置时借用 DashScope 系 TTS 的 key。"""
    cfg = await load_provider_config(db, "asr")
    if is_local_asr(cfg.provider) or cfg.api_key:
        return cfg
    tts_cfg = await load_provider_config(db, "tts")
    if tts_cfg.api_key and tts_cfg.provider in ("qwen_audio", "aliyun", "cosyvoice", "aliyun_tts"):
        return ProviderConfigIn(
            provider="dashscope",
            base_url="paraformer-realtime-v2",
            api_key=tts_cfg.api_key,
            model="paraformer-realtime-v2",
        )
    return cfg


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


def _rolling_turn_rate(
    completed_chars: int,
    completed_speech_sec: float,
    partial_chars: int,
    current_speech_sec: float,
    *,
    min_speech_sec: float = 3.0,
) -> float | None:
    """Return a stable chars/second estimate for the current answer.

    ASR partials are rewritten and finalised at uneven intervals. Dividing one
    short partial by roughly one second of audio produces meaningless spikes,
    so live display uses all finalised and current speech in the answer and
    waits for a minimally useful sample.
    """
    speech_sec = max(0.0, completed_speech_sec) + max(0.0, current_speech_sec)
    chars = max(0, completed_chars) + max(0, partial_chars)
    if speech_sec < min_speech_sec or chars <= 0:
        return None
    return chars / speech_sec


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
    if not await accept_trusted_websocket(websocket):
        return
    logger.info("Calibration WS connected")

    from app.modules.analysis import CALIBRATION_TEXT, PcmFeatureBuffer, build_baseline
    from app.modules.config.store import save_voice_baseline

    session_factory = await get_db_session()
    async with session_factory as db:
        # 与面试通道同款 ASR 配置
        try:
            asr_cfg = await _get_asr_config(db)
            if not is_local_asr(asr_cfg.provider) and not asr_cfg.api_key:
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

        asr: RealtimeSession | None = None
        try:
            asr = create_realtime_session(asr_cfg)
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
                            await asr.finish()
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
    if not await accept_trusted_websocket(websocket):
        return
    logger.info("Voice WS connected: sid=%s", sid)

    # 旧协议兼容：个人基线 + 历史代理值平滑器。
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

        voice_flush_event = _register_voice_flush(sid)

        # 个人声学基线：校准过才有
        try:
            baseline_dict = await load_voice_baseline(db)
        except Exception:
            logger.exception("读取声学基线失败（按默认基线运行）")
        baseline = VoiceBaseline.from_dict(baseline_dict) if baseline_dict else None

        # 本轮回答的累计状态（每句实时分析 + 累计文本）
        turn_sentences: list[str] = []
        turn_analyses: list[dict] = []
        # 只保存当前回答已定稿的句子；commit/end 后清空，避免把上轮回答
        # 的句子误当成当前回合的“重复意思”证据。
        semantic_history: list[str] = []
        latest_partial_holder = [""]
        last_committed_holder = [""]
        # 声学特征旁路缓冲：与 ASR 同一份 PCM，句子定稿时提取声音事实
        from app.modules.analysis.pcm_features import LivePcmTracker, PcmFeatureBuffer
        pcm_buffer = PcmFeatureBuffer()
        # 实时追踪：说话中滚动刷新语速（不等定稿）
        live_tracker = LivePcmTracker(window_sec=8.0)
        live_chars_holder = [0]   # 当前句已识别字数（partial 回调写入，节奏检测读取）
        turn_rate_chars_holder = [0]
        turn_rate_speech_sec_holder = [0.0]
        from app.modules.analysis.live_feedback import LiveFeedbackEngine
        # 连读/口吃强检测只看 final，避免 ASR partial 重写误报；口头禅
        # 仍由 final analysis_update 做准确的被动累计。
        live_engine = LiveFeedbackEngine(enable_partial_repeat=False)
        import time as _time
        last_metrics_push = 0.0  # live_metrics 节流
        last_rhythm_check = 0.0  # 节奏检测节流

        def _on_asr_partial(t: str) -> None:
            """增量字幕 + 实时指标 + 词级即时反馈（全部不等句子定稿）。"""
            nonlocal last_metrics_push
            latest_partial_holder[0] = t
            _safe_send(websocket, envelope(
                ServerMsgType.SPEECH_RECOGNIZED, text=t, is_final=False))
            try:
                # ① 词级即时反馈：口癖/模糊词说出口 ~1s 内提示（含冷却）
                for fb in live_engine.on_partial(t):
                    _safe_send(websocket, envelope(
                        ServerMsgType.LIVE_FEEDBACK, **fb))
                live_chars_holder[0] = len(t)  # 节奏检测用（当前句字数）
                # ② 实时指标（300ms 节流）：语速（字/发音秒）。声音波动、
                # 卡顿与节奏不再合并为实时“紧张度”。
                now = _time.monotonic()
                if now - last_metrics_push >= 0.3:
                    last_metrics_push = now
                    rate = _rolling_turn_rate(
                        turn_rate_chars_holder[0],
                        turn_rate_speech_sec_holder[0],
                        len(t),
                        live_tracker.speech_sec,
                    )
                    _push_live_metrics(rate)
            except Exception:
                pass

        def _push_live_metrics(rate: float | None) -> None:
            """推送实时语速；旧 tension 字段保留为空以兼容协议。"""
            try:
                _safe_send(websocket, envelope(
                    ServerMsgType.LIVE_METRICS,
                    speech_rate=round(rate * 60, 1) if rate else None,  # 字/分
                    speech_rate_level=_rate_level(rate),
                    tension_score=None,
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
            """句子定稿：推送字幕与多维表达分析。

            旁路 PCM 缓冲计算基频波动、能量起伏和停顿等可观察事实。
            """
            if not t.strip():
                return
            semantic_matches = detect_semantic_repetitions(t, semantic_history)
            semantic_match = semantic_matches[-1] if semantic_matches else None
            semantic_history.append(t)
            turn_sentences.append(t)
            latest_partial_holder[0] = ""
            # 当前回答的实时语速跨 ASR 断句累计，避免短句定稿后从零计算造成尖峰。
            turn_rate_chars_holder[0] += len(t)
            turn_rate_speech_sec_holder[0] += live_tracker.speech_sec
            live_engine.reset_sentence()      # 句子级实时状态复位（超长句/重复扫描）
            live_tracker.reset_speech_stats() # 当前句统计归零，回答级累计保留
            live_chars_holder[0] = 0
            _safe_send(websocket, envelope(
                ServerMsgType.SPEECH_RECOGNIZED, text=t, is_final=True))
            try:
                text_res = analyze_text(t)
                # 声学特征（音频不足 0.5s 时 feats=None，回落纯文本判定）
                _, voice_feats = pcm_buffer.tension()
                # 兼容分析所需语速（ASR 字数 ÷ 本句音频时长）
                speech_rate = None
                if voice_feats and voice_feats.duration_sec > 0:
                    speech_rate = len(t) / voice_feats.duration_sec
                emotion = analyze_emotion(
                    text_res, voice_feats,
                    baseline=baseline, speech_rate=speech_rate,
                    smoother=emotion_smoother,
                )
                sentence_analysis = {
                    "sentence": t,
                    "warning_level": text_res.warning_level,
                    "filler_hits": text_res.filler_hits,
                    "hedge_hits": text_res.hedge_hits,
                    "uncertain_hits": text_res.uncertain_hits,
                    "repeated_words": text_res.repeated_words,
                    "consecutive_repetition_hits": text_res.consecutive_repetition_hits,
                    "semantic_repetition": semantic_match,
                    "semantic_repetitions": semantic_matches,
                    "long_sentences": text_res.long_sentences,
                    "repetition_rate": text_res.repetition_rate,
                    "tension_score": emotion.tension_score,
                    "tension_level": emotion.tension_level,
                    "confidence_score": emotion.confidence_score,
                    "confidence_level": emotion.confidence_level,
                    "calibrated": emotion.calibrated,
                    "factors": emotion.factors,
                    # 可观察声学事实；卡顿、音高波动和心理感受互不等价。
                    **({
                        "voice_signal": True,
                        "pitch_jitter": round(voice_feats.pitch_jitter, 4),
                        "pause_count": voice_feats.pause_count,
                        "hesitation_count": voice_feats.hesitation_count,
                        "avg_pause_duration": round(voice_feats.avg_pause_duration, 2),
                        "speech_duration_sec": round(voice_feats.duration_sec, 2),
                        "speech_rate_estimate": round(speech_rate * 60, 1) if speech_rate else 0,
                    } if voice_feats else {"voice_signal": False}),
                }
                turn_analyses.append(sentence_analysis)
                _safe_send(
                    websocket,
                    envelope(ServerMsgType.ANALYSIS_UPDATE, **sentence_analysis),
                )
            except Exception:
                logger.exception("句子级分析失败")

        # 建 ASR 流式会话（带会话级热词表：简历技能/材料/JD 专有名词优先匹配）
        try:
            asr_cfg = await _get_asr_config(db)
            vocab_id = ""
            hotwords: list[str] = []
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
                    if hotwords and not is_local_asr(asr_cfg.provider):
                        vocab_id = await create_vocabulary(asr_cfg.api_key, hotwords) or ""
            except Exception:
                logger.exception("热词准备失败（ASR 将无热词运行）")
            asr = create_realtime_session(
                asr_cfg,
                hotwords=hotwords,
                vocabulary_id=vocab_id,
            )
            asr.on_partial = _on_asr_partial
            asr.on_final = _on_asr_final
            asr.on_error = lambda m: _safe_send(
                websocket, envelope(ServerMsgType.ERROR, message=f"ASR 错误：{m}"))
            await asr.start()
        except Exception as e:
            _mark_voice_flushed(sid, voice_flush_event)
            await websocket.send_text(envelope(
                ServerMsgType.ERROR,
                message=f"语音识别启动失败：{e}。请在设置页检查语音识别服务",
            ))
            await websocket.close()
            return

        # 限时场景：到点只提醒并继续收音；超时 10 分钟才强制结束。
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

            timer_task = asyncio.create_task(
                _send_timed_deadlines(websocket, timer_limit_min),
            )
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
                            cur_rate = _rolling_turn_rate(
                                turn_rate_chars_holder[0],
                                turn_rate_speech_sec_holder[0],
                                live_chars_holder[0],
                                live_tracker.speech_sec,
                            )
                            for fb in live_engine.on_rhythm(
                                speech_run_sec=live_tracker.current_speech_run_sec(),
                                silence_sec=live_tracker.current_silence_sec(),
                                speech_rate=cur_rate,
                                base_rate=base_rate,
                                speaking=True,
                            ):
                                _safe_send(websocket, envelope(ServerMsgType.LIVE_FEEDBACK, **fb))
                    elif "text" in msg and msg["text"]:
                        try:
                            client_message = json.loads(msg["text"])
                        except json.JSONDecodeError:
                            continue
                        if client_message.get("type") == "finish_audio":
                            await asr.finish()
                            await websocket.send_text(envelope(ServerMsgType.AUDIO_FINISHED))
                        else:
                            if client_message.get("type") in {
                                "commit_answer", "finish_stage", "end_interview",
                            }:
                                await asr.flush_utterance()
                            await _handle_json(
                                websocket, db, sid, msg["text"], turn_sentences,
                                turn_analyses, pcm_buffer,
                                semantic_history=semantic_history,
                                baseline=baseline, smoother=emotion_smoother,
                                start_timer=_start_timer,
                                latest_partial_holder=latest_partial_holder,
                                last_committed_holder=last_committed_holder,
                                voice_flush_event=voice_flush_event,
                            )
                            if client_message.get("type") in {
                                "commit_answer", "finish_stage", "end_interview",
                            }:
                                turn_rate_chars_holder[0] = 0
                                turn_rate_speech_sec_holder[0] = 0.0
                elif msg.get("type") == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("voice ws 异常")
        finally:
            if timer_task and not timer_task.done():
                timer_task.cancel()
            try:
                await _flush_pending_answer(
                    websocket,
                    db,
                    sid,
                    turn_sentences,
                    turn_analyses,
                    latest_partial_holder,
                    last_committed_holder,
                    semantic_history=semantic_history,
                    pcm_buffer=pcm_buffer,
                    baseline=baseline,
                    smoother=emotion_smoother,
                )
            except Exception:
                logger.exception("断线时保存最后一段字幕失败：sid=%s", sid)
                await db.rollback()
            finally:
                _mark_voice_flushed(sid, voice_flush_event)
            await asr.close()
            logger.info("Voice WS disconnected: sid=%s", sid)


def _safe_send(websocket: WebSocket, text: str) -> None:
    """从 ASR 回调（同步上下文）异步发送消息。"""
    try:
        asyncio.create_task(websocket.send_text(text))
    except RuntimeError:
        pass


async def _handle_json(
    websocket: WebSocket,
    db,
    sid: str,
    raw: str,
    turn_sentences: list[str],
    turn_analyses: list[dict],
    pcm_buffer=None,
    semantic_history: list[str] | None = None,
    baseline=None,
    smoother=None,
    start_timer=None,
    latest_partial_holder: list[str] | None = None,
    last_committed_holder: list[str] | None = None,
    voice_flush_event: asyncio.Event | None = None,
) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    mtype = msg.get("type", "")

    if mtype == "commit_answer":
        client_text = msg.get("payload", {}).get("text", "")
        backend_text = (
            "".join(turn_sentences)
            + (latest_partial_holder[0] if latest_partial_holder else "")
        ).strip()
        text = backend_text or client_text
        # 兜底：限时场景用户先开口（开场白还在播/没播完就跳阶段），从此刻起计时
        if start_timer:
            start_timer()
        sentence_analyses = list(turn_analyses)
        turn_sentences.clear()
        turn_analyses.clear()
        if semantic_history is not None:
            semantic_history.clear()
        if latest_partial_holder is not None:
            latest_partial_holder[0] = ""
        if text.strip() and last_committed_holder is not None:
            last_committed_holder[0] = text.strip()
        await _commit_answer(
            websocket,
            db,
            sid,
            text,
            pcm_buffer=pcm_buffer,
            baseline=baseline,
            smoother=smoother,
            sentence_analyses=sentence_analyses,
        )
    elif mtype == "start_stage":
        await _auto_next_question(websocket, db, sid)
    elif mtype == "start_solo_stage":
        await _start_solo_stage(websocket, db, sid, start_timer)
    elif mtype == "skip_topic":
        session = await interview.skip_interview_item(db, sid)
        await websocket.send_text(envelope(
            ServerMsgType.STAGE_CHANGED,
            stage=session.current_stage,
            progress=await interview.get_interview_progress(db, sid),
        ))
        if session.status == "completed":
            await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))
        else:
            await _auto_next_question(websocket, db, sid)
    elif mtype == "begin_timer":
        # 前端开场白播完：计时零点确认，从此刻起倒计时（非限时场景不回执）
        if start_timer and start_timer():
            await websocket.send_text(envelope(ServerMsgType.TIMER_STARTED))
    elif mtype == "finish_stage":
        # 限时场景：用户讲完本阶段（或计时器到点）→ 推进到下一阶段
        if start_timer:
            start_timer()  # 兜底：开场白没播完就点了「讲完了」
        await _finish_stage(
            websocket,
            db,
            sid,
            turn_sentences,
            turn_analyses,
            latest_partial_holder,
            last_committed_holder,
            pcm_buffer,
            semantic_history=semantic_history,
            explicit_text=msg.get("payload", {}).get("text", ""),
            baseline=baseline,
            smoother=smoother,
        )
    elif mtype == "end_interview":
        explicit_text = msg.get("payload", {}).get("text", "")
        await _flush_pending_answer(
            websocket,
            db,
            sid,
            turn_sentences,
            turn_analyses,
            latest_partial_holder,
            last_committed_holder,
            explicit_text=explicit_text,
            semantic_history=semantic_history,
            pcm_buffer=pcm_buffer,
            baseline=baseline,
            smoother=smoother,
        )
        await interview.complete_interview(db, sid)
        await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))
        if voice_flush_event is not None:
            _mark_voice_flushed(sid, voice_flush_event)
    # skip_tts 等其他消息暂不处理


async def _start_solo_stage(websocket: WebSocket, db, sid: str, start_timer=None) -> None:
    """汇报/演讲跳过 AI 开场，直接进入用户主讲与计时。"""
    session = await interview.get_session(db, sid)
    if session.scenario == "interview" or session.current_stage != "opening":
        return

    session = await interview.advance_stage(db, sid)
    await websocket.send_text(envelope(ServerMsgType.STAGE_CHANGED, stage=session.current_stage))
    if start_timer and start_timer():
        await websocket.send_text(envelope(ServerMsgType.TIMER_STARTED))


async def _flush_pending_answer(
    websocket: WebSocket,
    db,
    sid: str,
    turn_sentences: list[str],
    turn_analyses: list[dict],
    latest_partial_holder: list[str] | None,
    last_committed_holder: list[str] | None,
    *,
    semantic_history: list[str] | None = None,
    explicit_text: str = "",
    pcm_buffer=None,
    baseline=None,
    smoother=None,
) -> bool:
    """把尚在语音连接内存中的最后一轮回答可靠落库。

    final 句保存在 ``turn_sentences``，ASR 尚未定稿的最后一句保存在
    ``latest_partial_holder``。浏览器结束训练时还会携带它看到的完整字幕作为
    兜底。三者只在这个边界汇合，避免普通停顿就提前拆分或清空字幕。
    """
    latest_partial = latest_partial_holder[0] if latest_partial_holder else ""
    backend_text = ("".join(turn_sentences) + latest_partial).strip()
    client_text = explicit_text.strip() if isinstance(explicit_text, str) else ""
    text = backend_text or client_text

    # commit_answer 已经保存过、但用户在下一题出现前立即点击结束时，前端仍可能
    # 携带上一轮字幕。仅在服务端没有任何新识别内容时去掉这次精确重复。
    last_committed = last_committed_holder[0] if last_committed_holder else ""
    if text and not backend_text and text == last_committed:
        text = ""

    saved = False
    if text:
        await _commit_answer(
            websocket,
            db,
            sid,
            text,
            advance=False,
            pcm_buffer=pcm_buffer,
            baseline=baseline,
            smoother=smoother,
            sentence_analyses=turn_analyses,
        )
        saved = True
        if last_committed_holder is not None:
            last_committed_holder[0] = text

    turn_sentences.clear()
    turn_analyses.clear()
    if semantic_history is not None:
        semantic_history.clear()
    if latest_partial_holder is not None:
        latest_partial_holder[0] = ""
    return saved


async def _finish_stage(
    websocket: WebSocket,
    db,
    sid: str,
    turn_sentences: list[str],
    turn_analyses: list[dict],
    latest_partial_holder: list[str] | None = None,
    last_committed_holder: list[str] | None = None,
    pcm_buffer=None,
    semantic_history: list[str] | None = None,
    explicit_text: str = "",
    baseline=None,
    smoother=None,
) -> None:
    """限时场景结束当前阶段：先落库本轮已说的内容，再推进阶段。"""
    await _flush_pending_answer(
        websocket,
        db,
        sid,
        turn_sentences,
        turn_analyses,
        latest_partial_holder,
        last_committed_holder,
        semantic_history=semantic_history,
        explicit_text=explicit_text,
        pcm_buffer=pcm_buffer,
        baseline=baseline,
        smoother=smoother,
    )
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
    sentence_analyses: list[dict] | None = None,
) -> None:
    """提交回答（分析已逐句推送过）。advance=True 时自动生成下一题。"""
    if not text.strip():
        return

    # 整轮分析（用于落库；前端展示已由句子级推送完成）
    text_res = analyze_text(text)
    voice_feats = None
    if pcm_buffer is not None:
        _, voice_feats = pcm_buffer.tension()  # 冲掉缓冲里最后一段（<0.5s 返回 None）
    # 旧协议兼容：整轮语速 + 基线 + 平滑
    speech_rate = None
    if voice_feats and voice_feats.duration_sec > 0:
        speech_rate = len(text) / voice_feats.duration_sec
    emotion = analyze_emotion(
        text_res, voice_feats,
        baseline=baseline, speech_rate=speech_rate, smoother=smoother,
    )
    snapshots = list(sentence_analyses or [])
    if voice_feats is not None:
        snapshots.append({
            "tension_score": emotion.tension_score,
            "confidence_score": emotion.confidence_score,
            "voice_signal": True,
            "calibrated": emotion.calibrated,
            "pitch_jitter": round(voice_feats.pitch_jitter, 4),
            "pause_count": voice_feats.pause_count,
            "hesitation_count": voice_feats.hesitation_count,
            "avg_pause_duration": round(voice_feats.avg_pause_duration, 2),
            "speech_duration_sec": round(voice_feats.duration_sec, 2),
        })
    voice_summary = aggregate_sentence_analyses(snapshots)
    analysis_payload = {
        "text": text,
        "warning_level": text_res.warning_level,
        "filler_hits": text_res.filler_hits,
        "hedge_hits": text_res.hedge_hits,
        "uncertain_hits": text_res.uncertain_hits,
        "repeated_words": text_res.repeated_words,
        "consecutive_repetition_hits": text_res.consecutive_repetition_hits,
        "semantic_repetitions": [
            match
            for item in (sentence_analyses or [])
            for match in (
                item.get("semantic_repetitions")
                or ([item["semantic_repetition"]] if item.get("semantic_repetition") else [])
            )
        ],
        "long_sentences": text_res.long_sentences,
        "repetition_rate": text_res.repetition_rate,
        "tension_score": voice_summary.get("tension_score", emotion.tension_score),
        "tension_level": emotion.tension_level,
        "confidence_score": voice_summary.get("confidence_score", emotion.confidence_score),
        "confidence_level": emotion.confidence_level,
        "voice_signal": voice_summary.get("voice_signal", False),
        "calibrated": voice_summary.get("calibrated", False),
        "factors": emotion.factors,
        **voice_summary,
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
            await websocket.send_text(envelope(
                ServerMsgType.STAGE_CHANGED,
                stage=session.current_stage,
                progress=await interview.get_interview_progress(db, sid),
            ))
            if session.status == "completed":
                await websocket.send_text(envelope(ServerMsgType.INTERVIEW_COMPLETED))
                return

        # 当前阶段不生成 AI 发言（如演讲 presenting 阶段）→ 只广播阶段，等用户讲完 finish_stage
        session = await interview.get_session(db, sid)
        pack = get_pack(session.scenario)
        from sqlalchemy import select
        row = (await db.execute(select(InterviewSessionRow).where(InterviewSessionRow.id == sid))).scalar_one_or_none()
        if row is not None:
            if pack.key != "interview":
                stage_def = next((s for s in pack.stages if s.key == row.current_stage), None)
                if stage_def is not None and stage_def.question_limit == 0:
                    return

        text = await interview.generate_next(db, sid)
        session = await interview.get_session(db, sid)
        # ① 文字先行（前端立即显示，消除"等语音"的感知延迟）
        await websocket.send_text(
            envelope(
                ServerMsgType.AI_QUESTION,
                stage=session.current_stage,
                text=text,
                # 前端据此决定问题是语音浮层还是文字浮层；问题本身不再
                # 常驻占用用户回答字幕区域。
                delivery="voice" if pack.key == "interview" else "text",
                progress=await interview.get_interview_progress(db, sid),
            )
        )
        # 面试保留 AI 语音问答；汇报/演讲只保留文字提示，不要求配置 TTS。
        if pack.key == "interview":
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
                try:
                    await websocket.send_text(envelope(
                        ServerMsgType.AI_AUDIO_UNAVAILABLE,
                        text=sentences[i],
                        reason="AI 部分语音暂时不可用",
                        can_continue=True,
                    ))
                except Exception:
                    logger.debug("发送 AI 部分音频不可用通知失败", exc_info=True)
    except Exception as e:
        logger.warning("流式 TTS 失败（不影响文字流程）：%s", e)
        # AI_QUESTION 已先发送。明确告知前端“文字仍可继续”，避免状态
        # 永远停在 thinking 等不到首个音频帧。
        try:
            await websocket.send_text(envelope(
                ServerMsgType.AI_AUDIO_UNAVAILABLE,
                text=text,
                reason="AI 语音暂时不可用",
                can_continue=True,
            ))
        except Exception:
            logger.debug("发送 AI 音频不可用通知失败", exc_info=True)
