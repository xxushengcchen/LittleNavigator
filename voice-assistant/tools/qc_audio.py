"""测试音频质检：用 SenseVoice 回读每条干净音频，揪出合成坏掉的。

判据：识别文本经短语匹配后得分 < 0.55 → 可疑（要么 TTS 合成坏了，要么
短语本身不是人话）。--fix 会删掉可疑文件并重新合成，然后复检一次。

用法：
    python tools/qc_audio.py
    python tools/qc_audio.py --fix
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import wave

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJ = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJ / "scripts"))

from src.phrase_match import PhraseMatcher              # noqa: E402
from tools.asr_models import SAMPLE_RATE, load          # noqa: E402
from tools.audio_io import read_wav                     # noqa: E402
from tools.make_test_audio import VOICES                # noqa: E402

AUDIO_DIR = PROJ / "models" / "test-audio"
THRESHOLD = 0.55


def dur(path: pathlib.Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def check(asr, matcher, entries: list[dict]) -> list[tuple[dict, str, float]]:
    bad = []
    for r in entries:
        f = AUDIO_DIR / r["file"]
        if not f.exists():
            continue
        x, sr = read_wav(f)
        res = asr.transcribe(x, sr)
        ranked = matcher.rank(res.text)
        score = ranked[0][1] if ranked else 0.0
        if score < THRESHOLD:
            bad.append((r, res.text, score))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="删除并重新合成可疑音频")
    args = ap.parse_args()

    manifest = [json.loads(l) for l
                in (AUDIO_DIR / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
    # 只查正例：反例句本来就该对不上短语库，低分是正确表现
    clean = [r for r in manifest if r["rate"] == 0 and not r["neg"]]
    asr = load("sense-voice")
    asr.warmup()
    matcher = PhraseMatcher()
    bad = check(asr, matcher, clean)
    print(f"检查 {len(clean)} 条干净音频，可疑 {len(bad)} 条")
    for r, text, score in bad:
        print(f"  [{score:.2f}] {r['file']}  时长 {dur(AUDIO_DIR / r['file']):.2f}s")
        print(f"        识别：{text!r}  期望：{r['phrase']!r}")

    if not bad or not args.fix:
        return 0 if not bad else 1

    key = os.environ.get("DOUBAO_API_KEY")
    if not key:
        print("缺少 DOUBAO_API_KEY，无法重合成", file=sys.stderr)
        return 2
    sys.path.insert(0, str(PROJ / "scripts"))
    from tts_doubao import mp3_to_wav, synthesize  # noqa: E402

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="qc-"))
    fixed: list[dict] = []
    for r, _, _ in bad:
        f = AUDIO_DIR / r["file"]
        speaker, resource, instruct = VOICES[r["voice"]]
        print(f"  重新合成 {r['file']} …")
        try:
            audio = synthesize(r["phrase"], api_key=key, speaker=speaker,
                               resource_id=resource, instruct=instruct,
                               speech_rate=r["rate"] or None)
            mp3 = tmp / (r["file"] + ".mp3")
            mp3.write_bytes(audio)
            mp3_to_wav(mp3, f, 16000)
            mp3.unlink(missing_ok=True)
            fixed.append(r)
        except Exception as exc:  # noqa: BLE001
            print(f"    ✗ {exc}")

    again = check(asr, matcher, fixed)
    print(f"\n复检 {len(fixed)} 条，仍可疑 {len(again)} 条")
    for r, text, score in again:
        print(f"  [{score:.2f}] {r['file']}  识别：{text!r}  期望：{r['phrase']!r}")
    return 0 if not again else 1


if __name__ == "__main__":
    raise SystemExit(main())
