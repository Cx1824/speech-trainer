"""Edge TTS Provider（免费，无需 API Key）。

走微软 Edge 浏览器朗读接口（edge-tts 库）：
- 免费、无额度限制
- 输出 mp3（24kHz），前端浏览器原生可播
- 延迟 2-3.5s/句（CosyVoice 约 1.8s）

音色（存 config.model）：
- zh-CN-YunxiNeural  云希·年轻男声（沉稳）
- zh-CN-YunjianNeural 云健·浑厚男声（面试官感）
- zh-CN-XiaoxiaoNeural 晓晓·女声（亲切）
"""

from __future__ import annotations

import logging

from app.core.exceptions import ProviderError
from app.providers.base import BaseTTSProvider, registry
from app.schemas import ProviderConfigIn

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "zh-CN-YunjianNeural"

# 常用中文音色（设置页下拉用）
EDGE_VOICES = [
    {"value": "zh-CN-YunjianNeural", "label": "云健（浑厚男声·面试官感）"},
    {"value": "zh-CN-YunxiNeural", "label": "云希（年轻男声·沉稳）"},
    {"value": "zh-CN-YunyangNeural", "label": "云扬（新闻男声·正式）"},
    {"value": "zh-CN-XiaoxiaoNeural", "label": "晓晓（女声·亲切）"},
    {"value": "zh-CN-XiaoyiNeural", "label": "晓伊（女声·温柔）"},
]


class EdgeTTS(BaseTTSProvider):
    """微软 Edge TTS（免费）。"""

    name = "edge"

    def __init__(self, config: ProviderConfigIn) -> None:
        super().__init__(config)
        self.voice = config.model or DEFAULT_VOICE

    async def health_check(self) -> bool:
        # 免费服务无需 Key，探测一次合成即可；这里直接返回 True（连接测试走 synthesize）
        return True

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        """文本 -> mp3 二进制。"""
        import edge_tts

        text = (text or "").strip()
        if not text:
            return b""
        try:
            comm = edge_tts.Communicate(text, voice or self.voice)
            chunks: list[bytes] = []
            async for ch in comm.stream():
                if ch["type"] == "audio":
                    chunks.append(ch["data"])
            audio = b"".join(chunks)
            if not audio:
                raise ProviderError("Edge TTS 返回空音频")
            return audio
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Edge TTS 合成失败：{e}") from e


registry.register(EdgeTTS)
