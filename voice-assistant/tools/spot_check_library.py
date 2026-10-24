"""新词抽查：给短语库里的问法合成语音 → ASR 识别 → 匹配，验证端到端。

短语库体检（check_library.py）只测文本；这个脚本测的是完整链路：
豆包 TTS 念出来 → SenseVoice 识别 → 短语匹配，看能不能落回原意图。
扩库之后跑一遍，能发现"这条写法 ASR 根本听不出来"的问题。

用法（需要 DOUBAO_API_KEY）：
    python tools/spot_check_library.py --per-intent 2
    python tools/spot_check_library.py --per-intent 3 --voices ruyayi,mizai
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import random
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJ = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJ / "scripts"))

from src.phrase_match import PhraseMatcher                      # noqa: E402
from tools.asr_models import SAMPLE_RATE, load                  # noqa: E402
from tools.audio_io import read_wav                             # noqa: E402
from tools.make_test_audio import VOICES                        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-intent", type=int, default=2, help="每个意图抽几条")
    ap.add_argument("--voices", default="ruyayi")
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--model", default="sense-voice")
    args = ap.parse_args()

    key = os.environ.get("DOUBAO_API_KEY")
    if not key:
        print("缺少 DOUBAO_API_KEY", file=sys.stderr)
        return 2
    from tts_doubao import mp3_to_wav, synthesize

    rng = random.Random(args.seed)
    matcher = PhraseMatcher()
    by_intent: dict[str, list[str]] = {}
    for p in matcher.phrases:
        by_intent.setdefault(p.intent, []).append(p.text)
    picks = []
    for intent, items in sorted(by_intent.items()):
        for text in rng.sample(items, min(args.per_intent, len(items))):
            for v in [s.strip() for s in args.voices.split(",") if s.strip()]:
                picks.append((intent, text, v))
    print(f"抽查 {len(picks)} 条（{len(by_intent)} 个意图 × {args.per_intent} 条 × 音色）")

    out_dir = PROJ / "models" / "test-audio-new"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="spot-"))

    def synth(job):
        intent, text, voice = job
        speaker, resource, instruct = VOICES[voice]
        dst = out_dir / f"{intent}__{text}__{voice}.wav"
        if not dst.exists():
            audio = synthesize(text, api_key=key, speaker=speaker,
                               resource_id=resource, instruct=instruct)
            mp3 = tmp / (dst.name + ".mp3")
            mp3.write_bytes(audio)
            mp3_to_wav(mp3, dst, SAMPLE_RATE)
            mp3.unlink(missing_ok=True)
        return job, dst

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = list(pool.map(synth, picks))

    asr = load(args.model)
    asr.warmup()
    rows = []
    for (intent, text, voice), path in jobs:
        x, sr = read_wav(path)
        res = asr.transcribe(x, sr)
        h = matcher.match(res.text)
        rows.append({"intent": intent, "want": text, "voice": voice,
                     "heard": res.text, "got": h.intent, "score": round(h.score, 3),
                     "reason": h.reason})

    hit = sum(1 for r in rows if r["got"] == r["intent"])
    refused = sum(1 for r in rows if r["got"] is None)
    wrong = len(rows) - hit - refused
    print(f"\n结果：命中 {hit}/{len(rows)} = {hit / len(rows) * 100:.1f}%  "
          f"拒绝 {refused}  串到别的意图 {wrong}\n")
    for r in rows:
        mark = "✓" if r["got"] == r["intent"] else ("·" if r["got"] is None else "✗")
        print(f"  {mark} [{r['voice']}] 「{r['want']}」→ 识别「{r['heard']}」→ {r['got'] or 'REFUSE'}")
    (out_dir / "report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n音频与报告：{out_dir}")
    return 0 if wrong == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
