"""探测豆包 API 下还有哪些可用音色（给测试集增加说话人多样性）。"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJ = ROOT.parent
sys.path.insert(0, str(PROJ / "scripts"))

from tts_doubao import TtsError, synthesize  # noqa: E402

PROBES = [
    # (speaker, resource_id)
    ("zh_female_wanwanxiaohe_moon_bigtts", "seed-tts-1.0"),
    ("zh_male_wennan_mars_bigtts", "seed-tts-1.0"),
    ("zh_female_cancan_mars_bigtts", "seed-tts-1.0"),
    ("zh_male_yangguangqingnian_mars_bigtts", "seed-tts-1.0"),
    ("zh_male_beijingxiaoye_uranus_bigtts", "seed-tts-2.0"),
    ("zh_female_mizai_uranus_bigtts", "seed-tts-2.0"),
    ("zh_male_ruyayichen_uranus_bigtts", "seed-tts-2.0"),
]


def main() -> int:
    key = os.environ.get("DOUBAO_API_KEY")
    if not key:
        print("缺少 DOUBAO_API_KEY", file=sys.stderr)
        return 2
    for speaker, resource in PROBES:
        try:
            audio = synthesize("油还够吗", api_key=key, speaker=speaker,
                               resource_id=resource, speech_rate=None)
            print(f"OK   {speaker:45s} {resource:14s} {len(audio)} bytes")
        except TtsError as exc:
            print(f"FAIL {speaker:45s} {resource:14s} {str(exc)[:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
