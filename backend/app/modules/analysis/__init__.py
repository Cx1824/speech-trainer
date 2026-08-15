"""分析模块对外接口。"""

from app.modules.analysis.emotion import EmotionSnapshot, analyze_emotion
from app.modules.analysis.text_rules import (
    AnalysisResult,
    analyze_text,
    compute_speech_rate,
    rate_speech_rate,
)
from app.modules.analysis.voice_features import (
    VoiceFeatures,
    compute_tension,
    extract_features,
)

__all__ = [
    "analyze_text",
    "compute_speech_rate",
    "rate_speech_rate",
    "extract_features",
    "compute_tension",
    "analyze_emotion",
    "AnalysisResult",
    "VoiceFeatures",
    "EmotionSnapshot",
]
