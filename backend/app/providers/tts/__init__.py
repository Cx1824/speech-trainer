"""TTS Providers 导出。"""

from __future__ import annotations

from .cosyvoice import CosyVoiceTTS, CosyVoiceTTSFlash
from .openai_compatible import MiniMaxTTS, OpenAICompatibleTTS, OpenAITTS
from .qwen_audio import AliyunQwenTTS, QwenAudioTTS

__all__ = [
    "OpenAICompatibleTTS",
    "OpenAITTS",
    "MiniMaxTTS",
    "QwenAudioTTS",
    "AliyunQwenTTS",
    "CosyVoiceTTS",
    "CosyVoiceTTSFlash",
]
