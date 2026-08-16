"""声学管线评测：合成真值集 vs 管线输出 → markdown 报表。

用法（backend venv）：
    .venv/bin/python evals/run_eval.py [--dataset evals/dataset] [--report evals/REPORT.md]

评测项与真值口径：
  A. 基频（f0）
     - 同音色跨条稳定性：f0 中位数变异应 <8%（音色固定）
     - 性别区间：男声 85-200Hz、女声 160-320Hz（八度错误直接暴露）
  B. 语速（相对）
     - rate=±X% 注入 → 相对 r=0 的 字/发音秒 比值应≈1+X/100
     - 用 LivePcmTracker.speech_sec 做分母（与线上同口径）
  C. jitter（平稳参考带）
     - TTS 平稳发声 → detrended jitter 分布记录为"机器平稳带"
     - 真人紧张应显著高于此带（后续真人阶段使用）
  D. 停顿计数
     - base 文本含固定标点停顿位 → 记录 pause_count 分布（一致性参考）
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from app.modules.analysis.pcm_features import LivePcmTracker, PcmFeatureBuffer


def load_wav(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def eval_f0(items: list[dict], audio_dir: Path) -> list[dict]:
    """整段 PcmFeatureBuffer：每条输出 pitch_mean（中位数口径在报表聚合）。"""
    rows = []
    for m in items:
        buf = PcmFeatureBuffer()
        buf.push(load_wav(audio_dir / f"{m['id']}.wav"))
        feats = buf.flush()
        rows.append({
            "id": m["id"], "voice": m["voice"], "gender": m["voice_gender"],
            "text_key": m["text_key"], "rate": m["rate_percent"],
            "pitch_mean": feats.pitch_mean, "pitch_std": feats.pitch_std,
            "pitch_jitter": feats.pitch_jitter, "pause_count": feats.pause_count,
            "duration_sec": m["duration_sec"],
        })
    return rows


def eval_speech_rate(items: list[dict], audio_dir: Path) -> dict[str, float]:
    """LivePcmTracker：每条输出 chars/speech_sec（真实训练同口径的语速分母）。"""
    out: dict[str, float] = {}
    for m in items:
        if m["text_key"] != "base":
            continue  # 语速比值只在同文本（base）内比
        tr = LivePcmTracker()
        pcm = load_wav(audio_dir / f"{m['id']}.wav")
        # 按 100ms chunk 喂（模拟线上流式）
        for i in range(0, len(pcm), 3200):
            tr.push(pcm[i:i + 3200])
        if tr.speech_sec > 0.5:
            out[m["id"]] = m["char_count"] / tr.speech_sec
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="evals/dataset")
    ap.add_argument("--report", default="evals/REPORT.md")
    args = ap.parse_args()
    ds = Path(args.dataset)
    audio_dir = ds / "audio"
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    print(f"载入 {len(manifest)} 条")

    f0_rows = eval_f0(manifest, audio_dir)
    rate_map = eval_speech_rate(manifest, audio_dir)

    # ---- 聚合与判定 ----
    lines: list[str] = ["# 声学管线评测报告（合成真值集）", ""]

    # A. 基频
    lines += ["## A. 基频（f0）", "", "| 音色 | 条数 | f0中位数 | 跨条CV | 判定 |", "|---|---|---|---|---|"]
    summary: dict[str, list[float]] = {}
    for r in f0_rows:
        summary.setdefault(r["voice"], []).append(r["pitch_mean"])
    gender = {r["voice"]: r["gender"] for r in f0_rows}
    f0_pass = True
    for voice, vals in summary.items():
        arr = np.array(vals)
        med = float(np.median(arr))
        cv = float(np.std(arr) / np.mean(arr)) if np.mean(arr) > 0 else 1.0
        # 男声 85-210Hz（男高音上限 ~200Hz，TTS 年轻男声可略超）、女声 160-320Hz
        lo, hi = (85, 210) if gender[voice] == "Male" else (160, 320)
        in_band = lo <= med <= hi
        stable = cv < 0.08
        ok = in_band and stable
        f0_pass &= ok
        lines.append(f"| {voice} | {len(arr)} | {med:.1f} | {cv*100:.1f}% | "
                     f"{'✅' if ok else '❌'}（区间{'✓' if in_band else '✗'} 稳定{'✓' if stable else '✗'}） |")

    # B. 语速相对比
    lines += ["", "## B. 语速（相对注入 rate）", "", "| 音色 | 注入 | 实测字/秒 | 相对比 | 期望 | 误差 | 判定 |", "|---|---|---|---|---|---|---|"]
    rate_pass = True
    by_voice_rate: dict[tuple[str, int], float] = {}
    for rid, v in rate_map.items():
        voice = rid.rsplit("_", 1)[0]
        rate = int(rid.rsplit("_r", 1)[1])
        by_voice_rate[(voice, rate)] = v
    voices = sorted({k[0] for k in by_voice_rate})
    for voice in voices:
        base_v = by_voice_rate.get((voice, 0))
        if not base_v:
            continue
        for rate in (-20, -10, 0, 10, 20):
            v = by_voice_rate.get((voice, rate))
            if v is None:
                continue
            ratio = v / base_v
            expect = 1 + rate / 100
            err = abs(ratio - expect) / expect
            ok = err < 0.10 or rate == 0
            rate_pass &= ok
            lines.append(f"| {voice} | {rate:+d}% | {v:.2f} | {ratio:.3f} | {expect:.2f} | {err*100:.1f}% | {'✅' if ok else '❌'} |")

    # C. jitter 平稳带
    jit_vals = [r["pitch_jitter"] for r in f0_rows if r["pitch_jitter"] > 0]
    lines += ["", "## C. jitter（TTS 平稳参考带）", ""]
    if jit_vals:
        arr = np.array(jit_vals)
        lines += [f"- 条目数 {len(arr)}，中位数 {np.median(arr):.4f}，P90 {np.percentile(arr, 90):.4f}，最大 {arr.max():.4f}",
                  f"- 机器平稳带参考：≤ {np.percentile(arr, 95):.4f}（真人紧张应显著高于此带，后续真人阶段校验）"]

    # D. 停顿分布（一致性参考，不设硬线）
    lines += ["", "## D. 停顿计数分布（base 文本，参考）", "",
              "| 音色 | rate | pause_count |", "|---|---|---|"]
    for r in f0_rows:
        if r["text_key"] == "base":
            lines.append(f"| {r['voice']} | {r['rate']:+d}% | {r['pause_count']} |")

    lines += ["", "## 总判定", "",
              f"- 基频：{'✅ PASS' if f0_pass else '❌ FAIL'}",
              f"- 语速：{'✅ PASS（相对误差<10%）' if rate_pass else '❌ FAIL'}",
              f"- jitter/停顿：参考带已记录（无硬线）", ""]

    report = Path(args.report)
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[-8:]))
    print(f"\n报表 → {report}")


if __name__ == "__main__":
    main()
