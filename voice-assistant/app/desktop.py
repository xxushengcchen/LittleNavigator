"""原生窗口版入口：自己起服务 + WebView2 窗口，不开浏览器、不要命令行。

    python -m app.desktop            # 开发时这样跑
    python -m app.desktop --sim      # 模拟遥测
    python -m app.desktop --port 8788

窗口里跑的就是 app/static 那套界面，背后是本机的 API 服务（只监听 127.0.0.1）。
v1.5.1 起多开一个游戏内 Overlay 窗口：无边框、置顶、点击穿透、可调透明度，
用原生 Win32 扩展样式（WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE |
WS_EX_TOOLWINDOW）实现；Ctrl+Alt+O 全局热键临时显示/隐藏，不写配置。

v1.5.2 起 Overlay 可自由摆放：
- overlay.x / overlay.y 给绝对坐标；为 null 时仍按 corner + margin 排；
- Ctrl+Alt+L 切换「布局模式」：临时去掉 WS_EX_TRANSPARENT（不再点击穿透），
  页面显示虚线窗口框、拖动区与右下缩放手柄，拖动/缩放通过
  POST /api/overlay/layout 落到 controller.set_bounds()，退出后恢复穿透；
- 卡片改为贴合内容（fit-content），窗口其余区域全透明。

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
LWA_COLORKEY = 0x00000001
# 色键透明：页面把整窗铺成 #010203，系统把该颜色抠成完全透明。
# pywebview 的 WebView2 透明在个别 Windows/DPI 组合下仍会露白底，
# 色键是最后一道保险（文字抗锯齿会留下深色描边，反而利于游戏内辨认）。
OVERLAY_COLOR_KEY = 0x00030201  # COLORREF: R=1,G=2,B=3 (#010203)
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
HTBOTTOMRIGHT = 17
WS_THICKFRAME = 0x00040000
GWL_STYLE = -16
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SM_CXSCREEN = 0
SM_CYSCREEN = 1
# 全局热键轮询：Ctrl+Alt+O（F1–F12 被 iRacing 占用，不能用）
VK_CONTROL = 0x11
VK_MENU = 0x12           # Alt
VK_O = 0x4F
VK_L = 0x4C
OVERLAY_HOTKEY_POLL_S = 0.06
# 运行时几何钳制：与 app/static/overlay.js 的 MIN/MAX 保持一致，
# 也避免拖动/缩放把窗口推到不可见区域后用户找不回来。
OVERLAY_MIN_WIDTH = 480
OVERLAY_MAX_WIDTH = 1200
OVERLAY_MIN_HEIGHT = 140
OVERLAY_MAX_HEIGHT = 400
OVERLAY_COORD_LIMIT = 10000
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


def _int_or_none(value) -> int | None:
    """配置里的坐标允许 null；bool 是 int 子类，必须单独挡掉。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    return None


def resolve_position(settings: dict, screen_w: int, screen_h: int) -> tuple[int, int]:
    """按轴决定位置：overlay.x/y 有值就用绝对坐标，为 null 才用 corner+margin。

    两个轴独立判断——只设 x 不设 y 时，y 仍跟随 corner，方便用户把窗口
    卡在屏幕中间某一侧。T2 的 src/config.py 校验保证这里是 int 或 null。
    """
    corner_x, corner_y = corner_position(settings, screen_w, screen_h)
    abs_x = _int_or_none(settings.get("x"))
    abs_y = _int_or_none(settings.get("y"))
    x = corner_x if abs_x is None else abs_x
    y = corner_y if abs_y is None else abs_y
    return x, y


def overlay_bounds_settings(cfg: dict | None, settings: dict) -> dict:
    """把 overlay.x/y 合并进设置字典，不依赖 server.overlay_settings 是否已升级。

    T2 在 app/server.py 里扩展 overlay_settings() 之前，这里直接从原始配置读，
    保证「config 写了 x/y 就能生效」；升级之后两条路子读到的值一致。
    """
    out = dict(settings)
    raw = cfg.get("overlay") if isinstance(cfg, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    for key in ("x", "y"):
        out[key] = _int_or_none(raw.get(key))
    return out


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
        self._layout_mode = False
        self._lock = threading.RLock()
        self._log = log

    # ---- 基本属性 -------------------------------------------------
    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def settings(self) -> dict:
        return dict(self._settings)

    @property
    def layout_mode(self) -> bool:
        """布局模式：True = 不穿透、页面显示拖动区与缩放手柄。"""
        return bool(self._layout_mode)

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
        # overlay.x/y 绝对坐标优先；为 null 时才落到 corner + margin。
        return resolve_position(settings or self._settings, *self.screen_metrics())

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

    def _force_transparent(self) -> None:
        """把 WinForms Form / WebView2 自身底色也设为透明。

        pywebview 只处理了 WebView2 的 DefaultBackgroundColor；实际分层窗口
        仍可能由 Form.BackColor 透出白底（实测 720x200 整块白）。WebView2
        的控制器属性只能在 UI 线程改，所以这里通过 Form.Invoke 投递。
        """
        try:
            import clr
            clr.AddReference("System.Drawing")
            clr.AddReference("System.Windows.Forms")
            from System.Drawing import Color
            from System.Windows.Forms import ControlStyles
            from System import Action
            native = getattr(self.window, "native", None)
            if native is None:
                return

            def apply() -> None:
                try:
                    native.SetStyle(ControlStyles.SupportsTransparentBackColor, True)
                except Exception:  # noqa: BLE001
                    pass
                key = Color.FromArgb(1, 2, 3)
                try:
                    native.AllowTransparency = True
                    native.BackColor = key
                    native.TransparencyKey = key
                except Exception:  # noqa: BLE001
                    native.BackColor = Color.Transparent
                webview = getattr(native, "webview", None)
                if webview is not None:
                    webview.DefaultBackgroundColor = Color.Transparent

            if getattr(native, "InvokeRequired", False):
                native.Invoke(Action(apply))
            else:
                # after_start 不在 WinForms UI 线程；即使 InvokeRequired 返回 False，
                # 直接改 WebView2 控制器仍会抛「only UI thread」。统一走 Invoke。
                try:
                    native.Invoke(Action(apply))
                except Exception:  # noqa: BLE001 - 真 UI 线程时 Invoke 可能不可用
                    apply()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[overlay] 透明底设置失败：{type(exc).__name__}: {exc}")

    def begin_move(self) -> bool:
        """跟随鼠标手动移动窗口。

        原来用 WM_NCLBUTTONDOWN/HTCAPTION 让系统进入拖动循环，但 Overlay
        带 WS_EX_NOACTIVATE、页面又在 WebView2 里，实测消息进入不了拖动循环。
        改成轮询鼠标位置 + SetWindowPos，最稳，也不依赖窗口激活。
        """
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        api = _api()
        user32 = api["user32"]
        ctypes = api["ctypes"]
        wintypes = api["wintypes"]
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        start_x, start_y = int(pt.x), int(pt.y)
        rect = self.read_window_rect()
        base_left, base_top = rect["left"], rect["top"]
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if not (user32.GetAsyncKeyState(0x01) & 0x8000):
                break
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetWindowPos(
                hwnd, -1, base_left + int(pt.x) - start_x,
                base_top + int(pt.y) - start_y, 0, 0,
                SWP_NOSIZE | SWP_NOACTIVATE, )
            time.sleep(0.012)
        return True

    def begin_resize(self) -> bool:
        """跟随鼠标手动缩放右下角；逻辑与 begin_move 一致。"""
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        api = _api()
        user32 = api["user32"]
        ctypes = api["ctypes"]
        wintypes = api["wintypes"]
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        start_x, start_y = int(pt.x), int(pt.y)
        rect = self.read_window_rect()
        base_left, base_top = rect["left"], rect["top"]
        scale = self._dpi_scale()
        base_w = max(OVERLAY_MIN_WIDTH,
                     int(round(rect["width"] / scale))) if scale else rect["width"]
        base_h = max(OVERLAY_MIN_HEIGHT,
                     int(round(rect["height"] / scale))) if scale else rect["height"]
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if not (user32.GetAsyncKeyState(0x01) & 0x8000):
                break
            user32.GetCursorPos(ctypes.byref(pt))
            dx = int(round((int(pt.x) - start_x) / scale)) if scale else int(pt.x) - start_x
            dy = int(round((int(pt.y) - start_y) / scale)) if scale else int(pt.y) - start_y
            width = max(OVERLAY_MIN_WIDTH, min(OVERLAY_MAX_WIDTH, base_w + dx))
            height = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, base_h + dy))
            user32.SetWindowPos(
                hwnd, -1, base_left, base_top,
                int(round(width * scale)), int(round(height * scale)),
                SWP_NOACTIVATE, )
            time.sleep(0.012)
        return True

    def fit_height(self, height: int) -> dict:
        """按内容高度缩放窗口，保持左上角不动；返回实际矩形。"""
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        rect = self.read_window_rect()
        scale = self._dpi_scale()
        width = int(round(rect["width"] / scale)) if scale else rect["width"]
        x = int(round(rect["left"] / scale)) if scale else rect["left"]
        y = int(round(rect["top"] / scale)) if scale else rect["top"]
        new_h = max(OVERLAY_MIN_HEIGHT, min(OVERLAY_MAX_HEIGHT, int(height)))
        self._settings["height"] = new_h
        self._set_pos(x, y, width, new_h)
        return self.read_window_rect()

    def apply_native_style(self) -> dict:
        """点击穿透 + 不抢焦点 + 不占任务栏 + 分层透明度。

        布局模式下临时去掉 WS_EX_TRANSPARENT（否则鼠标事件全被穿透，
        拖动区/缩放手柄收不到点击），Layered / NoActivate / ToolWindow 保持。
        """
        hwnd = self.hwnd()
        if not hwnd:
            raise RuntimeError("Overlay 窗口尚未创建原生句柄")
        api = _api()
        user32 = api["user32"]
        style_before = int(user32.GetWindowLongW(hwnd, GWL_EXSTYLE))
        wanted = WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        if not self._layout_mode:
            wanted |= WS_EX_TRANSPARENT
        # 清掉 APPWINDOW：否则 TOOLWINDOW 和它同时存在时仍可能出现在任务栏。
        # 布局模式下还要把 TRANSPARENT 清掉，鼠标才能落到本窗口。
        clear = WS_EX_APPWINDOW
        if self._layout_mode:
            clear |= WS_EX_TRANSPARENT
        style_after = (style_before | wanted) & ~clear
        if style_after != style_before:
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, int(style_after))
        self._apply_alpha()
        self._force_transparent()
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

    # ---- 布局模式 / 运行时几何 ------------------------------------
    def _push_layout_mode(self, enabled: bool) -> bool:
        """把布局模式告诉页面（显示/隐藏拖动区、虚线框、缩放手柄）。

        页面可能还没加载完或窗口已销毁，失败只记一行日志，不能拖垮热键线程。
        """
        script = "window.ovSetLayoutMode && window.ovSetLayoutMode(%s)" % (
            "true" if enabled else "false")
        try:
            self.window.evaluate_js(script)
            return True
        except Exception as exc:  # noqa: BLE001 - 页面未就绪/窗口已销毁
            self._log(f"[overlay] 布局模式页面同步失败："
                      f"{type(exc).__name__}: {exc}")
            return False

    def set_layout_mode(self, enabled: bool) -> bool:
        """进入/退出布局模式：切换 WS_EX_TRANSPARENT 并通知页面。"""
        with self._lock:
            enabled = bool(enabled)
            self._layout_mode = enabled
            if self.hwnd():
                try:
                    self.apply_native_style()
                except Exception as exc:  # noqa: BLE001
                    self._log("[overlay] 布局模式原生样式切换失败："
                              f"{type(exc).__name__}: {exc}")
            self._push_layout_mode(enabled)
            return enabled

    @staticmethod
    def _clamp_int(value, lo: int, hi: int, fallback) -> int:
        number = _int_or_none(value)
        if number is None:
            return int(fallback)
        return max(lo, min(hi, number))

    @staticmethod
    def _clamp_coord(value, fallback=None):
        """坐标：None 保留 None（= 按 corner 排），数字钳到 ±OVERLAY_COORD_LIMIT。"""
        number = _int_or_none(value)
        if number is None:
            return fallback
        return max(-OVERLAY_COORD_LIMIT, min(OVERLAY_COORD_LIMIT, number))

    def _bounds_payload(self) -> dict:
        x, y = self.target_position()
        return {"ok": True, "x": int(x), "y": int(y),
                "width": int(self._settings["width"]),
                "height": int(self._settings["height"])}

    def set_bounds(self, x=None, y=None, width=None, height=None) -> dict:
        """运行时移动/缩放（POST /api/overlay/layout 落到这里）。

        x/y 的语义与配置完全一致（T2 保存后会把权威值原样传进来）：
        - 数字 = 绝对坐标，之后不再跟 corner 走；
        - None = 该轴没有绝对坐标，回到 corner + margin。
        width/height 传 None 表示保持当前值。返回实际采用的几何，
        调用方（server）直接回给界面就能让拖动后不回弹。
        """
        with self._lock:
            settings = self._settings
            if width is not None:
                settings["width"] = self._clamp_int(
                    width, OVERLAY_MIN_WIDTH, OVERLAY_MAX_WIDTH,
                    settings["width"])
            if height is not None:
                settings["height"] = self._clamp_int(
                    height, OVERLAY_MIN_HEIGHT, OVERLAY_MAX_HEIGHT,
                    settings["height"])
            settings["x"] = self._clamp_coord(x)
            settings["y"] = self._clamp_coord(y)
            self._resize_and_position()
            return self._bounds_payload()

    def set_position(self, x=None, y=None) -> dict:
        """只设位置；传 None 的轴回到 corner + margin 规则（界面「重置位置」用）。"""
        with self._lock:
            self._settings["x"] = self._clamp_coord(x)
            self._settings["y"] = self._clamp_coord(y)
            self._resize_and_position()
            return self._bounds_payload()

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
            if self._layout_mode:
                # 隐藏时退回点击穿透：下一次显示不应意外变成「能点但不能动」。
                self.set_layout_mode(False)
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
    """后台线程轮询两个组合键（不写配置）：

    - Ctrl+Alt+O：显示/隐藏 Overlay；
    - Ctrl+Alt+L：进入/退出布局模式（关掉点击穿透，允许鼠标拖动/缩放）。

    用 GetAsyncKeyState 而不是 RegisterHotKey：不动系统全局热键表，
    也不会因为 iRacing 独占输入而注册失败。
    """
    if sys.platform != "win32":
        return None
    api = _api()
    user32 = api["user32"]

    def down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def loop() -> None:
        toggle_down = False
        layout_down = False
        while not stop_event.is_set():
            try:
                ctrl_alt = down(VK_CONTROL) and down(VK_MENU)
                toggle_pressed = ctrl_alt and down(VK_O)
                layout_pressed = ctrl_alt and down(VK_L)
                if toggle_pressed and not toggle_down:
                    try:
                        visible = controller.toggle()
                        log(f"[overlay] 热键切换：{'显示' if visible else '隐藏'}")
                    except Exception as exc:  # noqa: BLE001
                        log(f"[overlay] 热键切换失败：{type(exc).__name__}: {exc}")
                if layout_pressed and not layout_down:
                    try:
                        enabled = controller.set_layout_mode(
                            not controller.layout_mode)
                        state = "开（可拖动/缩放，Ctrl+Alt+L 退出）" if enabled \
                            else "关（已恢复点击穿透）"
                        log(f"[overlay] 布局模式：{state}")
                    except Exception as exc:  # noqa: BLE001
                        log("[overlay] 布局模式切换失败："
                            f"{type(exc).__name__}: {exc}")
                toggle_down, layout_down = toggle_pressed, layout_pressed
            except Exception:  # noqa: BLE001
                toggle_down = layout_down = False
            stop_event.wait(OVERLAY_HOTKEY_POLL_S)

    thread = threading.Thread(target=loop, daemon=True, name="overlay-hotkey")
    thread.start()
    return thread


# ------------------------------------------------------------- 冒烟测试
_OVERLAY_PAGE_FACTS_JS = r"""
(function () {
  var card = document.getElementById("ov-card");
  var tag = document.querySelector(".ov-tag");
  var heard = document.getElementById("ov-heard-text");
  var answer = document.getElementById("ov-answer");
  var frame = document.getElementById("ov-frame");
  var handle = document.getElementById("ov-resize");
  function style(el, prop) { return el ? getComputedStyle(el)[prop] : null; }
  var rect = card ? card.getBoundingClientRect() : null;
  return JSON.stringify({
    title: document.title,
    viewport: [window.innerWidth, window.innerHeight],
    card: rect ? {width: rect.width, height: rect.height,
                  left: rect.left, top: rect.top} : null,
    card_width_style: style(card, "width"),
    card_max_width: style(card, "maxWidth"),
    card_background: style(card, "backgroundColor"),
    card_font: style(card, "fontSize"),
    tag_font: style(tag, "fontSize"),
    heard_font: style(heard, "fontSize"),
    answer_font: style(answer, "fontSize"),
    answer_color: style(answer, "color"),
    html_background: getComputedStyle(document.documentElement).backgroundColor,
    body_background: style(document.body, "backgroundColor"),
    body_layout: (document.body && document.body.dataset.layout) || "",
    layout_hook: typeof window.ovSetLayoutMode,
    frame_hidden: frame ? frame.hidden : null,
    handle_hidden: handle ? handle.hidden : null
  });
})()
"""


def _overlay_page_facts(window) -> dict:
    """读页面侧事实（DOM / 计算样式）。JS 不可用时抛异常，由调用方记限制。"""
    data = window.evaluate_js(_OVERLAY_PAGE_FACTS_JS)
    if isinstance(data, str):
        data = json.loads(data)
    return data if isinstance(data, dict) else {}


def _css_alpha(color) -> float:
    """把 getComputedStyle 的颜色值解析成 alpha（0–1）；解析不了按 0。"""
    if not color:
        return 0.0
    text = str(color).strip().lower()
    if text in ("transparent", "rgba(0, 0, 0, 0)"):
        return 0.0
    if text.startswith("rgb("):
        return 1.0
    if text.startswith("rgba("):
        try:
            return float(text[text.index("(") + 1:text.rindex(")")].split(",")[3])
        except (ValueError, IndexError):
            return 0.0
    return 0.0


def _transparent_color(color) -> bool:
    return str(color or "").strip().lower() in ("transparent",
                                                "rgba(0, 0, 0, 0)")


def _is_overlay_key_color(color) -> bool:
    """页面整窗铺 #010203；桌面端用同名色键把它抠掉。"""
    text = str(color or "").strip().lower().replace(" ", "")
    return text in ("#010203", "rgb(1,2,3)", "rgba(1,2,3,1)")


def overlay_smoke_facts(controller: OverlayController, window) -> dict:
    """创建窗口并应用样式后的机械断言（--overlay-smoke-test 用）。

    v1.5.1 的断言（点击穿透 / 置顶定位 / 透明度）全部保留；v1.5.2 追加：
    布局模式标志（原生样式位 + 页面拖动区）、卡片贴合内容与窗口全透明、
    字号统一 14px、运行时几何 set_bounds 生效且最小尺寸有钳制。
    """
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
    original = dict(controller.settings)
    page: dict = {}
    try:
        page = _overlay_page_facts(window)
    except Exception as exc:  # noqa: BLE001
        record["limitations"].append(
            f"页面 JS 校验不可用：{type(exc).__name__}: {exc}")

    def rect_matches(observed: dict, target: dict) -> bool:
        want = [int(round(target[key] * scale))
                for key in ("x", "y", "width", "height")]
        got = [observed["left"], observed["top"],
               observed["width"], observed["height"]]
        return all(abs(a - b) <= tolerance for a, b in zip(got, want))

    viewport = page.get("viewport") or [0, 0]
    card = page.get("card") or {}
    viewport_w = float(viewport[0] or 0)
    viewport_h = float(viewport[1] or 0)
    card_w = float(card.get("width") or 0)
    card_h = float(card.get("height") or 0)

    checks: dict[str, bool] = {
        # ---- v1.5.1 旧断言 ----
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
        "renderer_loaded": page.get("title") == "游戏内 Overlay",
        # ---- v1.5.3 透明 / 贴合内容 / 统一字号 ----
        "page_background_transparent": (
            _transparent_color(page.get("body_background"))
            and _transparent_color(page.get("html_background"))),
        "card_background_transparent": (
            _css_alpha(page.get("card_background")) <= 0.001),
        "card_hugs_content": (
            card_w > 0 and viewport_w > 0 and card_w < viewport_w - 16),
        "card_inside_window": (
            card_w > 0 and card_h > 0 and viewport_h > 0
            and float(card.get("left") or 0) >= 0
            and float(card.get("top") or 0) >= 0
            and card_w <= viewport_w and card_h <= viewport_h),
        "font_size_uniform_14px": all(
            page.get(key) == "14px" for key in
            ("card_font", "tag_font", "heard_font", "answer_font")),
        # ---- v1.5.2 布局模式 ----
        "layout_hook_present": page.get("layout_hook") == "function",
        "layout_mode_default_off": (
            not controller.layout_mode
            and page.get("body_layout") == "false"
            and page.get("handle_hidden") is True
            and page.get("frame_hidden") is True),
    }

    layout_facts: dict = {}
    try:
        controller.set_layout_mode(True)
        style_on = controller.read_extended_style()
        page_on = _overlay_page_facts(window)
        checks["layout_mode_enter_clears_transparent"] = (
            controller.layout_mode and not (style_on & WS_EX_TRANSPARENT))
        checks["layout_mode_enter_keeps_native_bits"] = (
            (style_on & (WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW))
            == (WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW))
        checks["layout_mode_enter_shows_handles"] = (
            page_on.get("body_layout") == "true"
            and page_on.get("handle_hidden") is False
            and page_on.get("frame_hidden") is False)
        controller.set_layout_mode(False)
        style_off = controller.read_extended_style()
        page_off = _overlay_page_facts(window)
        checks["layout_mode_exit_restores_click_through"] = (
            not controller.layout_mode and bool(style_off & WS_EX_TRANSPARENT))
        checks["layout_mode_exit_hides_handles"] = (
            page_off.get("body_layout") == "false"
            and page_off.get("handle_hidden") is True)
        layout_facts = {
            "ex_style_layout_on": f"0x{style_on:08X}",
            "ex_style_layout_off": f"0x{style_off:08X}",
            "page_layout_on": page_on.get("body_layout"),
            "page_layout_off": page_off.get("body_layout"),
        }
    except Exception as exc:  # noqa: BLE001
        record["limitations"].append(
            f"布局模式断言失败：{type(exc).__name__}: {exc}")
        for key in ("layout_mode_enter_clears_transparent",
                    "layout_mode_enter_keeps_native_bits",
                    "layout_mode_enter_shows_handles",
                    "layout_mode_exit_restores_click_through",
                    "layout_mode_exit_hides_handles"):
            checks.setdefault(key, False)

    geometry_facts: dict = {}
    try:
        target = {"x": 120, "y": 140, "width": 520, "height": 180}
        bounds = controller.set_bounds(**target)
        rect_geo = controller.read_window_rect()
        checks["geometry_applied"] = (
            bounds.get("x") == target["x"] and bounds.get("y") == target["y"]
            and bounds.get("width") == target["width"]
            and bounds.get("height") == target["height"]
            and rect_matches(rect_geo, target))
        page_geo = _overlay_page_facts(window)
        vp_geo = page_geo.get("viewport") or [0, 0]
        card_geo = page_geo.get("card") or {}
        checks["card_stays_fit_after_resize"] = (
            float(card_geo.get("width") or 0) > 0
            and float(card_geo.get("width") or 0) < float(vp_geo[0] or 0) - 16)
        clamped = controller.set_bounds(width=10, height=10)
        checks["geometry_clamps_min_size"] = (
            clamped.get("width") == OVERLAY_MIN_WIDTH
            and clamped.get("height") == OVERLAY_MIN_HEIGHT)
        geometry_facts = {
            "bounds_target": target,
            "bounds_applied": bounds,
            "window_rect_after_bounds": rect_geo,
            "bounds_clamped": clamped,
        }
    except Exception as exc:  # noqa: BLE001
        record["limitations"].append(
            f"几何断言失败：{type(exc).__name__}: {exc}")
        checks.setdefault("geometry_applied", False)

    # 还原：settings 与窗口几何都要回到冒烟前的状态，冒烟不能留下副作用。
    try:
        with controller._lock:
            controller._settings.clear()
            controller._settings.update(original)
            controller._resize_and_position()
        restored = controller.read_window_rect()
        checks["geometry_restored"] = (
            abs(restored["left"] - int(round(expected_x * scale))) <= tolerance
            and abs(restored["top"] - int(round(expected_y * scale)))
            <= tolerance)
    except Exception as exc:  # noqa: BLE001
        checks["geometry_restored"] = False
        record["limitations"].append(
            f"几何还原失败：{type(exc).__name__}: {exc}")

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
        "position_source": ("absolute" if controller.settings.get("x") is not None
                            or controller.settings.get("y") is not None
                            else "corner"),
        "window_rect": rect,
        "expected_position_logical": [expected_x, expected_y],
        "dpi_scale": scale,
        "document_title": page.get("title"),
        "body_background": page.get("body_background"),
        "html_background": page.get("html_background"),
        "card_background": page.get("card_background"),
        "card_rect": page.get("card"),
        "card_width_style": page.get("card_width_style"),
        "card_max_width": page.get("card_max_width"),
        "font_sizes": {key: page.get(key) for key in
                       ("tag_font", "heard_font", "answer_font")},
        "layout_mode": controller.layout_mode,
        **layout_facts,
        **geometry_facts,
    }
    if scale != 1.0:
        record["limitations"].append(
            f"DPI 缩放 {scale:.2f}，窗口矩形按物理像素与逻辑位置换算比对")
    record["limitations"].append("多屏环境只按主屏（GetSystemMetrics）定位")
    record["limitations"].append(
        "布局模式的真实鼠标拖动需人工验收；这里只验证原生样式位与页面拖动区可见")
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


def _port_free(port: int) -> bool:
    """端口是否能被我们独占监听。

    Windows 上 Server/ThreadingHTTPServer 默认 allow_reuse_address=True，
    SO_REUSEADDR 允许绑定一个已被其它进程监听的端口（两边都 Listen），
    新连接落到谁手里不确定。用户开着正式版时跑 --overlay-smoke-test，
    Overlay 窗口就可能连到那个进程，读到旧页面/旧静态资源。
    所以先用一个不带 REUSE 的普通 socket 探测一下。
    """
    if not isinstance(port, int) or port <= 0 or port > 65535:
        return False
    with socket.socket() as probe:
        probe.settimeout(0.5)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


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
    # 端口被正式版/其它实例占着时不要“抢”过来：Windows 的 SO_REUSEADDR 会让
    # bind 假成功，导致 Overlay 窗口连到别的进程上。探测失败就用系统端口。
    bind_port = args.port
    if not _port_free(bind_port):
        print(f"⚠ 端口 {bind_port} 已被其他进程占用，改用系统分配的端口"
              "（不会接管已经运行的实例）")
        bind_port = 0
    try:
        httpd = Server(("127.0.0.1", bind_port), Handler)
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

    override = overlay_bounds_settings(svc.cfg, overlay_settings(svc.cfg))
    overlay_enabled = bool(override["enabled"]) or args.overlay_smoke_test
    # overlay.x/y 有值就用绝对坐标（T2 在 src/config.py 校验）；否则 corner+margin。
    if override.get("x") is not None or override.get("y") is not None:
        print(f"[overlay] 使用配置里的绝对坐标：x={override.get('x')} "
              f"y={override.get('y')}")
    # 热键/PTT 冲突提示：默认 PTT 是右 Ctrl，和两个 Overlay 热键的 Ctrl 同一物理键。
    ptt_vk = svc.cfg.get("ptt", {}).get("vk", 0xA3)
    try:
        if int(ptt_vk) in (0x11, 0x12, 0xA2, 0xA3):
            print("提示：Overlay 热键 Ctrl+Alt+O / Ctrl+Alt+L 与 PTT 键位（Ctrl）"
                  "可能短暂冲突；短按通常低于误触阈值，不会被当成提问。")
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
                          "（显示/隐藏，不写配置）；"
                          "Ctrl+Alt+L = 布局模式（拖动/缩放）")
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
            initial_x, initial_y = resolve_position(override, *screen)
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
            # loaded 事件在 WinForms UI 线程触发；after_start 是后台线程，
            # 直接改 WebView2 控制器会抛「only UI thread」。
            def _overlay_loaded() -> None:
                try:
                    overlay_controller._force_transparent()
                except Exception as exc:  # noqa: BLE001
                    print(f"⚠ Overlay 透明底初始化失败：{exc}", flush=True)
            overlay.events.loaded += _overlay_loaded
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
