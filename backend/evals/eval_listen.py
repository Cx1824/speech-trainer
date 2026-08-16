"""盲听一致性评测：手机音频（外放翻录/手机直录均可）→ 管线紧张度 vs 你的听感。

用法：
  第一步（打分）：
    .venv/bin/python evals/eval_listen.py --dir evals/listen/audio
  第二步（盲听打分后对比）：
    1. 打开 evals/listen/ratings_template.csv，盲听 audio/ 下同名文件，
       按你的听感填 tension_1to5（1=非常放松 … 5=非常紧张），存为 ratings.csv
    2. .venv/bin/python evals/eval_listen.py --dir evals/listen --ratings

输出：
  - 每条音频的紧张度分 + 四信号明细（jitter/停顿/能量）+ 排序
  - 有 ratings 时：算法排序 vs 听感排序的一致性
    （Kendall tau + 完全一致对比例；tau≥0.6 方向感成立）

口径说明：
  - 未校准（人群默认基线）：手机外放会压缩 jitter 差距，绝对分值偏低属预期，
    本实验只看【相对排序】是否与人耳一致
  - 语速信号缺 ASR 文字，不参与（分母需要字数），仅声学三信号
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.analysis.pcm_features import PcmFeatureBuffer
from app.modules.analysis.voice_features import compute_tension_v2

SUPPORTED = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def to_wav16k(src: Path) -> bytes:
    """任意音频 → 16k mono s16 wav bytes（ffmpeg）。"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", tmp],
            check=True,
        )
        with open(tmp, "rb") as r:
            return r.read()
    finally:
        Path(tmp).unlink(missing_ok=True)


def wav_bytes_to_pcm(wav_bytes: bytes) -> bytes:
    """wav bytes → 原始 PCM（读 header）。"""
    import io
    with wave.open(io.BytesIO(wav_bytes)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def score_file(src: Path) -> dict | None:
    """单文件全管线评分。"""
    try:
        wav = to_wav16k(src)
        pcm = wav_bytes_to_pcm(wav)
    except Exception as e:
        return {"id": src.stem, "error": f"解码失败：{e}"}
    buf = PcmFeatureBuffer()
    # 分块喂（与线上一致）
    for i in range(0, len(pcm), 6400):
        buf.push(pcm[i:i + 6400])
    feats = buf.flush()
    if feats.duration_sec < 2.0:
        return {"id": src.stem, "error": f"音频太短（{feats.duration_sec:.1f}s <2s）"}
    score, detail = compute_tension_v2(feats, baseline=None)  # 人群默认基线
    return {
        "id": src.stem,
        "sec": round(feats.duration_sec, 1),
        "tension": round(score, 1),
        "jitter": round(feats.pitch_jitter, 4),
        "f0": round(feats.pitch_mean, 1) if feats.pitch_mean else 0,
        "pauses": feats.pause_count,
        "detail": detail,
    }


def kendall_tau(a: list[float], b: list[float]) -> float:
    """Kendall tau（-1..1）：排序一致性。无 scipy 依赖手算。"""
    n = len(a)
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            sa = (a[i] > a[j]) - (a[i] < a[j])
            sb = (b[i] > b[j]) - (b[i] < b[j])
            if sa * sb > 0:
                conc += 1
            elif sa * sb < 0:
                disc += 1
    total = n * (n - 1) / 2
    return (conc - disc) / total if total else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="evals/listen", help="含 audio/ 子目录")
    ap.add_argument("--ratings", action="store_true", help="对比盲听分（ratings.csv）")
    args = ap.parse_args()
    root = Path(args.dir)
    audio_dir = root / "audio"
    if not audio_dir.exists():
        audio_dir.mkdir(parents=True)
        print(f"已创建 {audio_dir}，把手机音频放进去再跑（支持 {'/'.join(sorted(SUPPORTED))}）")
        return

    files = sorted(p for p in audio_dir.iterdir() if p.suffix.lower() in SUPPORTED)
    if not files:
        print(f"{audio_dir} 下没有音频（支持 {'/'.join(sorted(SUPPORTED))}）")
        return
    print(f"评分 {len(files)} 条…")
    rows = [r for r in (score_file(f) for f in files) if r]
    errs = [r for r in rows if "error" in r]
    rows = [r for r in rows if "error" not in r]
    rows.sort(key=lambda r: -r["tension"])

    print(f"\n{'文件':<32} {'紧张度':>6} {'jitter':>7} {'f0':>6} {'停顿':>4} {'秒':>6}")
    for r in rows:
        print(f"{r['id']:<32} {r['tension']:>6} {r['jitter']:>7} {r['f0']:>6} {r['pauses']:>4} {r['sec']:>6}")
    for e in errs:
        print(f"⚠️  {e['id']}: {e['error']}")

    # 盲听模板
    tpl = root / "ratings_template.csv"
    with open(tpl, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "tension_1to5"])
        for r in rows:
            w.writerow([r["id"], ""])
    print(f"\n盲听模板 → {tpl}（填 1-5 分后另存 ratings.csv，加 --ratings 重跑对比）")

    # 对比模式
    if args.ratings:
        rp = root / "ratings.csv"
        if not rp.exists():
            print(f"未找到 {rp}")
            return
        human: dict[str, float] = {}
        with open(rp, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("tension_1to5", "").strip():
                    human[row["id"]] = float(row["tension_1to5"])
        pairs = [(r["tension"], human[r["id"]]) for r in rows if r["id"] in human]
        if len(pairs) < 3:
            print(f"有效配对仅 {len(pairs)} 条（<3），无法算一致性")
            return
        algo = [p[0] for p in pairs]
        hum = [p[1] for p in pairs]
        tau = kendall_tau(algo, hum)
        n = len(pairs)
        conc_pairs = sum(1 for i in range(n) for j in range(i + 1, n)
                         if ((algo[i] > algo[j]) - (algo[i] < algo[j]))
                         * ((hum[i] > hum[j]) - (hum[i] < hum[j])) > 0)
        total_pairs = n * (n - 1) / 2
        verdict = "✅ 方向感成立（tau≥0.6）" if tau >= 0.6 else (
            "⚠️ 弱一致（0.3≤tau<0.6），样本少或外放压缩差距" if tau >= 0.3 else "❌ 排序不一致，先修管线")
        print(f"\n== 盲听一致性（n={n}） ==")
        print(f"Kendall tau = {tau:.3f}   一致对 {conc_pairs:.0f}/{total_pairs:.0f}")
        print(verdict)


if __name__ == "__main__":
    main()
