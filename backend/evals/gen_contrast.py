"""紧张度反差组合成器：edge-tts 参数注入 → 盲听对照集（阶段0 对照测试）。

用法（backend venv）：
    .venv/bin/python evals/gen_contrast.py [--out evals/listen]

产物：
    evals/listen/audio/tNN.wav        盲听音频（文件名随机，不泄露真值）
    evals/listen/key.json             文件名 → 配方/真值映射（盲听前别看！）
    evals/listen/ratings_ai.csv       构造真值分（AI 视角的"听感"）
    evals/listen/ratings_template.csv 用户盲听模板（eval_listen.py 生成）

设计（第一性）：
  紧张声音的听感特征 = 音调发飘（pitch+）+ 语速急促（rate+）
  + 磕巴停顿多（省略号/重复/口癖注入文本）。
  用 edge-tts 参数明确注入这些特征 → 真值由构造方式确定：
  人耳听 tNN.wav 也应给出与注入强度一致的排序。
  对比：.venv/bin/python evals/eval_listen.py --dir evals/listen --ratings
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import subprocess
import tempfile
from pathlib import Path

import edge_tts

F = "zh-CN-XiaoxiaoNeural"   # 女
M = "zh-CN-YunxiNeural"      # 男

CALM_TEXT = (
    "大家好，今天我想分享三点内容。第一，项目背景：我们在三月份启动了这个计划，"
    "目标是在两个月内完成上线。第二，核心进展：目前完成了大约百分之七十的里程碑，"
    "重点功能已经全部通过验收。第三，下一步：我建议这个月底前完成灰度发布，"
    "大概需要三天的观察期。以上就是我的汇报，谢谢大家。"
)

# 磕巴文本：省略号长停顿 + 重复 + 口癖 → 模拟停顿紊乱/磕巴
SHAKY_TEXT = (
    "大家好……嗯，今天我，我想讲三点。第一……就是，那个……项目背景，"
    "我们，嗯……在三月份，启动了，就是……这个计划。"
    "第二……嗯，进展，那个，大概，差不多……完成了百分之……嗯，七十。"
    "第三，就是……下一步，我，我建议，月底前……嗯，灰度发布。"
    "以上……嗯，就是，我的汇报，谢谢。"
)

# (label, voice, rate%, pitchHz, text, truth_1to5)
JOBS = [
    ("calm_f",        F,   0,   0, CALM_TEXT, 1.0),   # 平静朗读（女）
    ("calm_m_slow",   M, -10,   0, CALM_TEXT, 1.0),   # 沉稳慢速（男）
    ("mid_f",         F,  15,  15, CALM_TEXT, 3.0),   # 轻度急促（女）
    ("mid_m",         M,  15,  15, CALM_TEXT, 3.0),   # 轻度急促（男）
    ("tense_f",       F,  30,  40, CALM_TEXT, 4.0),   # 急促高音（女）
    ("tense_m",       M,  30,  40, CALM_TEXT, 4.0),   # 急促高音（男）
    ("verytense_f",   F,  50,  60, CALM_TEXT, 5.0),   # 极度急促（女）
    ("shaky_f",       F,   0,  10, SHAKY_TEXT, 4.0),  # 磕巴停顿多（女）
    ("shaky_m",       M,   0,  10, SHAKY_TEXT, 4.0),  # 磕巴停顿多（男）
]


async def synth(voice: str, rate: int, pitch: int, text: str) -> bytes:
    tts = edge_tts.Communicate(
        text, voice,
        rate=f"+{rate}%" if rate >= 0 else f"{rate}%",
        pitch=f"+{pitch}Hz" if pitch >= 0 else f"{pitch}Hz",
    )
    mp3 = b""
    async for chunk in tts.stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    return mp3


def to_wav16k(mp3: bytes, dst: Path) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3)
        tmp = f.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
             "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(dst)],
            check=True,
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    import wave
    with wave.open(str(dst)) as w:
        return w.getnframes() / w.getframerate()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evals/listen")
    args = ap.parse_args()
    root = Path(args.out)
    audio_dir = root / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 固定种子 shuffle → 文件名 tNN 不泄露配方
    jobs = JOBS[:]
    random.Random(42).shuffle(jobs)

    key = []
    for i, (label, voice, rate, pitch, text, truth) in enumerate(jobs, 1):
        fname = f"t{i:02d}"
        wav = audio_dir / f"{fname}.wav"
        if wav.exists():
            print(f"[{i}/{len(jobs)}] {fname} 已存在，跳过")
        else:
            mp3 = b""
            for attempt in range(3):
                try:
                    mp3 = await synth(voice, rate, pitch, text)
                    if mp3:
                        break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2)
            dur = to_wav16k(mp3, wav)
            print(f"[{i}/{len(jobs)}] {fname}.wav  {dur:.1f}s")
        key.append({
            "file": fname,
            "label": label,
            "voice": voice.split("-")[-1],
            "rate_percent": rate,
            "pitch_hz": pitch,
            "char_count": len(text),
            "truth_1to5": truth,
        })

    (root / "key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    with open(root / "ratings_ai.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "tension_1to5"])
        for k in key:
            w.writerow([k["file"], k["truth_1to5"]])
    print(f"\n完成 {len(key)} 条 → {audio_dir}")
    print(f"答案 key.json（盲听完别看）/ 构造真值 ratings_ai.csv → {root}")


if __name__ == "__main__":
    asyncio.run(main())
