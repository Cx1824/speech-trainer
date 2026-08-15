"""分析模块对外接口。"""

from app.modules.analysis.emotion import (
    EmotionSnapshot,
    EmotionSmoother,
    analyze_emotion,
)
from app.modules.analysis.pcm_features import CALIBRATION_TEXT, PcmFeatureBuffer, build_baseline
from app.modules.analysis.text_rules import (
    AnalysisResult,
    analyze_text,
    compute_speech_rate,
    rate_speech_rate,
)
from app.modules.analysis.voice_features import (
    DEFAULT_BASELINE,
    VoiceBaseline,
    VoiceFeatures,
    compute_tension,
    compute_tension_v2,
    extract_features,
)

__all__ = [
    "analyze_text",
    "compute_speech_rate",
    "rate_speech_rate",
    "extract_features",
    "compute_tension",
    "compute_tension_v2",
    "analyze_emotion",
    "AnalysisResult",
    "VoiceFeatures",
    "VoiceBaseline",
    "DEFAULT_BASELINE",
    "EmotionSnapshot",
    "EmotionSmoother",
    "PcmFeatureBuffer",
    "CALIBRATION_TEXT",
    "build_baseline",
]
