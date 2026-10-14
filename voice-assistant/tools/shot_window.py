"""给原生窗口截图（开发验证用）。

    python tools/shot_window.py "iRacing 小小领航员" out.png

先按标题找到窗口、把它提到前台，再截取那块屏幕区域。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

user32 = ctypes.windll.user32


def find_window(title: str) -> int | None:
    found: list[int] = []

    def cb(hwnd, _):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title in buf.value:
            found.append(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return found[0] if found else None


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "iRacing 小小领航员"
    out = sys.argv[2] if len(sys.argv) > 2 else "window.png"
    hwnd = find_window(title)
    if not hwnd:
        print(f"找不到窗口：{title}")
        return 1
    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    img.save(out)
    print(f"已截图 {img.size} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
