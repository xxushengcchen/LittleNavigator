# -*- coding: utf-8 -*-
"""发行前窗口冒烟校验：退出码 0 不等于页面真的加载。

背景：app/desktop.py 的 --window-smoke-test 在 20 秒超时只设置 quit_requested，
window 模式仍 return 0，而且不会清理上次的成功记录；只检查退出码会把白屏判成
成功。本脚本跑一次真实冒烟，并在隔离的数据目录里检查“本次运行新写入”的
window-smoke.json：必须 loaded=true、title 非空、文件时间晚于开工时间，否则
非 0 退出。

用法：
    python scripts/verify_window_smoke.py --exe build/dist/小小领航员/小小领航员.exe
    python scripts/verify_window_smoke.py --exe <exe> --timeout 120

自测/CI 用（不需要真 exe）：
    python scripts/verify_window_smoke.py --self-test-script fake_exe.py
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

SMOKE_REL = pathlib.Path("logs") / "window-smoke.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", help="打包后的 小小领航员.exe 路径")
    ap.add_argument("--self-test-script",
                    help="测试用：用当前 Python 跑这个脚本代替 exe（模拟冒烟行为）")
    ap.add_argument("--timeout", type=float, default=120.0, help="冒烟最长等待秒数")
    args = ap.parse_args()

    if args.self_test_script:
        cmd = [sys.executable, args.self_test_script]
    elif args.exe:
        cmd = [args.exe, "--window-smoke-test"]
    else:
        print("需要 --exe 或 --self-test-script", file=sys.stderr)
        return 2

    data_dir = pathlib.Path(tempfile.mkdtemp(prefix="ln-window-smoke-"))
    env = os.environ.copy()
    env["LITTLE_NAVIGATOR_DATA_DIR"] = str(data_dir)  # 隔离旧的成功记录
    started = time.time()
    print(f">> 冒烟：{' '.join(cmd)}（数据目录 {data_dir}）", flush=True)
    try:
        proc = subprocess.run(cmd, env=env, timeout=args.timeout,
                              capture_output=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        print(f"FAIL: 冒烟 {args.timeout:.0f}s 内没有退出（页面很可能没加载）")
        return 1

    problems = []
    if proc.returncode != 0:
        problems.append(f"进程退出码 {proc.returncode}")
    smoke = data_dir / SMOKE_REL
    record = {}
    if not smoke.is_file():
        problems.append(f"没有生成窗口冒烟记录 {smoke}")
    else:
        try:
            record = json.loads(smoke.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"window-smoke.json 不是合法 JSON：{exc}")
            record = {}
        if record.get("loaded") is not True:
            problems.append(f"loaded 不是 true：{record.get('loaded')!r}")
        if not str(record.get("title") or "").strip():
            problems.append("窗口标题为空（页面可能未渲染）")
        if smoke.stat().st_mtime < started - 2:
            problems.append("window-smoke.json 是旧文件：本次运行没有更新它")
    if problems:
        print("FAIL: " + "；".join(problems))
        if proc.stdout:
            print("---- 子进程输出（最后 40 行）----")
            print("\n".join(proc.stdout.splitlines()[-40:]))
        return 1
    print(f"PASS: 窗口已加载（title={record.get('title')!r}，"
          f"renderer={record.get('renderer')!r}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
