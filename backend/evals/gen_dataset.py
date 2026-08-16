"""合成真值集生成器：edge-tts 多音色 × 多语速批量合成 + 真值标签 manifest。

用法（backend venv）：
    .venv/bin/python evals/gen_dataset.py [--out evals/dataset] [--limit N]

产物：
    evals/dataset/
      audio/           # 16kHz mono wav（ffmpeg 从 edge-tts mp3 转码）
      manifest.json    # 每条：id/voice/rate/text/char_count/notes + 真值标签

真值原理（第一性）：
  - 语速：edge-tts --rate=+X% 注入的播放速率已知 → 各条相对基准(rate=0)
    的字/秒 应≈ (1+X/100) 倍。绝对值不做断言（TTS 自身停顿不控），只断相对比。
  - 基频：音色固定 → 同音色所有条目的 f0 中位数应稳定（跨条变异 <8%）；
    八度减半/倍频错误通过绝对值区间暴露（男声 85-200Hz，女声 160-320Hz）。
  - jitter/能量：TTS 平稳发声 → detrended jitter 应处于低参考带（记录基线
    分布，作为"机器平稳"参考；真人紧张应显著高于该带）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import edge_tts

# ---- 矩阵设计 ----
# 4 音色（2 女 2 男，f0 区间分散）× 语速档（-20/-10/0/+10/+20%）+ 长句档
VOICES = [
    "zh-CN-XiaoxiaoNeural",  # 女 基准
    "zh-CN-XiaoyiNeural",    # 女 高音色
    "zh-CN-YunxiNeural",     # 男 基准
    "zh-CN-YunyangNeural",   # 男 低音色
]
RATES = [-20, -10, 0, 10, 20]  # percent

# 基准文本：含数字/长短句/常见口癖高危词，覆盖真实训练输入形态
BASE_TEXT = (
    "大家好，今天我想分享三点内容。第一，项目背景：我们在三月份启动了这个计划，"
    "目标是在两个月内完成上线。第二，嗯，核心进展：目前完成了大约百分之七十的里程碑，"
    "嗯，就是，重点功能已经全部通过验收。第三，下一步：我建议这个月底前完成灰度发布，"
    "大概需要三天的观察期。以上就是我的汇报，谢谢大家。"
)

# 长文本（长句/超长句考点：无标点长句 + 正常长句混排）
LONG_TEXT = (
    "我来详细说明一下这个方案的整体设计思路和背后的权衡过程。"
    "首先从用户侧看我们需要同时满足三个相互制约的目标分别是响应速度成本控制与数据一致性。"
    "在最初的版本里，我们尝试了同步写双库的方案；但是压测发现，"
    "在峰值每秒一万两千次请求的场景下，主库的写入延迟会从平均五毫秒恶化到四十毫秒以上。"
    "所以最终的方案是异步复制加对账兜底，把不一致窗口压缩到五秒以内。"
)

# 口癖密集文本（filler 检测考点：嗯/就是/然后/可能/大概/应该 高频出现）
FILLER_TEXT = (
    "嗯，这个，我觉得吧，可能就是，大概是这样的。"
    "然后呢，嗯，就是说，我们当时可能应该是，嗯，先做了调研。"
    "然后就是，那个，可能大概有，嗯，三十个人左右吧。"
    "我觉得应该就是，嗯，差不多这样，就是可能还差点意思。"
)


async def synth_one(voice: str, rate: int, text: str, text_key: str) -> tuple[bytes, str]:
    """合成一条，返回 (mp3_bytes, item_id)。"""
    rate_str = f"+{rate}%" if rate >= 0 else f"{rate}%"
    item_id = f"{voice.split('-')[-1].replace('Neural','')}_{text_key}_r{rate:+d}"
    tts = edge_tts.Communicate(text, voice, rate=rate_str)
    mp3 = b""
    async for chunk in tts.stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    return mp3, item_id


def to_wav_16k(mp3: bytes, wav_path: Path) -> float:
    """mp3 → 16kHz mono s16 wav，返回时长(秒)。"""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3)
        tmp = f.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
             "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", str(wav_path)],
            check=True,
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    import wave
    with wave.open(str(wav_path)) as w:
        return w.getnframes() / w.getframerate()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evals/dataset")
    args = ap.parse_args()
    out_dir = Path(args.out)
    audio_dir = out_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 组合矩阵：每音色 × (BASE×5档语速 + LONG×1档 + FILLER×1档)
    jobs: list[tuple[str, int, str, str]] = []
    for v in VOICES:
        for r in RATES:
            jobs.append((v, r, BASE_TEXT, "base"))
        jobs.append((v, 0, LONG_TEXT, "long"))
        jobs.append((v, 0, FILLER_TEXT, "filler"))
    print(f"计划合成 {len(jobs)} 条")

    # 幂等：旧 manifest 里已有的条目直接复用（只补缺/重试失败的）
    manifest_path = out_dir / "manifest.json"
    manifest: list[dict] = []
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    done_ids = {m["id"] for m in manifest}

    for i, (voice, rate, text, tkey) in enumerate(jobs, 1):
        rate_str = f"+{rate}%" if rate >= 0 else f"{rate}%"
        item_id_probe = f"{voice.split('-')[-1].replace('Neural','')}_{tkey}_r{rate:+d}"
        if item_id_probe in done_ids and (audio_dir / f"{item_id_probe}.wav").exists():
            continue  # 已有，跳过
        try:
            mp3, item_id = None, None
            for attempt in range(3):  # edge-tts 偶发 No audio，重试
                try:
                    mp3, item_id = await synth_one(voice, rate, text, tkey)
                    if mp3:
                        break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2)
            wav_path = audio_dir / f"{item_id}.wav"
            dur = to_wav_16k(mp3, wav_path)
            manifest.append({
                "id": item_id,
                "voice": voice,
                "voice_gender": "Female" if any(v in voice for v in ("Xiaoxiao", "Xiaoyi")) else "Male",
                "text_key": tkey,
                "rate_percent": rate,          # 语速真值（相对基准）
                "char_count": len(text),
                "duration_sec": round(dur, 2),
                "text": text,
            })
            done_ids.add(item_id)
            print(f"[{i}/{len(jobs)}] {item_id} {dur:.1f}s")
        except Exception as e:
            print(f"[{i}] {voice} r{rate} {tkey} 失败：{e}")

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n完成：manifest 共 {len(manifest)} 条 → {manifest_path}")


if __name__ == "__main__":
    asyncio.run(main())
