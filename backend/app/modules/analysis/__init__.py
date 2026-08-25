"""分析模块对外接口。"""

from app.modules.analysis.emotion import (
    EmotionSnapshot,
    EmotionSmoother,
    analyze_emotion,
)
from app.modules.analysis.pcm_features import CALIBRATION_TEXT, PcmFeatureBuffer, build_baseline
from app.modules.analysis.summary import aggregate_sentence_analyses
from app.modules.analysis.text_rules import (
    AnalysisResult,
    analyze_text,
    compute_speech_rate,
    detect_consecutive_repetitions,
    detect_expression_breaks,
    detect_semantic_repetition,
    detect_semantic_repetitions,
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
    "detect_consecutive_repetitions",
    "detect_expression_breaks",
    "detect_semantic_repetition",
    "detect_semantic_repetitions",
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
    "aggregate_sentence_analyses",
]
