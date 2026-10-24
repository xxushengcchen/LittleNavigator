"""短语库体检：自检 + 跨意图碰撞扫描 + 越界句拒绝 + 鲁棒性估算。

短语库一大，最大的风险不是"认不出"，而是"两条意图互相抢命中"——
匹配层宁可拒绝也不猜，所以写重了的表现是「用户说了却被拒」，很隐蔽。
这个脚本把这类问题在加词的时候当场抓出来。

用法：
    python tools/check_library.py                # 全套
    python tools/check_library.py --collide 0.75 # 改碰撞告警阈值
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.phrase_match import _sim, normalize, PhraseMatcher  # noqa: E402

# 越界句：必须全部被拒
NEGATIVES = [
    "今天晚饭吃什么", "给我讲个笑话", "帮我查一下航班", "明天股票会涨吗",
    "你叫什么名字", "放首歌来听", "把空调开大点", "我饿了",
    "帮我订个酒店", "现在几点了", "给我读一下新闻",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collide", type=float, default=0.80,
                    help="跨意图相似度告警阈值（默认 0.80）")
    ap.add_argument("--library", default=None)
    args = ap.parse_args()

    matcher = PhraseMatcher(args.library) if args.library else PhraseMatcher()
    phrases = matcher.phrases
    by_intent: dict[str, list[str]] = {}
    for p in phrases:
        by_intent.setdefault(p.intent, []).append(p.text)

    print("=" * 66)
    print("短语库体检")
    print("=" * 66)
    total = len(phrases)
    print(f"总条目 {total} 条，{len(by_intent)} 个意图；"
          f"阈值 accept={matcher.accept} margin={matcher.margin}")
    for intent, items in sorted(by_intent.items(), key=lambda kv: -len(kv[1])):
        print(f"   {intent:16s} {len(items):3d} 条")

    # ---- 1. 跨意图重复（同一句话出现在两个意图里，必被拒） ----
    seen: dict[str, list[str]] = {}
    for p in phrases:
        seen.setdefault(p.norm, []).append(p.intent)
    dups = {k: v for k, v in seen.items() if len(set(v)) > 1}
    print(f"\n[1] 跨意图重复：{len(dups)} 条")
    for k, v in dups.items():
        print(f"    「{k}」同时出现在 {v}")

    # ---- 2. 自检：每条都要落回自己的意图 ----
    fails = []
    for p in phrases:
        h = matcher.match(p.text)
        if h.intent != p.intent:
            fails.append((p.intent, p.text, h.intent, round(h.score, 3), h.reason))
    print(f"\n[2] 自检（每条落回自己意图）：{total - len(fails)}/{total} 通过")
    for intent, text, got, score, reason in fails[:20]:
        print(f"    ✗ 「{text}」属于 {intent}，却判成 {got}（{score}）— {reason}")

    # ---- 3. 跨意图碰撞扫描：不同意图的两条被写得几乎一样 ----
    prepared = [(p.intent, p.text, p.chars, p.syls, p.pystr) for p in matcher._canonical_candidates]
    pairs = []
    for i in range(len(prepared)):
        ai, at, ac, asy, apy = prepared[i]
        for j in range(i + 1, len(prepared)):
            bi, bt, bc, bsy, bpy = prepared[j]
            if ai == bi:
                continue
            s = max(_sim(ac, bc), _sim(asy, bsy), _sim(apy, bpy))
            if s >= args.collide:
                pairs.append((s, at, ai, bt, bi))
    pairs.sort(reverse=True)
    print(f"\n[3] 跨意图高相似（≥{args.collide}）：{len(pairs)} 对")
    for s, at, ai, bt, bi in pairs[:20]:
        print(f"    {s:.2f}  「{at}」({ai})  ↔  「{bt}」({bi})")
    if pairs:
        print("    说明：原话仍 100% 落回自己意图（见 [2]），这里只是提示"
              "「听岔时可能串门」的理论风险；真要串了，margin 会先拒绝")

    # ---- 4. 越界句必须被拒 ----
    leaked = []
    for q in NEGATIVES:
        h = matcher.match(q)
        if h.intent:
            leaked.append((q, h.intent, round(h.score, 3)))
    print(f"\n[4] 越界句拒绝：{len(NEGATIVES) - len(leaked)}/{len(NEGATIVES)} 拒绝")
    for q, intent, score in leaked:
        print(f"    ✗ 「{q}」被收进 {intent}（{score}）")

    # ---- 5. 鲁棒性估算：砍头/砍尾/结巴，还认不认得出 ----
    stats: Counter = Counter()
    lost = []
    for p in phrases:
        mid = len(p.text) // 2
        variants = [p.text[1:], p.text[:-1],
                    p.text[:mid] + p.text[mid:mid + 1] + p.text[mid:]]
        for v in variants:
            if len(normalize(v)) < 3:
                continue
            stats["n"] += 1
            h = matcher.match(v)
            if h.intent == p.intent:
                stats["hit"] += 1
            elif h.intent is None:
                stats["refuse"] += 1
                lost.append((p.text, v, "拒绝"))
            else:
                stats["wrong"] += 1
                lost.append((p.text, v, h.intent))
    if stats["n"]:
        print(f"\n[5] 鲁棒性（砍头/砍尾/结巴 {stats['n']} 次）："
              f"仍命中 {stats['hit'] / stats['n'] * 100:.1f}%，"
              f"拒绝 {stats['refuse'] / stats['n'] * 100:.1f}%，"
              f"跑到别的意图 {stats['wrong'] / stats['n'] * 100:.1f}%")
        for orig, v, got in lost[:8]:
            print(f"    「{orig}」→ 变形「{v}」→ {got}")
        wrongs = [x for x in lost if x[2] != "拒绝"]
        if wrongs:
            print(f"    跑到别的意图的 {len(wrongs)} 例（要盯的就是这些）：")
            for orig, v, got in wrongs[:8]:
                print(f"      「{orig}」→ 变形「{v}」→ 判成 {got}")

    ok = not dups and not fails and not leaked
    print("\n" + ("✅ 通过（碰撞项只作提示）" if ok else "⚠ 有需要处理的问题（见上）"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
