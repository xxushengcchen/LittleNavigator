"""测试音频质检：用 SenseVoice 回读每条干净音频，揪出合成坏掉的。

判据：识别文本经短语匹配后得分 < 0.55 → 可疑。清单里的文件缺失、--fix 修复
失败、复检仍可疑都计入失败集合，退出码非 0；不再把遗漏当“通过”。

用法：
    python tools/qc_audio.py
    python tools/qc_audio.py --fix
    python tools/qc_audio.py --audio-dir models/test-audio --manifest .../manifest.jsonl
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

AUDIO_DIR = PROJ / "models" / "test-audio"
THRESHOLD = 0.55


def dur(path: pathlib.Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def check(asr, matcher, entries: list[dict], audio_dir: pathlib.Path | None = None):
    """回传 (可疑列表, 缺失文件列表)。

    旧实现遇到清单音频不存在直接 continue 却仍宣称检查了 N 条；缺失文件
    必须单独返回并计入失败，否则测试集缩小也会显示“全过”。
    """
    from tools.audio_io import read_wav  # 延迟导入，模块可被纯逻辑测试直接导入

    base = pathlib.Path(audio_dir) if audio_dir else AUDIO_DIR
    bad, missing = [], []
    for r in entries:
        f = base / r["file"]
        if not f.is_file():
            missing.append(r["file"])
            continue
        x, sr = read_wav(f)
        res = asr.transcribe(x, sr)
        ranked = matcher.rank(res.text)
        score = ranked[0][1] if ranked else 0.0
        if score < THRESHOLD:
            bad.append((r, res.text, score))
    return bad, missing


def outcome_exit(missing, bad, fix_requested, fix_failed, again) -> int:
    """统一退出码：缺文件、可疑、修复失败、复检仍可疑，任一为真即失败。"""
    if fix_requested:
        return 1 if (missing or bad or fix_failed or again) else 0
    return 1 if (missing or bad) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="删除并重新合成可疑音频")
    ap.add_argument("--audio-dir", type=pathlib.Path, default=AUDIO_DIR,
                    help="测试音频目录（默认 models/test-audio）")
    ap.add_argument("--manifest", type=pathlib.Path, default=None,
                    help="清单路径（默认 <audio-dir>/manifest.jsonl）")
    args = ap.parse_args()

    manifest_path = args.manifest or (args.audio_dir / "manifest.jsonl")
    manifest = [json.loads(l) for l
                in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 只查正例：反例句本来就该对不上短语库，低分是正确表现
    clean = [r for r in manifest if r["rate"] == 0 and not r["neg"]]

    from src.phrase_match import PhraseMatcher
    from tools.asr_models import load

    asr = load("sense-voice")
    asr.warmup()
    matcher = PhraseMatcher()
    bad, missing = check(asr, matcher, clean, args.audio_dir)
    print(f"检查 {len(clean)} 条干净音频：缺失 {len(missing)} 条，可疑 {len(bad)} 条")
    for name in missing:
        print(f"  [缺失] {name} —— 清单里的文件不存在，按失败处理")
    for r, text, score in bad:
        print(f"  [{score:.2f}] {r['file']}  时长 {dur(args.audio_dir / r['file']):.2f}s")
        print(f"        识别：{text!r}  期望：{r['phrase']!r}")

    if not bad and not missing:
        return 0
    if not args.fix:
        return 1

    key = os.environ.get("DOUBAO_API_KEY")
    if not key:
        print("缺少 DOUBAO_API_KEY，无法重合成", file=sys.stderr)
        return 2
    sys.path.insert(0, str(PROJ / "scripts"))
    from tts_doubao import mp3_to_wav, synthesize  # noqa: E402
    from tools.make_test_audio import VOICES  # noqa: E402

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="qc-"))
    fixed = []
    fix_failed = 0
    for r, _, _ in bad:
        f = args.audio_dir / r["file"]
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
            fix_failed += 1
            print(f"    ✗ 修复失败：{exc}")

    again, again_missing = check(asr, matcher, fixed, args.audio_dir)
    print(f"\n复检 {len(fixed)} 条，仍可疑 {len(again)} 条，修复失败 {fix_failed} 条，"
          f"缺失未处理 {len(missing)} 条")
    for r, text, score in again:
        print(f"  [{score:.2f}] {r['file']}  识别：{text!r}  期望：{r['phrase']!r}")
    # 审计复审：修复前的 bad 列表已经处理过，不能再计入退出码；
    # 只看“缺失未处理 / 修复失败 / 复检仍可疑”。
    return outcome_exit(missing, [], True, fix_failed, again or again_missing)


if __name__ == "__main__":
    raise SystemExit(main())
