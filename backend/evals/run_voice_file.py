"""Send a local WAV file through the production voice WebSocket.

This is an end-to-end evaluation helper, not an alternative analysis path. It
creates a normal training session, streams 16 kHz PCM frames at recording
speed, commits the ASR transcript, and asks the normal report API for results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets


CHUNK_MS = 100
SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="16 kHz, 16-bit, mono WAV file")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", default="speech")
    parser.add_argument("--topic", default="录音导入测试")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="streaming speed multiplier; 1.0 preserves real-time timing",
    )
    return parser.parse_args()


def _read_pcm(path: Path) -> tuple[bytes, float]:
    with wave.open(str(path), "rb") as wav:
        params = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate())
        expected = (1, SAMPLE_WIDTH, SAMPLE_RATE)
        if params != expected:
            raise ValueError(
                f"WAV format must be mono/16-bit/16kHz, got "
                f"channels={params[0]}, width={params[1] * 8}-bit, rate={params[2]}Hz"
            )
        frames = wav.readframes(wav.getnframes())
        return frames, wav.getnframes() / SAMPLE_RATE


def _ws_url(base_url: str, sid: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/ws/voice/{sid}"


async def _run(args: argparse.Namespace) -> None:
    if args.speed <= 0:
        raise ValueError("--speed must be greater than zero")
    pcm, duration_sec = _read_pcm(args.audio)
    api = args.base_url.rstrip("/") + "/api/v1"

    async with httpx.AsyncClient(timeout=180) as client:
        created = await client.post(
            f"{api}/interviews",
            json={
                "scenario": args.scenario,
                "position": args.topic,
                "style": "professional",
                "duration_limit": max(1, round(duration_sec / 60)),
            },
        )
        created.raise_for_status()
        sid = created.json()["id"]

        started = await client.post(f"{api}/interviews/{sid}/start")
        started.raise_for_status()
        print(f"session={sid}", flush=True)

        final_parts: list[str] = []
        latest_partial = ""
        committed_transcript = ""
        server_errors: list[str] = []
        presenting = asyncio.Event()
        audio_finished = asyncio.Event()
        completed = asyncio.Event()
        server_error = asyncio.Event()

        async with websockets.connect(_ws_url(args.base_url, sid), max_size=16 * 1024 * 1024) as ws:
            async def receive_messages() -> None:
                nonlocal latest_partial
                async for raw in ws:
                    if not isinstance(raw, str):
                        continue
                    message = json.loads(raw)
                    kind = message.get("type")
                    payload = message.get("payload", {})
                    if kind == "speech_recognized":
                        text = str(payload.get("text", ""))
                        if payload.get("is_final"):
                            final_parts.append(text)
                            latest_partial = ""
                            print(f"asr-final: {text}", flush=True)
                        else:
                            latest_partial = text
                    elif kind == "stage_changed" and payload.get("stage") == "presenting":
                        presenting.set()
                    elif kind == "audio_finished":
                        audio_finished.set()
                    elif kind == "interview_completed":
                        completed.set()
                    elif kind == "error":
                        error_message = str(payload.get("message", ""))
                        server_errors.append(error_message)
                        server_error.set()
                        print(f"server-error: {error_message}", flush=True)

            receiver = asyncio.create_task(receive_messages())
            try:
                # The regular UI plays an AI opening before entering the presenting
                # stage. File evaluation skips playback but keeps the same state
                # transition and production audio/report path.
                await ws.send(json.dumps({"type": "finish_stage"}))
                # ASR startup may include a provider-side hotword attempt before
                # the voice loop can process this transition.
                stage_wait = asyncio.create_task(presenting.wait())
                error_wait = asyncio.create_task(server_error.wait())
                done, pending = await asyncio.wait(
                    {stage_wait, error_wait},
                    timeout=40,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                if server_errors:
                    raise RuntimeError(f"voice server reported: {'; '.join(server_errors)}")
                if stage_wait not in done:
                    raise TimeoutError("voice session did not enter presenting stage")

                bytes_per_chunk = SAMPLE_RATE * SAMPLE_WIDTH * CHUNK_MS // 1000
                stream_started = time.monotonic()
                next_progress = 10
                for offset in range(0, len(pcm), bytes_per_chunk):
                    await ws.send(pcm[offset : offset + bytes_per_chunk])
                    sent_sec = min(offset + bytes_per_chunk, len(pcm)) / (SAMPLE_RATE * SAMPLE_WIDTH)
                    if sent_sec >= next_progress:
                        print(f"streamed={sent_sec:.0f}/{duration_sec:.0f}s", flush=True)
                        next_progress += 10
                    target_elapsed = sent_sec / args.speed
                    remaining = target_elapsed - (time.monotonic() - stream_started)
                    if remaining > 0:
                        await asyncio.sleep(remaining)

                # Preserve a short natural silence, then explicitly flush the
                # fixed recording so the provider cannot drop its final sentence.
                silence = bytes(bytes_per_chunk)
                for _ in range(8):
                    await ws.send(silence)
                    await asyncio.sleep(CHUNK_MS / 1000 / args.speed)
                await ws.send(json.dumps({"type": "finish_audio"}))
                await asyncio.wait_for(audio_finished.wait(), timeout=15)
                if server_errors:
                    raise RuntimeError(f"voice server reported: {'; '.join(server_errors)}")

                transcript = "".join(final_parts)
                if latest_partial and latest_partial not in transcript:
                    transcript += latest_partial
                transcript = transcript.strip()
                if not transcript:
                    raise RuntimeError("ASR returned no transcript")
                committed_transcript = transcript

                await ws.send(json.dumps({
                    "type": "commit_answer",
                    "payload": {"text": transcript},
                }, ensure_ascii=False))
                await asyncio.sleep(1)
                await ws.send(json.dumps({"type": "end_interview"}))
                await asyncio.wait_for(completed.wait(), timeout=15)
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)

        response = await client.post(f"{api}/reports/{sid}")
        response.raise_for_status()
        report = response.json()
        metrics = report.get("expression_metrics", {})
        print(f"transcript={committed_transcript}", flush=True)
        print(
            "report="
            + json.dumps(
                {
                    "overall_score": report.get("overall_score"),
                    "speech_rate": metrics.get("speech_rate"),
                    "filler_total": metrics.get("filler_total"),
                    "repetition_rate": metrics.get("repetition_rate"),
                    "short_pause_count": metrics.get("short_pause_count"),
                    "long_pause_count": metrics.get("long_pause_count"),
                    "axes": report.get("axes", []),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        print(f"report_url=/report/{sid}?scenario={args.scenario}", flush=True)


def main() -> None:
    asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    main()
