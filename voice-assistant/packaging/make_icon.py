"""生成程序图标 icon.ico（蓝绿白，跟界面配色一致）。

画的是：圆角蓝底 + 白色无线电波 + 一点绿。
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

OUT = pathlib.Path(__file__).resolve().parent / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size: int) -> Image.Image:
    s = size * 4                                  # 先画大图再缩，边缘干净
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = int(s * 0.055)
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=int(s * 0.22),
                        fill=(26, 111, 224, 255))          # 蓝底
    # 信号弧（三条）
    cx, cy = s * 0.5, s * 0.78
    for i, r in enumerate((0.26, 0.38, 0.50)):
        rr = s * r
        width = int(s * 0.052)
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr], start=210, end=330,
              fill=(255, 255, 255, 255), width=width)
    # 发射点（绿）
    dot = s * 0.085
    d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=(14, 159, 110, 255))
    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    frames = [draw(n) for n in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(n, n) for n in SIZES])
    print("图标已生成：", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
