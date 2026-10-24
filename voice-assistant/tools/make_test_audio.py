"""用豆包 TTS 合成 ASR 对比测试音频。

来源：voice-assistant/intents_examples.yaml 的短语库 + 人工越界句。
产物：models/test-audio/*.wav（16k 单声道 s16）+ manifest.jsonl。

用法（PowerShell，密钥只走环境变量）：
    $env:DOUBAO_API_KEY = "<key>"
    python tools/make_test_audio.py --voices xiaohe2
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import pathlib
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]          # voice-assistant/
PROJ = ROOT.parent                                          # iRacing spotter/
sys.path.insert(0, str(PROJ / "scripts"))
sys.path.insert(0, str(ROOT))

from tts_doubao import TtsError, mp3_to_wav, synthesize  # noqa: E402

# 音色表：(speaker, resource_id, instruct)
# 三个音色都实测可用（2026-10-12 探测）：
#   xiaohe2  台湾腔女声，也就是语音包本人的声音
#   mizai    普通话女声
#   ruyayi   普通话男声（最接近用户本人）
VOICES: dict[str, tuple[str, str, str | None]] = {
    "xiaohe2": ("zh_female_xiaohe_uranus_bigtts", "seed-tts-2.0", None),
    "mizai": ("zh_female_mizai_uranus_bigtts", "seed-tts-2.0", None),
    "ruyayi": ("zh_male_ruyayichen_uranus_bigtts", "seed-tts-2.0", None),
}

# 明确的越界句：匹配层必须拒绝，绝不能落到任何意图
NEGATIVES = [
    "今天晚饭吃什么",
    "给我讲个笑话",
    "帮我查一下航班",
    "明天股票会涨吗",
    "你叫什么名字",
    "放首歌来听",
]


def load_phrases() -> list[tuple[str, str, bool]]:
    """→ [(意图, 短语, 是否越界)]"""
    data = yaml.safe_load((ROOT / "intents_examples.yaml").read_text(encoding="utf-8"))
    out: list[tuple[str, str, bool]] = []
    for intent, spec in data.items():
        examples = (spec or {}).get("examples") or []
        if intent == "unknown":
            continue
        # open_question 现在也应当被拒绝：本产品只回答遥测能直接答的问题
        neg = intent == "open_question"
        for p in examples:
            out.append((intent, str(p), neg))
    for p in NEGATIVES:
        out.append(("__negative__", p, True))
    return out


def safe_name(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(PROJ / "models" / "test-audio"))
    ap.add_argument("--voices", default="xiaohe2", help="逗号分隔，见 VOICES 表")
    ap.add_argument("--rush-every", type=int, default=3,
                    help="每 N 条额外合成一条加速版（0=不合成）")
    ap.add_argument("--rush-rate", type=int, default=30, help="加速版的 speech_rate")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    api_key = args.api_key or os.environ.get("DOUBAO_API_KEY")
    if not api_key:
        print("缺少 DOUBAO_API_KEY", file=sys.stderr)
        return 2

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted_voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    for v in wanted_voices:
        if v not in VOICES:
            print(f"未知音色 {v}，可选：{list(VOICES)}", file=sys.stderr)
            return 2

    phrases = load_phrases()
    jobs: list[tuple[str, str, bool, str, int]] = []   # 意图, 短语, neg, voice, rate
    for i, (intent, phrase, neg) in enumerate(phrases):
        for v in wanted_voices:
            jobs.append((intent, phrase, neg, v, 0))
            if args.rush_every and i % args.rush_every == 0:
                jobs.append((intent, phrase, neg, v, args.rush_rate))

    print(f"共 {len(phrases)} 条短语 → {len(jobs)} 个合成任务")
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="tts-"))
    manifest_path = out_dir / "manifest.jsonl"
    results: list[dict] = []
    fails: list[str] = []

    def run(job: tuple[str, str, bool, str, int]) -> dict | None:
        intent, phrase, neg, voice, rate = job
        speaker, resource, instruct = VOICES[voice]
        tag = "n" if neg else "p"
        name = f"{tag}__{safe_name(intent)}__{safe_name(phrase)[:24]}__{voice}__r{rate}.wav"
        dst = out_dir / name
        if dst.exists():
            return {"file": name, "phrase": phrase, "intent": intent, "neg": neg,
                    "voice": voice, "rate": rate, "ok": True, "cached": True}
        try:
            audio = synthesize(phrase, api_key=api_key, speaker=speaker,
                               resource_id=resource, instruct=instruct,
                               speech_rate=rate if rate else None)
            mp3 = tmpdir / (name + ".mp3")
            mp3.write_bytes(audio)
            mp3_to_wav(mp3, dst, 16000)
            mp3.unlink(missing_ok=True)
            return {"file": name, "phrase": phrase, "intent": intent, "neg": neg,
                    "voice": voice, "rate": rate, "ok": True}
        except (TtsError, OSError) as exc:
            fails.append(f"{phrase} [{voice} r{rate}] {exc}")
            return None

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for r in pool.map(run, jobs):
            if r:
                results.append(r)

    with manifest_path.open("w", encoding="utf-8") as f:
        for r in sorted(results, key=lambda x: x["file"]):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"成功 {len(results)} / {len(jobs)}，清单：{manifest_path}")
    if fails:
        print(f"失败 {len(fails)} 条，前 5 条：")
        for s in fails[:5]:
            print("  -", s)
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
