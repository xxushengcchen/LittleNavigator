"""原生窗口版入口：自己起服务 + WebView2 窗口，不开浏览器、不要命令行。

    python -m app.desktop            # 开发时这样跑
    python -m app.desktop --sim      # 模拟遥测
    python -m app.desktop --port 8788

窗口里跑的就是 app/static 那套界面，背后是本机的 API 服务（只监听 127.0.0.1）。
关窗 = 退出：会顺手停掉 PTT 监听、关掉 HTTP 服务。
"""
from __future__ import annotations

import argparse
import pathlib
import socket
import sys
import threading
import time

WEBVIEW_TITLE = "iRacing 小小领航员"
_app_mutex = None


def _guard_installed_instance():
    """安装/卸载与应用共享运行标记，更新前要求用户关闭程序。"""
    from src.config import installed_mode
    if not installed_mode() or sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    global _app_mutex
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    _app_mutex = kernel.CreateMutexW(None, False, 'Local\\LittleNavigator')
    if not _app_mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        raise RuntimeError('小小领航员已经运行，请使用已有窗口。')


def _free_port(preferred: int) -> int:
    """优先用固定端口；被占用就让系统随便给一个（浏览器里还能自己开）。"""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--config", default=None)
    ap.add_argument("--sim", action="store_true", help="强制模拟遥测")
    ap.add_argument("--no-listen", action="store_true", help="启动时不自动开 PTT 监听")
    ap.add_argument("--no-window", action="store_true",
                    help="不起窗口，只跑服务（调试用，等同 app.server）")
    ap.add_argument('--window-smoke-test', action='store_true', help='创建实际窗口验证 WebView2 启动并自动退出')
    args = ap.parse_args(argv)
    if args.window_smoke_test:
        args.no_listen = True
    _guard_installed_instance()

    # 延迟 import：只在真正起窗口时才拉 webview（它拖 pythonnet）
    from .server import Handler, Server, Service
    from src.config import fix_console_encoding
    fix_console_encoding()

    svc = Service(cfg_path=args.config, sim=args.sim)
    svc.desktop_mode = True
    print("=" * 60)
    print(f"  {WEBVIEW_TITLE}")
    print("=" * 60)
    for line in svc.asst.info_lines():
        print(line)
    if svc.asst.tel_warn:
        print(f"⚠ {svc.asst.tel_warn}")
    print(f"播放后端 = {svc.asst.player.backend}")

    Handler.service = svc
    try:
        httpd = Server(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        if getattr(exc,"winerror",None) != 10048 and exc.errno not in (98,10048):
            svc.close()
            raise
        httpd = Server(("127.0.0.1", 0), Handler)
    port = httpd.server_port
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    url = f"http://127.0.0.1:{port}/"
    print(f"服务：{url}")

    if not args.no_listen:
        r = svc.start_listener()
        print(f"PTT 监听：{'已启动' if r.get('running') else '未启动（' + str(r.get('error')) + '）'}")

    if args.no_window:
        print("（--no-window：只有服务，Ctrl+C 退出）")
        try:
            while not svc.quit_requested:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            svc.close()
            httpd.shutdown()
            httpd.server_close()
        return 0

    window = None
    class WindowControls:
        def __init__(self):self._window=None
        def minimize(self):
            if self._window:self._window.minimize()
        def close(self):
            svc.quit_requested=True

    def watch() -> None:
        """等界面里的「退出程序」按钮；窗口被关掉时也会走到这里。"""
        deadline = time.monotonic() + 20 if args.window_smoke_test else None
        while not svc.quit_requested:
            if deadline is not None and time.monotonic() > deadline:
                svc.quit_requested = True
                break
            time.sleep(0.3)
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass

    try:
        import webview
        controls=WindowControls()
        window = webview.create_window(WEBVIEW_TITLE, url,
            width=1080, height=780, resizable=False, frameless=True, easy_drag=False,
            shadow=False, js_api=controls, background_color="#FFFFFF")
        controls._window=window
        if args.window_smoke_test:
            def loaded():
                import json
                from src.config import data_dir
                title = window.evaluate_js('document.title')
                result = data_dir() / 'logs' / 'window-smoke.json'
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(json.dumps({'renderer': 'edgechromium', 'title': title, 'loaded': True}), encoding='utf-8')
                svc.quit_requested = True
            window.events.loaded += loaded
        webview.start(watch)
    finally:
        svc.close()
        httpd.shutdown()
        httpd.server_close()
        print("已退出。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
