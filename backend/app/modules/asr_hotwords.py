"""ASR 热词管理。

调 DashScope speech-biasing 接口，为每个会话动态创建热词表：
- 从简历（技能/项目/公司）、汇报/演讲材料、JD 中提取关键词
- 创建 vocabulary → 返回 vocabulary_id → ASR 启动时带上

API（HTTP，同步）：
POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization
  {"model": "speech-biasing", "input": {"action": "create_vocabulary",
   "target_model": "paraformer-realtime-v2", "prefix": "...",
   "vocabulary": [{"text": "词", "weight": 5, "lang": "zh"}]}}

限制：单表最多 500 词。
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VOCAB_API = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/customization"
MAX_WORDS = 500
WORD_MAX_LEN = 16


def extract_hotwords(
    *,
    resume: dict[str, Any] | None = None,
    material_text: str = "",
    jd_content: str = "",
    position: str = "",
    company: str = "",
) -> list[str]:
    """从会话上下文提取热词（去重、限长、限量）。"""
    words: list[str] = []

    def add(w: str) -> None:
        w = w.strip()
        if w and len(w) <= WORD_MAX_LEN and w not in words:
            words.append(w)

    # 岗位/公司（最高价值；"未指定"等占位值无热词价值）
    if position and position != "未指定":
        add(position)
    add(company)

    # 简历：技能 + 项目名 + 公司名
    if resume:
        for skill in (resume.get("skills") or [])[:30]:
            add(str(skill))
        for p in (resume.get("projects") or [])[:10]:
            add(str(p.get("name", "")))
        for w in (resume.get("work") or [])[:5]:
            add(str(w.get("company", "")))
            add(str(w.get("title", "")))

    # 材料/JD 文本：提取技术词、英文词、数字指标词
    for text in (material_text, jd_content):
        if not text:
            continue
        # 英文词（技术名词：React/K8s/OKR/DAU...）
        for m in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{1,15}", text):
            add(m)
        # 中文短语（2-6字的连续汉字段，取出现频次高的）
        segs: dict[str, int] = {}
        for seg in re.findall(r"[\u4e00-\u9fa5]{2,6}", text):
            segs[seg] = segs.get(seg, 0) + 1
        top = sorted(segs.items(), key=lambda x: -x[1])[:80]
        for seg, _ in top:
            add(seg)

    return words[:MAX_WORDS]


async def create_vocabulary(api_key: str, words: list[str]) -> str | None:
    """创建 DashScope 热词表，返回 vocabulary_id；失败返回 None（不阻断 ASR）。"""
    if not words:
        return None
    payload = {
        "model": "speech-biasing",
        "input": {
            "action": "create_vocabulary",
            "target_model": "paraformer-realtime-v2",
            "prefix": f"st-{uuid.uuid4().hex[:8]}",
            "vocabulary": [
                {"text": w, "weight": 5, "lang": "en" if re.match(r"^[A-Za-z0-9.+-]+$", w) else "zh"}
                for w in words
            ],
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                VOCAB_API,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
            data = r.json()
            vocab_id = data.get("output", {}).get("vocabulary_id", "")
            if vocab_id:
                logger.info("热词表已创建：%s（%d 词）", vocab_id, len(words))
                return vocab_id
            logger.warning("热词表创建响应无 vocabulary_id：%s", data)
            return None
    except Exception as e:
        logger.warning("热词表创建失败（ASR 将无热词运行）：%s", e)
        return None
