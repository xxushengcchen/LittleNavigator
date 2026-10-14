"""ASR 三模型对比测试。

判据不是通用字错率，而是本产品的真实口径：
  · 正例（短语库里的问法）→ 识别文本经短语匹配后必须落回原意图
  · 反例（越界句）        → 必须被拒绝
  · 延迟                  → PTT 松键到出字的耗时

噪声条件：干净 / 20dB / 10dB（粉噪，模拟座舱底噪），
每条音频前加 0.15s 静音、后加 0.1s，模拟真实的按键录音。

用法：
    python tools/bench_asr.py                    # 跑全部三个模型
    python tools/bench_asr.py --models sense-voice
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJ = ROOT.parent
sys.path.insert(0, str(ROOT))

from src.phrase_match import PhraseMatcher          # noqa: E402
from tools.asr_models import REGISTRY, SAMPLE_RATE, load   # noqa: E402
from tools.audio_io import read_wav                 # noqa: E402

AUDIO_DIR = PROJ / "models" / "test-audio"
OUT_DIR = PROJ / "models" / "bench"
NOISE_CONDS = [("clean", None), ("20db", 20.0), ("10db", 10.0)]


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    spec = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    freqs = np.fft.rfftfreq(n)
    scale = 1.0 / np.sqrt(np.maximum(freqs, 1e-4))
    y = np.fft.irfft(spec * scale, n)
    return y / (np.std(y) + 1e-9)


def augment(x: np.ndarray, snr_db: float | None, seed: int) -> np.ndarray:
    pad = np.concatenate([np.zeros(int(0.15 * SAMPLE_RATE), dtype=np.float32),
                          x,
                          np.zeros(int(0.10 * SAMPLE_RATE), dtype=np.float32)])
    if snr_db is None:
        return pad
    rng = np.random.default_rng(seed)
    n = pink_noise(len(pad), rng)
    gain = (np.sqrt(np.mean(pad ** 2)) / (np.sqrt(np.mean(n ** 2)) + 1e-9)
            * (10 ** (-snr_db / 20.0)))
    return (pad + gain * n).astype(np.float32)


def eval_thresholds(records: list[dict], accept: float, margin: float) -> dict:
    """用已落盘的 rank 记录离线重算某个阈值组合下的命中/拒绝表现。"""
    pos = [r for r in records if not r["neg"]]
    neg = [r for r in records if r["neg"]]
    hit = refuse_ok = 0
    for r in pos:
        ranked = r["ranked"]
        if not ranked or ranked[0][1] < accept:
            continue
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < margin:
            continue
        if ranked[0][0] == r["intent"]:
            hit += 1
    for r in neg:
        ranked = r["ranked"]
        ok = (not ranked) or ranked[0][1] < accept
        if ranked and len(ranked) > 1:
            ok = ok or (ranked[0][1] - ranked[1][1] < margin)
        refuse_ok += int(ok)
    return {
        "accept": accept,
        "margin": margin,
        "hit": hit / max(len(pos), 1),
        "pass_on": len(pos) != 0,
        "refuse_neg": refuse_ok / max(len(neg), 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=",".join(REGISTRY))
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    args = ap.parse_args()

    manifest = [json.loads(line) for line
                in (AUDIO_DIR / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    if args.limit:
        manifest = manifest[: args.limit]
    print(f"测试音频 {len(manifest)} 条 × 噪声 {len(NOISE_CONDS)} 档")

    # 预读所有音频（含噪声增强各一版），三个模型复用，避免重复 IO/噪声计算
    cache: dict[tuple[str, str], np.ndarray] = {}
    for r in manifest:
        x, sr = read_wav(AUDIO_DIR / r["file"])
        assert sr == SAMPLE_RATE
        for cond, snr in NOISE_CONDS:
            seed = abs(hash(r["file"])) % (2**31) if cond != "clean" else 0
            cache[(r["file"], cond)] = augment(x, snr, seed)

    matcher = PhraseMatcher()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {}

    for name in [s.strip() for s in args.models.split(",") if s.strip()]:
        asr = load(name, num_threads=args.threads)
        asr.warmup()
        print(f"\n== {REGISTRY[name].label}  载入 {asr.load_sec:.2f}s")
        records = []
        t_start = time.perf_counter()
        for i, r in enumerate(manifest, 1):
            for cond, _ in NOISE_CONDS:
                audio = cache[(r["file"], cond)]
                t0 = time.perf_counter()
                res = asr.transcribe(audio, SAMPLE_RATE)
                total = time.perf_counter() - t0
                sr_rank = matcher.rank(res.text)
                records.append({
                    "file": r["file"], "phrase": r["phrase"], "intent": r["intent"],
                    "neg": r["neg"], "voice": r["voice"], "rate": r["rate"],
                    "noise": cond, "text": res.text,
                    "ranked": sr_rank[:4],
                    "decode_sec": res.decode_sec, "total_sec": total,
                    "audio_sec": res.audio_sec, "chunks": res.chunks,
                })
            if i % 50 == 0:
                print(f"   {i}/{len(manifest)}  {time.perf_counter() - t_start:.0f}s")

        rec_path = OUT_DIR / f"records-{name}.jsonl"
        with rec_path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 阈值扫描：优先「反例全拒绝」，再挑正例命中最高
        sweep = []
        for acc in (0.55, 0.60, 0.64, 0.66, 0.68, 0.72, 0.76, 0.80):
            for mg in (0.0, 0.03, 0.05, 0.08, 0.12):
                sweep.append(eval_thresholds(records, acc, mg))
        safe = [s for s in sweep if s["refuse_neg"] >= 0.99] or sweep
        best = max(safe, key=lambda s: (s["hit"], s["refuse_neg"]) )
        strict = max(sweep, key=lambda s: (s["refuse_neg"], s["hit"]))

        lat = [r["decode_sec"] for r in records]
        tot = [r["total_sec"] for r in records]
        aud = [r["audio_sec"] for r in records]
        neg_refused = sum(
            1 for r in records if r["neg"] and (
                not r["ranked"]
                or r["ranked"][0][1] < best["accept"]
                or (len(r["ranked"]) > 1 and r["ranked"][0][1] - r["ranked"][1][1] < best["margin"])
            )
        )
        neg_total = sum(1 for r in records if r["neg"])
        summary[name] = {
            "label": REGISTRY[name].label,
            "load_sec": asr.load_sec,
            "n_records": len(records),
            "best": best,
            "strict": strict,
            "latency": {
                "decode_median_ms": st.median(lat) * 1000,
                "decode_p95_ms": sorted(lat)[int(len(lat) * 0.95)] * 1000,
                "total_median_ms": st.median(tot) * 1000,
                "rtf_median": st.median([t / a for t, a in zip(tot, aud)]),
            },
            "neg_total": neg_total,
            "neg_refused": neg_refused,
        }
        print(f"   写入 {rec_path.name}")
        print(f"   最佳阈值 accept={best['accept']:.2f} margin={best['margin']:.2f} → "
              f"正例命中 {best['hit'] * 100:.1f}%，反例拒绝 {neg_refused}/{neg_total}")
        print(f"   松键→出字 中位 {summary[name]['latency']['decode_median_ms']:.0f}ms，"
              f"端到端 RTF 中位 {summary[name]['latency']['rtf_median']:.3f}")

    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n================ 汇总 ================")
    print(f"{'模型':28s} {'正例命中':>8s} {'反例拒绝':>8s} {'松键ms':>7s} {'RTF':>7s} {'载入s':>6s}")
    for name, s in summary.items():
        print(f"{s['label']:28s} {s['best']['hit'] * 100:7.1f}% "
              f"{s['neg_refused']}/{s['neg_total']:<6d} "
              f"{s['latency']['decode_median_ms']:7.0f} "
              f"{s['latency']['rtf_median']:7.3f} {s['load_sec']:6.2f}")
    print(f"\n明细：{OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
