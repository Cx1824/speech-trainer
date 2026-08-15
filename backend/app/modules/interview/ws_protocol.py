"""面试 WebSocket 协议。

消息格式（JSON envelope）：
{
  "type": "message_type",
  "payload": {...}
}

消息类型：
- 客户端 → 服务端：
  * start_stage：请求生成下一题
  * user_answer：候选人回答（一段文字，由前端 ASR 后发送）
  * user_audio_chunk：音频片段（二进制，不走 JSON，单独帧）
  * finish_stage：手动结束当前阶段
  * end_interview：结束面试

- 服务端 → 客户端：
  * ai_question：AI 下一题（含 stage/text）
  * tts_audio：TTS 音频（base64 编码）
  * stage_changed：阶段切换
  * interview_completed：面试结束
  * error：错误
"""

from __future__ import annotations

import base64
import json
import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ClientMsgType(str, Enum):
    START_STAGE = "start_stage"
    USER_ANSWER = "user_answer"
    USER_SPEECH = "user_speech"          # 浏览器 ASR 识别后的实时文本（interim + final）
    FINISH_STAGE = "finish_stage"
    END_INTERVIEW = "end_interview"
    REQUEST_TTS = "request_tts"
    BEGIN_TIMER = "begin_timer"          # 前端开场白播完，从此刻起计时（限时场景）


class ServerMsgType(str, Enum):
    AI_QUESTION = "ai_question"
    TTS_AUDIO = "tts_audio"
    STAGE_CHANGED = "stage_changed"
    INTERVIEW_COMPLETED = "interview_completed"
    SPEECH_RECOGNIZED = "speech_recognized"   # 给前端用作弹幕/显示
    ANALYSIS_UPDATE = "analysis_update"       # 实时分析推送（口癖/重复/情绪）
    EMOTION_UPDATE = "emotion_update"
    TIME_UP = "time_up"                       # 限时场景到点（汇报/演讲）
    TIMER_STARTED = "timer_started"           # 计时已启动（开场白播完才计时的回执）
    LIVE_METRICS = "live_metrics"             # 实时指标（语速/紧张度，说话中滚动刷新）
    LIVE_FEEDBACK = "live_feedback"           # 即时反馈（词级/节奏，不定稿就推）
    ERROR = "error"


class ClientMessage(BaseModel):
    type: ClientMsgType
    payload: dict[str, Any] = Field(default_factory=dict)


def envelope(type_: ServerMsgType, **payload) -> str:
    return json.dumps({"type": type_.value, "payload": payload}, ensure_ascii=False)


def encode_audio(audio: bytes) -> str:
    return base64.b64encode(audio).decode("ascii")
