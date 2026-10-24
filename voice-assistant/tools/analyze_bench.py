"""对比测试的细分析：阈值扫描表、按音色/噪声拆分、误判样本清单。"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
BENCH = ROOT.parent / "models" / "bench"


def load(name: str) -> list[dict]:
    p = BENCH / f"records-{name}.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def decide(ranked: list[list], accept: float, margin: float) -> str | None:
    if not ranked or ranked[0][1] < accept:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < margin:
        return None
    return ranked[0][0]


def sweep(records: list[dict], accepts, margins) -> None:
    pos = [r for r in records if not r["neg"]]
    neg = [r for r in records if r["neg"]]
    print("   accept  margin  命中率   拒绝率(反例)  误放行")
    for accept in accepts:
        for margin in margins:
            hit = sum(1 for r in pos if decide(r["ranked"], accept, margin) == r["intent"])
            ref = sum(1 for r in neg if decide(r["ranked"], accept, margin) is None)
            wrong_intent = sum(1 for r in pos
                               if decide(r["ranked"], accept, margin) not in (None, r["intent"]))
            print(f"   {accept:5.2f}  {margin:5.2f}  {hit / len(pos) * 100:6.1f}%  "
                  f"{ref / len(neg) * 100:8.1f}%  {wrong_intent:8d}")


def breakdown(records: list[dict], accept: float, margin: float) -> None:
    by_voice: dict[str, Counter] = defaultdict(Counter)
    by_noise: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        d = decide(r["ranked"], accept, margin)
        if r["neg"]:
            c = by_noise[r["noise"]]
            c["neg_total"] += 1
            c["refuse"] += int(d is None)
            continue
        ok = d == r["intent"]
        by_voice[r["voice"]]["total"] += 1
        by_voice[r["voice"]]["hit"] += int(ok)
        c = by_noise[r["noise"]]
        c["pos_total"] += 1
        c["hit"] += int(ok)
    print("   按音色：")
    for v, c in sorted(by_voice.items()):
        print(f"     {v:10s} {c['hit']}/{c['total']}  {c['hit'] / c['total'] * 100:.1f}%")
    print("   按噪声（正例命中 / 反例拒绝）：")
    for n, c in sorted(by_noise.items()):
        print(f"     {n:10s} 正例 {c['hit']}/{c['pos_total']}  {c['hit'] / c['pos_total'] * 100:.1f}%   "
              f"反例 {c['refuse']}/{c['neg_total']}")


def misses(records: list[dict], accept: float, margin: float, limit: int = 25) -> None:
    print("   误判样本（期望 ≠ 判定）：")
    n = 0
    for r in records:
        if r["neg"]:
            continue
        d = decide(r["ranked"], accept, margin)
        if d == r["intent"]:
            continue
        top = r["ranked"][:2]
        print(f"     [{r['voice']}/{r['noise']}/r{r['rate']}] 「{r['phrase']}」"
              f" 期望 {r['intent']} → 判定 {d}")
        print(f"        识别：{r['text']!r}  候补：{[(x[0], round(x[1], 2)) for x in top]}")
        n += 1
        if n >= limit:
            break
    if n == 0:
        print("     （无）")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="sense-voice,zipformer-ctc,streaming-zip")
    args = ap.parse_args()
    accepts = (0.55, 0.60, 0.64, 0.66, 0.68, 0.72, 0.76)
    margins = (0.0, 0.03, 0.05, 0.08, 0.12)
    for name in [s.strip() for s in args.models.split(",") if s.strip()]:
        records = load(name)
        print(f"\n================ {name} ================")
        sweep(records, accepts, margins)
        print("\n   取 accept=0.66 / margin=0.05 的拆分：")
        breakdown(records, 0.66, 0.05)
        print()
        misses(records, 0.66, 0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
