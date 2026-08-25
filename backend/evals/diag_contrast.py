"""反差集诊断：逐条打印声学明细（修复前后对照用）。

用法：.venv/bin/python evals/diag_contrast.py [--dir evals/listen]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from evals.eval_listen import to_wav16k, wav_bytes_to_pcm
from app.modules.analysis.pcm_features import PcmFeatureBuffer, FRAME_LEN, FRAME_MS


def envelope_10ms(y: np.ndarray, sr: int) -> np.ndarray:
    """10ms hop 能量包络（音节检测用，比 25ms 帧分辨率高）。"""
    hop = sr // 100
    n = len(y) // hop
    frames = y[: n * hop].reshape(n, hop)
    return np.sum(frames * frames, axis=1)


def syllable_peaks(env: np.ndarray, hop_ms: float = 10.0) -> int:
    """能量包络峰计数（音节核估计）：峰值≥30%最高峰、峰间距≥100ms。"""
    if len(env) == 0:
        return 0
    thresh = np.max(env) * 0.30
    min_dist = int(100 / hop_ms)
    count = 0
    last = -10**9
    for i in range(1, len(env) - 1):
        if env[i] >= thresh and env[i] >= env[i - 1] and env[i] > env[i + 1]:
            if i - last >= min_dist:
                count += 1
                last = i
    return count


def diag_one(path: Path) -> dict:
    wav = to_wav16k(path)
    pcm = wav_bytes_to_pcm(wav)
    buf = PcmFeatureBuffer()
    for i in range(0, len(pcm), 6400):
        buf.push(pcm[i:i + 6400])
    feats = buf.flush()

    # 重新取原始样本算停顿直方图 + 音节率
    n = len(pcm) // 2
    y = np.frombuffer(pcm, dtype=np.int16)[: n].astype(np.float64) / 32768.0
    frames = y[: len(y) // FRAME_LEN * FRAME_LEN].reshape(-1, FRAME_LEN)
    energy = np.sum(frames * frames, axis=1)

    # 停顿 run 直方图（两种阈值对比：峰值5% vs 噪声底）
    runs = {}
    for name, th in [("peak5%", np.max(energy) * 0.05),
                     ("p10x3", np.percentile(energy, 10) * 3)]:
        silent = energy < th
        durs, cur = [], 0
        for s in silent:
            cur = cur + 1 if s else (durs.append(cur), 0)[1] if cur else 0
        if cur:
            durs.append(cur)
        runs[name] = sorted(round(d * FRAME_MS / 1000, 2) for d in durs if d * FRAME_MS / 1000 >= 0.15)

    env = envelope_10ms(y, 16000)
    syl = syllable_peaks(env)

    return {
        "id": path.stem,
        "sec": round(feats.duration_sec, 1),
        "f0": round(feats.pitch_mean, 1),
        "f0std": round(feats.pitch_std, 1),
        "jitter": round(feats.pitch_jitter, 4),
        "syl": syl,
        "syl_rate": round(syl / feats.duration_sec, 2),
        "runs_peak5": runs["peak5%"],
        "runs_p10x3": runs["p10x3"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="evals/listen")
    args = ap.parse_args()
    files = sorted((Path(args.dir) / "audio").glob("*.wav"))
    for f in files:
        d = diag_one(f)
        print(f"\n== {d['id']} ({d['sec']}s) f0={d['f0']}±{d['f0std']} jitter={d['jitter']}")
        print(f"   音节峰 {d['syl']} 个 → {d['syl_rate']}/秒")
        r5 = d["runs_peak5"]
        print(f"   停顿(峰值5%): {len(r5)} 个 ≥0.2s {r5}")
        r10 = d["runs_p10x3"]
        print(f"   停顿(p10×3):  {len(r10)} 个 ≥0.2s {r10}")


if __name__ == "__main__":
    main()
