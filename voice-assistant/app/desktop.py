"""原生窗口版入口：自己起服务 + WebView2 窗口，不开浏览器、不要命令行。

    python -m app.desktop            # 开发时这样跑
    python -m app.desktop --sim      # 模拟遥测
    python -m app.desktop --port 8788

窗口里跑的就是 app/static 那套界面，背后是本机的 API 服务（只监听 127.0.0.1）。
v1.5.1 起多开一个游戏内 Overlay 窗口：无边框、置顶、点击穿透、可调透明度，
用原生 Win32 扩展样式（WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
WS_EX_TOOLWINDOW）实现；Ctrl+Alt+O 全局热键临时显示/隐藏，不写配置。

关窗 = 退出：会顺手停掉 PTT 监听、关掉 HTTP 服务。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import socket
import sys
import threading
import time

WEBVIEW_TITLE = "iRacing 小小领航员"
OVERLAY_TITLE = "Overlay"
_app_mutex = None

# ---------------------------------------------------------------- Win32 常量
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
LWA_ALPHA = 0x00000002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SM_CXSCREEN = 0
SM_CYSCREEN = 1
# 全局热键轮询：Ctrl+Alt+O（F1–F12 被 iRacing 占用，不能用）
VK_CONTROL = 0x11
VK_MENU = 0x12           # Alt
VK_O = 0x4F
OVERLAY_HOTKEY_POLL_S = 0.06
# 关闭状态时把 Overlay 停在屏幕外：透明窗口在页面导航时会被 pywebview
# 主动 Show() 一次（透明窗口的已知行为），停在屏幕外可避免启动闪一下。
OVERLAY_OFFSCREEN = -20000

_win32_api_cache: dict | None = None


def _win32():
    if sys.platform != "win32":
        raise RuntimeError("原生窗口仅支持 Windows")
    import ctypes
    return ctypes


def _api() -> dict:
    """user32 函数原型。64 位下必须显式声明参数类型，否则 HWND 会被截断。"""
    global _win32_api_cache
    if _win32_api_cache is not None:
        return _win32_api_cache
    ctypes = _win32()
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    has_dpi = False
    try:
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = ctypes.c_uint
        user32.GetDpiForSystem.argtypes = []
        user32.GetDpiForSystem.restype = ctypes.c_uint
        has_dpi = True
    except AttributeError:
        pass
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                    ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    user32.GetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(wintypes.DWORD)]
    user32.GetLayeredWindowAttributes.restype = wintypes.BOOL
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int,
                                      ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.GetWindowRect.argtypes = [wintypes.HWND,
                                     ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    _win32_api_cache = {"ctypes": ctypes, "wintypes": wintypes,
                        "user32": user32, "has_dpi": has_dpi}
    return _win32_api_cache


def _system_dpi_scale() -> float:
    """系统 DPI 缩放。仅用于把 GetSystemMetrics 的读数换算回逻辑像素。"""
    try:
        api = _api()
        if not api["has_dpi"]:
            return 1.0
        scale = api["user32"].GetDpiForSystem() / 96.0
        return scale if scale > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def primary_screen_size() -> tuple[int, int] | None:
    """主屏逻辑尺寸（多屏时以主屏为准，记录为已知限制）。

    GetSystemMetrics 在 DPI 感知进程里返回物理像素，而 pywebview 的窗口
    x/y 用逻辑像素，两者必须换算；否则 125% 缩放下窗口会被二次放大、挪出屏幕。
    """
    if sys.platform != "win32":
        return None
    try:
        api = _api()
        metrics = api["user32"].GetSystemMetrics
        width, height = int(metrics(SM_CXSCREEN)), int(metrics(SM_CYSCREEN))
        scale = _system_dpi_scale()
        if scale > 0 and scale != 1.0:
            width, height = int(round(width / scale)), int(round(height / scale))
        return width, height
    except Exception:  # noqa: BLE001
        return None


def corner_position(settings: dict, screen_w: int, screen_h: int) -> tuple[int, int]:
    """按 corner + margin 计算窗口左上角（逻辑像素）。"""
    width, height = int(settings["width"]), int(settings["height"])
    margin = int(settings["margin"])
    corner = str(settings["corner"])
    x = margin if corner.endswith("left") else screen_w - width - margin
    y = margin if corner.startswith("top") else screen_h - height - margin
    return max(0, x), max(0, y)


# ====================================================================== Overlay
class OverlayController:
    """游戏内 Overlay 的窗口控制：原生样式、透明度、定位、显示/隐藏。

    只负责窗口层的事：页面内容由 /overlay 自己轮询 /api/overlay 更新。
    窗口对象是 pywebview 的 Window；窗口创建后 .native 是 WinForms Form，
    .native.Handle 是 .NET IntPtr，即 HWND。
    """

    def __init__(self, window, settings: dict, log=print):
        self.window = window
        self._settings = dict(settings)
        self._visible = False
        self._hwnd = None
        self._style_applied = False
        self._lock = threading.RLock()
        self._log = log

    # ---- 基本属性 -------------------------------------------------
    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def settings(self) -> dict:
        return dict(self._settings)

    # ---- 原生句柄 / DPI / 屏幕 ------------------------------------
    def hwnd(self):
        if self._hwnd:
            return self._hwnd
        native = getattr(self.window, "native", None)
        if native is None:
            return None
        handle = getattr(native, "Handle", None)
        if handle is None:
            return None
        try:
            # .NET IntPtr 不能直接 int()，必须先 ToInt64()
            if hasattr(handle, "ToInt64"):
                self._hwnd = int(handle.ToInt64())
            else:
                self._hwnd = int(handle)
        except (TypeError, ValueError, AttributeError, OverflowError):
            return None
        return self._hwnd

    def wait_for_native(self, timeout: float = 10.0) -> bool:
        """等 WebView2 窗口真正拿到原生句柄。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.hwnd():
                return True
            time.sleep(0.05)
        return False

    def _dpi_scale(self) -> float:
        hwnd = self.hwnd()
        if not hwnd:
            return 1.0
        try:
            api = _api()
            if not api["has_dpi"]:
                return 1.0
            scale = api["user32"].GetDpiForWindow(hwnd) / 96.0
            return scale if scale > 0 else 1.0
        except Exception:  # noqa: BLE001
            return 1.0

    def screen_metrics(self) -> tuple[int, int]:
        """主屏逻辑尺寸：物理像素 ÷ 当前窗口 DPI 缩放。"""
        api = _api()
        metrics = api["user32"].GetSystemMetrics
        width, height = int(metrics(SM_CXSCREEN)), int(metrics(SM_CYSCREEN))
        scale = self._dpi_scale()
        if scale > 0 and scale != 1.0:
            width, height = int(round(width / scale)), int(round(height / scale))
        return width, height

    def target_position(self, settings: dict | None = None) -> tuple[int, int]:
        return corner_position(settings or self._settings, *self.screen_metrics())

    # ---- 原生操作 -------------------------------------------------
    def _set_pos(self, x: int, y: int, width: int | None = None,
                 height: int | None = None) -> bool:
        hwnd = self.hwnd()
        if not hwnd:
            return False
        api = _api()
        scale = self._dpi_scale()
        flags = SWP_NOZORDER | SWP_NOACTIVATE
        if width is None or height is None:
            flags |= SWP_NOSIZE
            phys_w = phys_h = 0
        else:
            phys_w, phys_h = int(round(width * scale)), int(round(height * scale))
        return bool(api["user32"].SetWindowPos(
            hwnd, None, int(round(x * scale)), int(round(y * scale)),
            phys_w, phys_h, flags))

    def _apply_alpha(self) -> bool:
        hwnd = self.hwnd()
        if not hwnd:
            return False
        api = _api()
        return bool(api["user32"].SetLayeredWindowAttributes(
            hwnd, 0, self._alpha(), LWA_ALPHA))

    def _alpha(self) -> int:
        return max(0, min(255, int(round(float(self._settings["opacity"]) * 255))))

    def apply_native_style(self) -> dict:
        """点击穿透 + 不抢焦点 + 不占任务栏 + 分层透明度。"""
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        api = _api()
        user32 = api["user32"]
        style_before = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        wanted = (WS_EX_LAYERED | WS_EX_TRANSPARENT
                  | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        # 清掉 APPWINDOW：否则 TOOLWINDOW 和它同时存在时仍可能出现在任务栏。
        style_after = (style_before | wanted) & ~WS_EX_APPWINDOW
        if style_after != style_before:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, int(style_after))
        self._apply_alpha()
        self._style_applied = True
        return {
            "hwnd": hwnd,
            "style_before": style_before,
            "style_after": self.read_extended_style(),
            "expected_bits": wanted,
            "alpha": self._alpha(),
        }

    def read_extended_style(self) -> int:
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        return int(_api()["user32"].GetWindowLongW(hwnd, GWL_EXSTYLE))

    def read_layered_attributes(self) -> dict:
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        api = _api()
        ctypes = api["ctypes"]
        wintypes = api["wintypes"]
        color_key = wintypes.DWORD(0)
        alpha = ctypes.c_ubyte(0)
        flags = wintypes.DWORD(0)
        ok = api["user32"].GetLayeredWindowAttributes(
            hwnd, ctypes.byref(color_key), ctypes.byref(alpha),
            ctypes.byref(flags))
        return {"ok": bool(ok), "color_key": int(color_key.value),
                "alpha": int(alpha.value), "flags": int(flags.value)}

    def read_window_rect(self) -> dict:
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        api = _api()
        ctypes = api["ctypes"]
        wintypes = api["wintypes"]
        rect = wintypes.RECT()
        ok = api["user32"].GetWindowRect(hwnd, ctypes.byref(rect))
        return {"ok": bool(ok), "left": int(rect.left), "top": int(rect.top),
                "right": int(rect.right), "bottom": int(rect.bottom),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top)}

    # ---- 位置 / 尺寸 ----------------------------------------------
    def position(self) -> tuple[int, int]:
        x, y = self.target_position()
        self._set_pos(x, y)
        return x, y

    def park_offscreen(self) -> None:
        self._set_pos(OVERLAY_OFFSCREEN, OVERLAY_OFFSCREEN)

    def _resize_and_position(self) -> tuple[int, int]:
        x, y = self.target_position()
        self._set_pos(x, y, self._settings["width"], self._settings["height"])
        return x, y

    # ---- 显示 / 隐藏 ----------------------------------------------
    def _window_call(self, name: str, *args):
        method = getattr(self.window, name, None)
        if not callable(method):
            raise RuntimeError(f"当前窗口不支持 {name}()")
        return method(*args)

    def show(self) -> bool:
        with self._lock:
            self._resize_and_position()
            self._window_call("show")
            if self._style_applied:
                # WinForms 重新 Show() 可能把扩展样式改回去，重新压一遍。
                try:
                    self.apply_native_style()
                except Exception:  # noqa: BLE001
                    pass
            self._visible = True
            return True

    def hide(self) -> bool:
        with self._lock:
            try:
                self._window_call("hide")
            finally:
                self._visible = False
            return False

    def toggle(self) -> bool:
        with self._lock:
            return self.hide() if self._visible else self.show()

    # ---- 配置热更新 ------------------------------------------------
    def set_enabled(self, enabled: bool) -> bool:
        return self.show() if enabled else self.hide()

    def set_opacity(self, opacity: float) -> None:
        with self._lock:
            self._settings["opacity"] = float(opacity)
            if self._style_applied:
                self._apply_alpha()

    def set_corner(self, corner: str) -> None:
        with self._lock:
            self._settings["corner"] = str(corner)
            if self._visible:
                x, y = self.target_position()
                self._set_pos(x, y)

    def set_geometry(self, width: int, height: int,
                     margin: int | None = None) -> None:
        with self._lock:
            self._settings["width"] = int(width)
            self._settings["height"] = int(height)
            if margin is not None:
                self._settings["margin"] = int(margin)
            if self._visible:
                self._resize_and_position()

    def initial_state(self, visible: bool) -> bool:
        """GUI 起来后的初始状态：启用则显示，否则挪到屏幕外并隐藏。"""
        with self._lock:
            if visible:
                return self.show()
            self.park_offscreen()
            try:
                self._window_call("hide")
            except Exception:  # noqa: BLE001
                pass
            self._visible = False
            return False


# ------------------------------------------------------------------ 热键
def start_overlay_hotkey(controller: OverlayController,
                         stop_event: threading.Event, log=print):
    """后台线程轮询 Ctrl+Alt+O，切换 Overlay 显示/隐藏（不写配置）。"""
    if sys.platform != "win32":
        return None
    api = _api()
    user32 = api["user32"]

    def down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def loop() -> None:
        was_down = False
        while not stop_event.is_set():
            try:
                pressed = down(VK_CONTROL) and down(VK_MENU) and down(VK_O)
                if pressed and not was_down:
                    try:
                        visible = controller.toggle()
                        log(f"[overlay] 热键切换：{'显示' if visible else '隐藏'}")
                    except Exception as exc:  # noqa: BLE001
                        log(f"[overlay] 热键切换失败：{type(exc).__name__}: {exc}")
                was_down = pressed
            except Exception:  # noqa: BLE001
                was_down = False
            stop_event.wait(OVERLAY_HOTKEY_POLL_S)

    thread = threading.Thread(target=loop, daemon=True, name="overlay-hotkey")
    thread.start()
    return thread


# ------------------------------------------------------------- 冒烟测试
def overlay_smoke_facts(controller: OverlayController, window) -> dict:
    """创建窗口并应用样式后的机械断言（--overlay-smoke-test 用）。"""
    record: dict = {"ok": False, "checks": {}, "facts": {}, "limitations": []}
    try:
        applied = controller.apply_native_style()
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record
    style = controller.read_extended_style()
    layered = controller.read_layered_attributes()
    rect = controller.read_window_rect()
    scale = controller._dpi_scale()
    expected_x, expected_y = controller.target_position()
    tolerance = max(6, int(round(scale * 16)))
    title = None
    body_background = None
    try:
        title = window.evaluate_js("document.title")
        body_background = window.evaluate_js(
            "getComputedStyle(document.body).backgroundColor")
    except Exception as exc:  # noqa: BLE001
        record["limitations"].append(
            f"页面 JS 校验不可用：{type(exc).__name__}: {exc}")

    checks = {
        "ex_style_layered": bool(style & WS_EX_LAYERED),
        "ex_style_transparent": bool(style & WS_EX_TRANSPARENT),
        "ex_style_noactivate": bool(style & WS_EX_NOACTIVATE),
        "ex_style_toolwindow": bool(style & WS_EX_TOOLWINDOW),
        "layered_attributes_readable": bool(layered["ok"]),
        "alpha_matches_opacity": layered["alpha"] == applied["alpha"],
        "position_matches_corner": (
            abs(rect["left"] - int(round(expected_x * scale))) <= tolerance
            and abs(rect["top"] - int(round(expected_y * scale))) <= tolerance),
        "ex_style_appwindow_cleared": not bool(style & WS_EX_APPWINDOW),
        "renderer_loaded": title == "游戏内 Overlay",
        "page_background_transparent": body_background in ("rgba(0, 0, 0, 0)",
                                                           "transparent"),
    }
    record["checks"] = checks
    record["facts"] = {
        "hwnd": applied["hwnd"],
        "ex_style_before": f"0x{applied['style_before']:08X}",
        "ex_style_after": f"0x{style:08X}",
        "expected_bits": f"0x{applied['expected_bits']:08X}",
        "alpha_expected": applied["alpha"],
        "alpha_observed": layered["alpha"],
        "layered_flags": layered["flags"],
        "opacity": controller.settings["opacity"],
        "corner": controller.settings["corner"],
        "margin": controller.settings["margin"],
        "window_rect": rect,
        "expected_position_logical": [expected_x, expected_y],
        "dpi_scale": scale,
        "document_title": title,
        "body_background": body_background,
    }
    if scale != 1.0:
        record["limitations"].append(
            f"DPI 缩放 {scale:.2f}，窗口矩形按物理像素与逻辑位置换算比对")
    record["limitations"].append("多屏环境只按主屏（GetSystemMetrics）定位")
    record["ok"] = all(checks.values())
    return record


def _write_overlay_smoke(record: dict) -> str | None:
    try:
        from src.config import data_dir
        path = data_dir() / "logs" / "overlay-smoke.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return str(path)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ Overlay 冒烟结果写盘失败：{exc}")
        return None


# ------------------------------------------------------------------ 入口
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
    ap.add_argument("--sim-advance", action="store_true",
                    help="模拟遥测随时间推进圈数（配合 --sim，用于验证圈记录）")
    ap.add_argument("--no-listen", action="store_true", help="启动时不自动开 PTT 监听")
    ap.add_argument("--no-window", action="store_true",
                    help="不起窗口，只跑服务（调试用，等同 app.server）")
    ap.add_argument('--window-smoke-test', action='store_true', help='创建实际窗口验证 WebView2 启动并自动退出')
    ap.add_argument('--overlay-smoke-test', action='store_true',
                    help='创建主窗口 + 游戏内 Overlay，验证原生样式/透明度后自动退出')
    args = ap.parse_args(argv)
    if args.window_smoke_test or args.overlay_smoke_test:
        args.no_listen = True
    _guard_installed_instance()

    # 延迟 import：只在真正起窗口时才拉 webview（它拖 pythonnet）
    from .server import Handler, Server, Service, overlay_settings
    from src.config import fix_console_encoding
    fix_console_encoding()

    svc = Service(cfg_path=args.config, sim=args.sim, sim_advance=args.sim_advance)
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
        if getattr(exc, "winerror", None) != 10048 and exc.errno not in (98, 10048):
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

    override = overlay_settings(svc.cfg)
    overlay_enabled = bool(override["enabled"]) or args.overlay_smoke_test
    # 热键/PTT 冲突提示：默认 PTT 是右 Ctrl，和 Ctrl+Alt+O 的 Ctrl 同一物理键。
    ptt_vk = svc.cfg.get("ptt", {}).get("vk", 0xA3)
    try:
        if int(ptt_vk) in (0x11, 0x12, 0xA2, 0xA3):
            print("提示：Overlay 热键 Ctrl+Alt+O 与 PTT 键位（Ctrl）可能短暂冲突；"
                  "短按通常低于误触阈值，不会被当成提问。")
    except (TypeError, ValueError):
        pass

    window = None
    overlay = None
    overlay_controller = None
    stop_hotkey = threading.Event()
    smoke_holder: dict = {"result": None}
    smoke_path: dict = {"path": None}

    class WindowControls:
        def __init__(self):
            self._window = None

        def minimize(self):
            if self._window:
                self._window.minimize()

        def close(self):
            svc.quit_requested = True

    def watch() -> None:
        """等界面里的「退出程序」按钮；窗口被关掉时也会走到这里。"""
        deadline = time.monotonic() + 20 if args.window_smoke_test else None
        while not svc.quit_requested:
            if deadline is not None and time.monotonic() > deadline:
                svc.quit_requested = True
                break
            time.sleep(0.3)
        # 两个窗口都要关：只关一个时 pywebview 的 GUI 循环不会退出。
        for target in (overlay, window):
            if target is None:
                continue
            try:
                target.destroy()
            except Exception:  # noqa: BLE001
                pass

    def after_start() -> None:
        """GUI 循环起来后：应用 Overlay 原生样式、起热键、跑冒烟测试。"""
        if overlay_controller is not None and overlay is not None:
            try:
                if not overlay.events.shown.wait(15):
                    raise RuntimeError("Overlay 窗口 15 秒内没有显示")
                if not overlay_controller.wait_for_native(10):
                    raise RuntimeError("Overlay 窗口没有拿到原生句柄")
                applied = overlay_controller.apply_native_style()
                print(f"[overlay] 原生样式已应用：ex_style="
                      f"0x{applied['style_after']:08X} alpha={applied['alpha']}")
                overlay_controller.initial_state(overlay_enabled)
                print(f"[overlay] 初始状态：{'显示' if overlay_enabled else '隐藏'}")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠ Overlay 初始化失败：{type(exc).__name__}: {exc}")
            if sys.platform == "win32":
                try:
                    start_overlay_hotkey(overlay_controller, stop_hotkey, log=print)
                    print(f"[overlay] 热键 {override['toggle_key']} 已监听"
                          "（显示/隐藏，不写配置）")
                except Exception as exc:  # noqa: BLE001
                    print(f"⚠ Overlay 热键监听失败：{type(exc).__name__}: {exc}")
        if args.overlay_smoke_test:
            try:
                record: dict
                try:
                    if overlay is None or overlay_controller is None:
                        raise RuntimeError("Overlay 窗口未创建")
                    if not overlay.events.loaded.wait(15):
                        raise RuntimeError("Overlay 页面 15 秒内没有加载完成")
                    record = overlay_smoke_facts(overlay_controller, overlay)
                except Exception as exc:  # noqa: BLE001
                    record = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                smoke_holder["result"] = record
                print("=" * 60)
                print("  Overlay 冒烟测试")
                print("=" * 60)
                for name, passed in (record.get("checks") or {}).items():
                    print(f"  {'✓' if passed else '✗'} {name}")
                for name, value in (record.get("facts") or {}).items():
                    print(f"  {name}: {value}")
                if record.get("error"):
                    print(f"  错误：{record['error']}")
                for note in record.get("limitations") or []:
                    print(f"  限制：{note}")
                print(f"  结果：{'通过' if record.get('ok') else '失败'}")
                smoke_path["path"] = _write_overlay_smoke(record)
                if smoke_path["path"]:
                    print(f"  记录：{smoke_path['path']}")
            finally:
                svc.quit_requested = True
        watch()

    try:
        import webview
        screen = primary_screen_size()
        if screen is None:
            print("⚠ 读不到主屏尺寸，Overlay 交由窗口管理器定位")
            initial_x = initial_y = None
        elif overlay_enabled:
            initial_x, initial_y = corner_position(override, *screen)
        else:
            # 关闭时先停在屏幕外：透明窗口导航时会被 pywebview 主动 Show 一次。
            initial_x = initial_y = OVERLAY_OFFSCREEN
        controls = WindowControls()
        window = webview.create_window(WEBVIEW_TITLE, url,
            width=1180, height=820, min_size=(900, 650), resizable=True, frameless=True, easy_drag=False,
            shadow=False, js_api=controls, background_color="#FFFFFF",
            hidden=bool(args.overlay_smoke_test))
        controls._window = window
        overlay = webview.create_window(
            OVERLAY_TITLE, url + "overlay",
            width=int(override["width"]), height=int(override["height"]),
            x=initial_x, y=initial_y,
            resizable=False, frameless=True, on_top=True, transparent=True,
            shadow=False, easy_drag=False, focus=False, hidden=False,
            background_color="#000000")
        if overlay is not None:
            overlay_controller = OverlayController(overlay, override, log=print)
            svc.overlay_controller = overlay_controller
        else:
            print("⚠ Overlay 窗口创建返回空，游戏内 Overlay 不可用")
        if args.window_smoke_test:
            def loaded():
                from src.config import data_dir
                title = window.evaluate_js('document.title')
                result = data_dir() / 'logs' / 'window-smoke.json'
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(json.dumps({'renderer': 'edgechromium', 'title': title, 'loaded': True}), encoding='utf-8')
                svc.quit_requested = True
            window.events.loaded += loaded
        webview.start(after_start)
    finally:
        stop_hotkey.set()
        svc.overlay_controller = None
        svc.close()
        httpd.shutdown()
        httpd.server_close()
        print("已退出。")
    if args.overlay_smoke_test:
        result = smoke_holder["result"] or {"ok": False, "error": "没有产生冒烟结果"}
        return 0 if result.get("ok") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
