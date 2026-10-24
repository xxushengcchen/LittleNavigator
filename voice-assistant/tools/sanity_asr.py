"""模型自检：用官方测试音频确认三个模型都能加载、能出字。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.asr_models import REGISTRY, SAMPLE_RATE, load  # noqa: E402
from tools.audio_io import read_wav  # noqa: E402

MODELS_ROOT = Path(__file__).resolve().parents[2] / "models"


def main() -> int:
    cases = [
        ("sense-voice", MODELS_ROOT / "_sanity_zh.wav"),
        ("zipformer-ctc", MODELS_ROOT / "_sanity_zc.wav"),
        ("streaming-zip", MODELS_ROOT / "_sanity_zc.wav"),
    ]
    for name, wav in cases:
        samples, sr = read_wav(wav)
        asr = load(name)
        asr.warmup()
        r = asr.transcribe(samples, sr)
        print(f"[{name}] 载入 {asr.load_sec:.2f}s  音频 {r.audio_sec:.2f}s  "
              f"松键→出字 {r.decode_sec * 1000:.0f}ms  chunks={r.chunks}")
        print(f"    → {r.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
