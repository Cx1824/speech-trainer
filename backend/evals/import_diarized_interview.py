"""Import a diarized two-person call as a normal interview report.

The diarization transcript supplies the question/answer boundaries. Acoustic
facts are computed only from the selected user's original time ranges, so the
other speaker's voice and the time spent listening cannot affect expression
metrics. The resulting session still uses the production report generator.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.core.database import get_db_session, init_db
from app.models.interview import InterviewDialogueRow, InterviewSessionRow
from app.modules.analysis import PcmFeatureBuffer, VoiceBaseline, analyze_emotion, analyze_text
from app.modules.config.store import load_voice_baseline


@dataclass(frozen=True)
class SpeakerTurn:
    speaker_id: int
    begin_ms: int
    end_ms: int
    text: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="16 kHz, 16-bit, mono WAV")
    parser.add_argument("diarization", type=Path, help="Diarization JSON with sentence timestamps")
    parser.add_argument("--user-speaker", type=int, required=True)
    parser.add_argument("--position", default="通话面试复盘")
    parser.add_argument("--company", default="")
    parser.add_argument("--level", default="中级")
    parser.add_argument("--jd-content", default="")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    return parser.parse_args()


def group_speaker_turns(sentences: list[dict[str, Any]]) -> list[SpeakerTurn]:
    """Merge only adjacent sentences from the same diarized speaker."""
    turns: list[SpeakerTurn] = []
    for sentence in sentences:
        speaker_id = int(sentence["speaker_id"])
        begin_ms = int(sentence["begin_time"])
        end_ms = int(sentence["end_time"])
        text = str(sentence.get("text", "")).strip()
        if not text or end_ms <= begin_ms:
            continue
        if turns and turns[-1].speaker_id == speaker_id:
            previous = turns[-1]
            turns[-1] = SpeakerTurn(
                speaker_id=speaker_id,
                begin_ms=previous.begin_ms,
                end_ms=max(previous.end_ms, end_ms),
                text=(previous.text + text).strip(),
            )
        else:
            turns.append(SpeakerTurn(speaker_id, begin_ms, end_ms, text))
    return turns


def _read_pcm(path: Path) -> tuple[bytes, int, float]:
    with wave.open(str(path), "rb") as wav:
        params = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
        if params != (1, 2, 16_000):
            raise ValueError(
                "Audio must be mono/16-bit/16kHz, got "
                f"channels={params[0]}, width={params[1] * 8}-bit, rate={params[2]}Hz"
            )
        frames = wav.readframes(wav.getnframes())
        return frames, wav.getframerate(), wav.getnframes() / wav.getframerate()


def _slice_pcm(pcm: bytes, sample_rate: int, begin_ms: int, end_ms: int) -> bytes:
    frame_bytes = 2
    begin = max(0, round(begin_ms * sample_rate / 1000)) * frame_bytes
    end = min(len(pcm), round(end_ms * sample_rate / 1000) * frame_bytes)
    return pcm[begin:end]


def _analyze_user_turn(
    text: str,
    pcm: bytes,
    baseline: VoiceBaseline | None,
) -> dict[str, Any]:
    text_result = analyze_text(text)
    buffer = PcmFeatureBuffer()
    buffer.push(pcm)
    _, voice_features = buffer.tension()
    speech_rate = None
    if voice_features and voice_features.duration_sec > 0:
        speech_rate = len(text) / voice_features.duration_sec
    emotion = analyze_emotion(
        text_result,
        voice_features,
        baseline=baseline,
        speech_rate=speech_rate,
    )

    payload: dict[str, Any] = {
        "text": text,
        "warning_level": text_result.warning_level,
        "filler_hits": text_result.filler_hits,
        "hedge_hits": text_result.hedge_hits,
        "uncertain_hits": text_result.uncertain_hits,
        "repeated_words": text_result.repeated_words,
        "consecutive_repetition_hits": text_result.consecutive_repetition_hits,
        "long_sentences": text_result.long_sentences,
        "repetition_rate": text_result.repetition_rate,
        "tension_score": emotion.tension_score,
        "tension_level": emotion.tension_level,
        "confidence_score": emotion.confidence_score,
        "confidence_level": emotion.confidence_level,
        "voice_signal": voice_features is not None,
        "calibrated": emotion.calibrated,
        "factors": emotion.factors,
    }
    if voice_features is not None:
        payload.update(
            {
                "pitch_jitter": round(voice_features.pitch_jitter, 4),
                "pause_count": voice_features.pause_count,
                "hesitation_count": voice_features.hesitation_count,
                "avg_pause_duration": round(voice_features.avg_pause_duration, 2),
                "speech_duration_sec": round(voice_features.duration_sec, 2),
            }
        )
    return payload


async def _run(args: argparse.Namespace) -> None:
    data = json.loads(args.diarization.read_text(encoding="utf-8"))
    sentences = data["transcripts"][0]["sentences"]
    turns = group_speaker_turns(sentences)
    if not any(turn.speaker_id == args.user_speaker for turn in turns):
        raise ValueError(f"Speaker {args.user_speaker} does not exist in the diarization result")

    pcm, sample_rate, audio_duration = _read_pcm(args.audio)
    await init_db()
    session_factory = await get_db_session()
    sid = str(uuid.uuid4())
    async with session_factory as db:
        baseline_data = await load_voice_baseline(db)
        baseline = VoiceBaseline.from_dict(baseline_data) if baseline_data else None
        session = InterviewSessionRow(
            id=sid,
            scenario="interview",
            position=args.position,
            level=args.level,
            style="professional",
            company=args.company,
            jd_content=args.jd_content,
            duration_limit=0,
            started_at=datetime.now() - timedelta(seconds=audio_duration),
            status="in_progress",
            current_stage="project",
        )
        db.add(session)

        seq = 0
        user_turns = 0
        for turn in turns:
            seq += 1
            is_user = turn.speaker_id == args.user_speaker
            analysis = None
            if is_user:
                user_turns += 1
                turn_pcm = _slice_pcm(pcm, sample_rate, turn.begin_ms, turn.end_ms)
                analysis = _analyze_user_turn(turn.text, turn_pcm, baseline)
            db.add(
                InterviewDialogueRow(
                    id=str(uuid.uuid4()),
                    session_id=sid,
                    seq=seq,
                    role="user" if is_user else "ai",
                    stage="project",
                    text=turn.text,
                    analysis_json=json.dumps(analysis, ensure_ascii=False) if analysis else "",
                )
            )
        await db.commit()

    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(f"{args.base_url.rstrip('/')}/api/v1/reports/{sid}")
        response.raise_for_status()
        report = response.json()

    print(
        json.dumps(
            {
                "session": sid,
                "turns": len(turns),
                "user_turns": user_turns,
                "overall_score": report.get("overall_score"),
                "expression_metrics": report.get("expression_metrics"),
                "axes": report.get("axes"),
                "report_url": f"/report/{sid}?scenario=interview",
            },
            ensure_ascii=False,
        )
    )


def main() -> None:
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()
