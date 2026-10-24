"""PTT 监听线程的自动化自检：不用人按键，用合成按键事件驱动。

验证三件事：
  1. 按住 → 松开，能产出一条转写结果（音频时长对得上按住的时长）
  2. 短于 min_hold_ms 的轻点是误触，被丢弃
  3. 播报忙（is_busy=True）时按键被忽略

ASR 用桩：直接报「录制了 N 秒」，不加载真实模型。
"""
from __future__ import annotations

import ctypes
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.listener import PttListener  # noqa: E402

VK = 0x7C          # F13：真实键盘上一般没有，避免干扰
KEYEVENTF_KEYUP = 0x0002


def press() -> None:
    ctypes.windll.user32.keybd_event(VK, 0, 0, 0)


def release() -> None:
    ctypes.windll.user32.keybd_event(VK, 0, KEYEVENTF_KEYUP, 0)


class StubAsr:
    key = "stub"

    def transcribe(self, samples, sr=16000):
        class R:
            text = f"录制{len(samples) / sr:.2f}秒"
            decode_sec = 0.001
            audio_sec = len(samples) / sr
            chunks = 1
        return R()


def main() -> int:
    results: list[str] = []
    cfg = {"ptt": {"vk": VK, "min_hold_ms": 250, "input_device": 1, "poll_ms": 10}}
    busy = {"v": False}
    listener = PttListener(StubAsr(), cfg, lambda text, meta: results.append(text),
                           is_busy=lambda: busy["v"])
    listener.start()
    time.sleep(0.3)

    print("① 按住 0.8 秒再松开 …")
    press()
    time.sleep(0.8)
    release()
    time.sleep(1.2)

    print("② 轻点 0.1 秒（误触）…")
    press()
    time.sleep(0.1)
    release()
    time.sleep(0.8)

    print("③ 播报中按住 0.8 秒（应被忽略）…")
    busy["v"] = True
    press()
    time.sleep(0.8)
    release()
    time.sleep(0.8)
    busy["v"] = False

    listener.stop()
    print(f"\n结果 {len(results)} 条：{results}")
    print(f"统计：{listener.stats}")
    ok = (len(results) == 1 and results[0].startswith("录制0.6")
          and listener.stats["too_short"] == 1
          and listener.stats["ignored_busy"] == 1)
    print("✅ 通过" if ok else "✗ 不符合预期")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
