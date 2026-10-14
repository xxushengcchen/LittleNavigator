"""打包入口：双击 exe 走这里。

双击 = 原生窗口版（app.desktop），连真游戏；要模拟遥测：小小领航员.exe --sim
"""
from __future__ import annotations

import multiprocessing
import pathlib
import sys
import traceback

APP_NAME = "iRacing 小小领航员"


def _base_dir() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path(__file__).resolve().parent


def _prepare_streams() -> None:
    """窗口模式下 stdout/stderr 是 None：重定向到日志文件，别让 print 崩。

    同时把控制台切到 UTF-8（源码运行时中文 Windows 控制台是 GBK，
    打印 ✓ ✗ ⚠ 会抛 UnicodeEncodeError）。
    """
    if sys.stdout is not None and sys.stderr is not None:
        try:
            from src.config import fix_console_encoding

            fix_console_encoding()
        except Exception:  # noqa: BLE001
            pass
        return
    from src.config import data_dir
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    f = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


def _fatal(exc: BaseException) -> None:
    try:
        traceback.print_exc()
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            f"{APP_NAME} 启动失败：\n\n{type(exc).__name__}: {exc}\n\n"
            f"详细日志请查看用户数据目录的 logs/app.log。",
            APP_NAME, 0x10)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    multiprocessing.freeze_support()      # 打包后防重复拉起
    try:
        _prepare_streams()
        from app.desktop import main as desktop_main

        return desktop_main()
    except Exception as exc:  # noqa: BLE001
        _fatal(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
