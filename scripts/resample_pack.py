#!/usr/bin/env python
"""把语音包目录里的 WAV 重采样到目标采样率，输出到新目录（不改动源）。

用途：保留 24 kHz 高规格母带，另派生一份 16 kHz 发行用包，让安装包更小。
重采样交给 ffmpeg（imageio-ffmpeg 自带，带限滤波），比线性插值干净。

用法：
    python scripts/resample_pack.py \
        --src build/pack/Mandarin-CNTW-Xusheng_Chen \
        --dst build/pack-16k/Mandarin-CNTW-Xusheng_Chen \
        --rate 16000

源目录里的非 WAV 文件（spmsg.txt 等）原样复制。跑完逐条校验：
采样率／位深／声道／非空，数量与源一致；不符即报错退出。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import pathlib
import shutil
import subprocess


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def convert(exe: str, src: pathlib.Path, dst: pathlib.Path, rate: int) -> None:
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{src.name} 重采样失败："
                           + proc.stderr.decode("utf-8", "replace")[:400])


def verify(dst: pathlib.Path, rate: int) -> None:
    import wave

    with wave.open(str(dst), "rb") as w:
        got = (w.getframerate(), w.getsampwidth() * 8, w.getnchannels())
        if got != (rate, 16, 1):
            raise ValueError(f"格式不符（应为 {rate}Hz/16bit/1ch，实为 {got}）：{dst}")
        if w.getnframes() == 0:
            raise ValueError(f"空音频：{dst}")
        if len(w.readframes(w.getnframes())) != w.getnframes() * 2:
            raise ValueError(f"内容截断：{dst}")


def wavs_in(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(f for f in root.iterdir() if f.is_file() and f.suffix.lower() == ".wav")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=pathlib.Path, required=True,
                    help="源语音包目录（含 *.WAV 与 spmsg.txt）")
    ap.add_argument("--dst", type=pathlib.Path, required=True, help="输出目录")
    ap.add_argument("--rate", type=int, default=16000, help="目标采样率，默认 16000")
    ap.add_argument("--jobs", type=int, default=4, help="ffmpeg 并发数")
    args = ap.parse_args()

    src, dst = args.src.resolve(), args.dst.resolve()
    if not src.is_dir():
        raise SystemExit(f"源目录不存在：{src}")
    if src == dst:
        raise SystemExit("源与目标相同；请指定不同的 --dst")
    if args.rate <= 0:
        raise SystemExit("--rate 必须为正整数")

    src_wavs = wavs_in(src)
    if not src_wavs:
        raise SystemExit(f"源目录里没有 WAV：{src}")
    dst.mkdir(parents=True, exist_ok=True)
    exe = ffmpeg_exe()
    before = sum(f.stat().st_size for f in src_wavs)

    done, errors = 0, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(convert, exe, w, dst / w.name, args.rate): w
                   for w in src_wavs}
        for fut in concurrent.futures.as_completed(futures):
            try:
                fut.result()
            except Exception as exc:            # 单条失败也收集，最后一起报
                errors.append(str(exc))
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(src_wavs)}", flush=True)
    if errors:
        raise SystemExit(f"{len(errors)} 条重采样失败，例如：\n  " + "\n  ".join(errors[:5]))

    for f in src.iterdir():                       # spmsg.txt 等非 WAV 原样复制
        if f.is_file() and f.suffix.lower() != ".wav":
            shutil.copy2(f, dst / f.name)

    dst_wavs = wavs_in(dst)
    if len(dst_wavs) != len(src_wavs):
        raise SystemExit(f"数量不一致：源 {len(src_wavs)} → 目标 {len(dst_wavs)}")
    bad = []
    for f in dst_wavs:
        try:
            verify(f, args.rate)
        except (ValueError, OSError) as exc:
            bad.append(str(exc))
    if bad:
        raise SystemExit(f"{len(bad)} 条校验未通过，例如：\n  " + "\n  ".join(bad[:5]))
    after = sum(f.stat().st_size for f in dst_wavs)

    print(f"源 {src}")
    print(f"目标 {dst}")
    print(f"完成：{len(dst_wavs)} 条 → {args.rate} Hz/16-bit/单声道，全部校验通过")
    print(f"体积 {before / 1024 / 1024:.1f} MB → {after / 1024 / 1024:.1f} MB"
          f"（省 {(1 - after / before) * 100:.0f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
