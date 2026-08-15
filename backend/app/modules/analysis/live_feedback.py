"""实时反馈引擎（partial 流式检测 + 节奏检测）。

两类反馈都「说话过程中即时」触发，不等句子定稿：
- 词级即时：口癖/模糊词/连读重复/超长未断句（扫描 ASR partial 增量文本）
- 节奏反馈：快说超时/该换气/冷场（基于 LivePcmTracker 的语音活动统计）

所有反馈带冷却（同类提醒间隔内不重复），避免刷屏。
输出结构统一：{"kind", "word", "advice"}，由 voice_ws 包装成 live_feedback 推送。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# 冷却秒数（同类提醒不重复触发窗口）
COOLDOWN = {
    "filler": 8.0,        # 口癖词（每词独立冷却）
    "hedge": 10.0,        # 模糊词（每词独立）
    "repeat": 8.0,        # 连读重复
    "long_sentence": 20.0,
    "fast_run": 15.0,     # 连续快说
    "no_breath": 12.0,    # 长时间无停顿
    "silence": 15.0,      # 冷场
}

# 连读重复检测：词尾立即重复（"就是就是"/"然后然后"）
_REPEAT_PAT = None  # 初始化见下（re 预编译）


def _repeat_pattern():
    global _REPEAT_PAT
    if _REPEAT_PAT is None:
        import re
        # 2-4 字中文词，紧接着原样重复一遍（中间可夹 1 个语气字）
        _REPEAT_PAT = re.compile(r"([\u4e00-\u9fa5]{2,4})(?:[嗯啊呃]{0,1})\1")
    return _REPEAT_PAT


class LiveFeedbackEngine:
    """会话级实时反馈状态机（每个语音会话一个实例）。"""

    def __init__(self) -> None:
        self._last_fire: dict[str, float] = {}       # key → monotonic 时间
        self._partial_chars_seen = 0                  # 当前 partial 累计已扫描字符数
        self._last_partial_text = ""
        # 超长句跟踪：当前未断句的字符数（partial 文本长度即当前句累积）
        self._long_warned_at_chars = 0

    def reset_sentence(self) -> None:
        """句子定稿后重置句子级状态。

        词级冷却一并清除（新的一句重新提示空间）；节奏类冷却保留（跨句时间窗）。
        """
        self._partial_chars_seen = 0
        self._last_partial_text = ""
        self._long_warned_at_chars = 0
        self._last_fire = {
            k: v for k, v in self._last_fire.items()
            if not k.startswith(("filler:", "hedge:", "repeat:")) and k != "long_sentence"
        }

    # ---- 节流 ----

    def _can_fire(self, key: str, cooldown: float) -> bool:
        now = time.monotonic()
        if now - self._last_fire.get(key, 0.0) < cooldown:
            return False
        self._last_fire[key] = now
        return True

    # ---- 词级即时（partial 驱动） ----

    def on_partial(self, text: str) -> list[dict]:
        """ASR partial 回调：扫描新增文本，返回即时反馈列表。

        partial 是「句内累积文本」，只扫上次之后的新增尾巴；
        同一词在一次 partial 中只报一次（靠冷却）。
        """
        feedbacks: list[dict] = []
        try:
            from app.modules.analysis.text_rules import FILLER_WORDS, HEDGING_WORDS

            # partial 可能回退/修正：文本变短视为重置
            if len(text) < len(self._last_partial_text):
                self._partial_chars_seen = 0
            self._last_partial_text = text

            # 只扫新增部分（尾部带上 4 字重叠，词跨 chunk 时不漏）
            start = max(0, self._partial_chars_seen - 4)
            new_part = text[start:]
            self._partial_chars_seen = len(text)

            # ① 口癖 + 模糊词
            for word, weight in FILLER_WORDS.items():
                if word in new_part and self._can_fire(f"filler:{word}", COOLDOWN["filler"]):
                    feedbacks.append({
                        "kind": "filler",
                        "word": word,
                        "advice": f"「{word}」出口了，停半拍再说",
                    })
            for word in HEDGING_WORDS:
                if word in new_part and self._can_fire(f"hedge:{word}", COOLDOWN["hedge"]):
                    feedbacks.append({
                        "kind": "hedge",
                        "word": word,
                        "advice": f"「{word}」显得不确定，试着给明确结论",
                    })

            # ② 连读重复（"就是就是"）
            for m in _repeat_pattern().finditer(new_part):
                word = m.group(1)
                if self._can_fire(f"repeat:{word}", COOLDOWN["repeat"]):
                    feedbacks.append({
                        "kind": "repeat",
                        "word": word,
                        "advice": f"「{word}{word}」连说了，说一遍就够",
                    })

            # ③ 超长未断句
            if (
                len(text) >= 60
                and len(text) - self._long_warned_at_chars >= 40
                and self._can_fire("long_sentence", COOLDOWN["long_sentence"])
            ):
                self._long_warned_at_chars = len(text)
                feedbacks.append({
                    "kind": "long_sentence",
                    "word": f"{len(text)} 字",
                    "advice": "这句话有点长了，说到重点就收句",
                })
        except Exception:
            logger.debug("词级即时检测失败", exc_info=True)
        return feedbacks

    # ---- 节奏反馈（PCM 统计驱动） ----

    def on_rhythm(
        self,
        speech_run_sec: float,      # 当前连续发音时长
        silence_sec: float,          # 当前连续静音时长
        speech_rate: float | None,   # 实时语速（字/秒，None=样本不足）
        base_rate: float,            # 基线语速
        speaking: bool,              # 当前是否在说话（非长时间静音）
    ) -> list[dict]:
        """节奏检测：连续快说 / 无停顿 / 冷场。由 voice_ws 周期调用。"""
        feedbacks: list[dict] = []
        try:
            # ① 冷场：静音 >3s（且会话在听）
            if speaking and silence_sec > 3.0 and self._can_fire("silence", COOLDOWN["silence"]):
                feedbacks.append({
                    "kind": "silence",
                    "word": f"{silence_sec:.0f}s",
                    "advice": "停顿有点久，用一句话接上就好",
                })
            # ② 该换气：连续发音 >15s 无停顿
            if speech_run_sec > 15.0 and self._can_fire("no_breath", COOLDOWN["no_breath"]):
                feedbacks.append({
                    "kind": "no_breath",
                    "word": f"{speech_run_sec:.0f}s",
                    "advice": "说了很久没换气，停一下节奏更好",
                })
            # ③ 连续快说：连续发音 >10s 且实时语速超基线 120%
            if (
                speech_run_sec > 10.0
                and speech_rate is not None
                and speech_rate > base_rate * 1.2
                and self._can_fire("fast_run", COOLDOWN["fast_run"])
            ):
                feedbacks.append({
                    "kind": "fast_run",
                    "word": f"{speech_rate:.1f}字/秒",
                    "advice": "语速偏快，放慢一点更从容",
                })
        except Exception:
            logger.debug("节奏检测失败", exc_info=True)
        return feedbacks
