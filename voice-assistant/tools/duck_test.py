"""压低游戏音量（ducking）的自测：拿 python 进程自己当靶子。

步骤：python 开始循环放一段噪声 → 它的音频会话出现 → 对它做 duck(−12dB)
→ 读出会话音量确认真的降了 → restore 还原。不需要 iRacing 在跑。
"""
from __future__ import annotations

import pathlib
import sys
import time

import numpy as np
import sounddevice as sd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.duck import GameDucker  # noqa: E402


def main() -> int:
    tone = (0.05 * np.sin(2 * np.pi * 440 * np.arange(16000 * 6) / 16000)).astype(np.float32)
    sd.play(np.tile(tone, 6), 16000)
    time.sleep(1.0)

    ducker = GameDucker(processes=["python"], amount_db=-12.0)
    if not ducker.enabled:
        print("✗ pycaw 不可用")
        return 2
    before = [orig for _v, orig in ducker._find()]
    print(f"找到 python 音频会话 {len(before)} 个，音量 {[round(b, 3) for b in before]}")
    ducker.duck()
    mid = [orig for _v, orig in ducker._find()]
    print(f"duck 之后音量 {[round(m, 3) for m in mid]}（ducker.active={ducker.active}）")
    ducker.restore()
    after = [orig for _v, orig in ducker._find()]
    print(f"restore 之后音量 {[round(a, 3) for a in after]}")
    sd.stop()

    ok = bool(before) and len(mid) == len(before) and all(
        abs(m - b * 10 ** (-12 / 20)) < 0.05 for b, m in zip(before, mid, strict=False))
    print("✅ 通过" if ok else "✗ 不符合预期")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
