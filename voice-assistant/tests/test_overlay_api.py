"""游戏内 Overlay 接口回归（v1.5.1）。

不创建真实窗口：用假 controller 验证 /api/overlay、/api/overlay/toggle、
/overlay 路由与 configure(overlay) 的契约；没有 controller 时验证优雅降级。
真实窗口的点击穿透/透明度由 `python -m app.desktop --overlay-smoke-test` 验证。
"""
import json
import pathlib
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock

import yaml

from src import config
from app.server import (Handler, OVERLAY_DEFAULTS, OVERLAY_CORNERS, Server,
                        Service, overlay_settings)

TOKEN = "overlay-test-token"


class FakeOverlayController:
    """记录被调用的 setter；toggle() 语义与桌面端一致（返回切换后可见性）。"""

    def __init__(self):
        self.visible = False
        self.calls = []

    def set_enabled(self, enabled):
        self.calls.append(("enabled", enabled))
        self.visible = bool(enabled)

    def set_opacity(self, opacity):
        self.calls.append(("opacity", opacity))

    def set_corner(self, corner):
        self.calls.append(("corner", corner))

    def set_geometry(self, width, height, margin=None):
        self.calls.append(("geometry", (width, height, margin)))

    def toggle(self):
        self.visible = not self.visible
        return self.visible


def make_service(**attrs):
    """绕过 Assistant 的轻量 Service：只放 Overlay 相关字段。"""
    svc = object.__new__(Service)
    svc.cfg = {"overlay": dict(OVERLAY_DEFAULTS)}
    svc.asst = types.SimpleNamespace(live={})
    svc.overlay_controller = None
    svc.api_token = TOKEN
    for key, value in attrs.items():
        setattr(svc, key, value)
    return svc


class OverlaySettingsTests(unittest.TestCase):
    def test_missing_section_uses_defaults(self):
        settings = overlay_settings({})
        for key, value in OVERLAY_DEFAULTS.items():
            self.assertEqual(settings[key], value, key)

    def test_partial_section_is_completed(self):
        settings = overlay_settings({"overlay": {"opacity": 0.5,
                                                 "corner": "bottom-left"}})
        self.assertEqual(settings["opacity"], 0.5)
        self.assertEqual(settings["corner"], "bottom-left")
        self.assertEqual(settings["width"], OVERLAY_DEFAULTS["width"])
        self.assertIs(settings["enabled"], False)

    def test_bad_values_fall_back_without_raising(self):
        settings = overlay_settings({"overlay": {
            "enabled": "yes",        # 非布尔
            "opacity": "lots",       # 非数字
            "corner": "middle",      # 非法方位
            "width": None,
            "margin": -5,
        }})
        self.assertIs(settings["enabled"], False)
        self.assertEqual(settings["opacity"], OVERLAY_DEFAULTS["opacity"])
        self.assertEqual(settings["corner"], "top-right")
        self.assertEqual(settings["width"], OVERLAY_DEFAULTS["width"])
        self.assertEqual(settings["margin"], 0)

    def test_overlay_section_missing_from_cfg_is_safe(self):
        self.assertIsInstance(overlay_settings(None), dict)
        self.assertIsInstance(overlay_settings({}), dict)


class OverlayStateTests(unittest.TestCase):
    def test_state_shape_and_live_passthrough(self):
        live = {"heard": "还剩几圈", "state": "thinking",
                "answer": "", "error": ""}
        svc = make_service(overlay_controller=FakeOverlayController(),
                           asst=types.SimpleNamespace(live=live))
        state = svc.overlay_state()
        self.assertTrue(state["available"])
        self.assertEqual(state["enabled"], OVERLAY_DEFAULTS["enabled"])
        self.assertEqual(state["opacity"], OVERLAY_DEFAULTS["opacity"])
        self.assertEqual(state["corner"], OVERLAY_DEFAULTS["corner"])
        self.assertEqual(state["live"], live)
        # 契约内的附加字段（前端滑杆/热键说明用），保持存在。
        for key in ("margin", "width", "height", "toggle_key", "visible"):
            self.assertIn(key, state, key)

    def test_live_missing_is_idle(self):
        svc = make_service(overlay_controller=FakeOverlayController())
        live = svc.overlay_state()["live"]
        self.assertEqual(live["state"], "idle")
        self.assertEqual(live["heard"], "")
        self.assertEqual(live["answer"], "")
        self.assertEqual(live["error"], "")

    def test_live_bad_state_normalised(self):
        svc = make_service(
            overlay_controller=FakeOverlayController(),
            asst=types.SimpleNamespace(live={"state": "exploded",
                                             "heard": None, "answer": 3}))
        live = svc.overlay_state()["live"]
        self.assertEqual(live["state"], "idle")
        self.assertEqual(live["heard"], "")
        self.assertEqual(live["answer"], "3")

    def test_without_controller_is_available_false(self):
        svc = make_service()
        self.assertFalse(svc.overlay_state()["available"])


class OverlayToggleTests(unittest.TestCase):
    def test_toggle_uses_controller_and_keeps_config(self):
        controller = FakeOverlayController()
        svc = make_service(overlay_controller=controller)
        before = json.dumps(svc.cfg, ensure_ascii=False, sort_keys=True)
        first = svc.toggle_overlay()
        second = svc.toggle_overlay()
        self.assertEqual(first, {"ok": True, "available": True, "visible": True})
        self.assertEqual(second, {"ok": True, "available": True, "visible": False})
        self.assertEqual(json.dumps(svc.cfg, ensure_ascii=False, sort_keys=True),
                         before)
        self.assertFalse(controller.visible)

    def test_toggle_without_controller_is_graceful(self):
        result = make_service().toggle_overlay()
        self.assertFalse(result["ok"])
        self.assertFalse(result["available"])
        self.assertIn("error", result)

    def test_controller_exception_is_reported_not_raised(self):
        class Broken:
            visible = False

            def toggle(self):
                raise RuntimeError("窗口已经销毁")

        result = make_service(overlay_controller=Broken()).toggle_overlay()
        self.assertFalse(result["ok"])
        self.assertIn("RuntimeError", result["error"])


class OverlayConfigureTests(unittest.TestCase):
    """configure(overlay=...) 保存后必须立即调用窗口 controller。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        base = config.load()
        for section in ("voice_pack",):
            for key, value in list(base[section].items()):
                if isinstance(value, str) and not pathlib.Path(value).is_absolute():
                    base[section][key] = str(config.resolve(base, value))
        (root / "config.yaml").write_text(
            yaml.safe_dump(base, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        self.cfg = config.load(root / "config.yaml")
        self.root = root

    def _service(self):
        controller = FakeOverlayController()
        svc = make_service(overlay_controller=controller,
                           cfg=self.cfg, cfg_path=str(self.root / "config.yaml"),
                           listener_lock=threading.RLock())

        # 真实 _configure 会写盘并重新 load() 配置；这里模拟「保存成功」，
        # 同时把 patch 合并进内存配置，才能验证热生效读的是保存后的值。
        def fake_configure(patch):
            merged = dict(svc.cfg)
            if isinstance(patch.get("overlay"), dict):
                overlay = dict(merged.get("overlay") or {})
                overlay.update(patch["overlay"])
                merged["overlay"] = overlay
            svc.cfg = merged
            return {"ok": True, "saved": "x", "restart_hint": [], "applied": {}}

        svc._configure = MagicMock(side_effect=fake_configure)
        return svc, controller

    def test_configure_applies_overlay_settings_to_controller(self):
        svc, controller = self._service()
        result = svc.configure({"overlay": {"enabled": True, "opacity": 0.6,
                                            "corner": "bottom-left"}})
        self.assertTrue(result["ok"])
        self.assertEqual(controller.calls, [("enabled", True),
                                           ("opacity", 0.6),
                                           ("corner", "bottom-left")])
        self.assertEqual(result["applied_overlay"]["enabled"], True)
        svc._configure.assert_called_once()

    def test_configure_without_controller_still_saves(self):
        svc, _ = self._service()
        svc.overlay_controller = None
        result = svc.configure({"overlay": {"enabled": True}})
        self.assertTrue(result["ok"])
        self.assertNotIn("applied_overlay", result)

    def test_configure_rejects_unknown_overlay_key(self):
        svc, _ = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"overlay": {"click_through": True}})
        svc._configure.assert_not_called()

    def test_configure_does_not_touch_controller_when_save_fails(self):
        svc, controller = self._service()
        svc._configure = MagicMock(return_value={"ok": False, "error": "监听尚未结束"})
        result = svc.configure({"overlay": {"opacity": 0.5}})
        self.assertFalse(result["ok"])
        self.assertEqual(controller.calls, [])


class OverlayHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.controller = FakeOverlayController()
        self.service = make_service(
            overlay_controller=self.controller,
            asst=types.SimpleNamespace(live={"heard": "油还够吗",
                                             "state": "answer",
                                             "answer": "还剩 12 圈",
                                             "error": ""}),
            cfg={"overlay": {"enabled": True, "opacity": 0.7,
                             "corner": "top-left"}})

        class BoundHandler(Handler):
            pass

        BoundHandler.service = self.service
        self.server = Server(("127.0.0.1", 0), BoundHandler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self._stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.base + path, data=data,
                                         headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_get_overlay_without_controller_graceful(self):
        self.service.overlay_controller = None
        status, _, body = self._request("/api/overlay")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["live"]["state"], "answer")

    def test_get_overlay_returns_config_and_live(self):
        status, headers, body = self._request("/api/overlay")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["opacity"], 0.7)
        self.assertEqual(payload["corner"], "top-left")
        self.assertEqual(payload["live"]["heard"], "油还够吗")
        self.assertEqual(payload["live"]["answer"], "还剩 12 圈")

    def test_overlay_page_served_without_token(self):
        status, headers, body = self._request("/overlay")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        text = body.decode("utf-8")
        self.assertIn("游戏内 Overlay", text)
        self.assertIn("/static/overlay.js", text)
        self.assertIn("/static/overlay.css", text)

    def test_toggle_requires_token(self):
        status, _, _ = self._request("/api/overlay/toggle", data=b"{}")
        self.assertEqual(status, 403)
        self.assertFalse(self.controller.visible)

    def test_toggle_with_token_toggles_controller(self):
        headers = {"X-Assistant-Token": TOKEN,
                   "Content-Type": "application/json"}
        status, _, body = self._request("/api/overlay/toggle", data=b"{}",
                                        headers=headers)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["visible"])
        self.assertTrue(self.controller.visible)


class OverlayPositionTests(unittest.TestCase):
    def test_four_corners_use_margin(self):
        from app.desktop import corner_position
        settings = {"width": 720, "height": 200, "margin": 24,
                    "corner": "top-right"}
        self.assertEqual(corner_position(settings, 1920, 1080), (1176, 24))
        settings["corner"] = "top-left"
        self.assertEqual(corner_position(settings, 1920, 1080), (24, 24))
        settings["corner"] = "bottom-left"
        self.assertEqual(corner_position(settings, 1920, 1080), (24, 856))
        settings["corner"] = "bottom-right"
        self.assertEqual(corner_position(settings, 1920, 1080), (1176, 856))

    def test_position_never_negative_on_tiny_screen(self):
        from app.desktop import corner_position
        settings = {"width": 720, "height": 200, "margin": 24,
                    "corner": "bottom-right"}
        # 窗口比屏幕宽时 x 只能贴到 0；y 仍按正常公式算（480-200-24）。
        self.assertEqual(corner_position(settings, 640, 480), (0, 256))


class OverlayStaticContractTests(unittest.TestCase):
    """Overlay 前端契约：无按钮/图片/外链、透明背景、250ms 轮询、无变化不重绘。"""

    STATIC = pathlib.Path(__file__).resolve().parents[1] / "app" / "static"

    def read(self, name):
        return (self.STATIC / name).read_text(encoding="utf-8")

    def test_html_is_minimal_and_self_contained(self):
        html = self.read("overlay.html")
        self.assertIn("/static/overlay.css", html)
        self.assertIn("/static/overlay.js", html)
        for blocker in ("<button", "<img", "<a ", "http://", "https://"):
            self.assertNotIn(blocker, html)
        # 两行内容：听到 / 状态或回答
        self.assertIn("听到", html)

    def test_css_uses_shared_tokens_and_transparent_page(self):
        css = self.read("overlay.css")
        for token in ("--ground:#171719", "--surface:#131217",
                      "--accent:#6ba8ed"):
            self.assertIn(token, css)
        self.assertIn("background:transparent", css)
        self.assertIn("border:1px solid var(--border)", css)
        self.assertIn("border-radius:var(--r)", css)

    def test_js_polls_250ms_and_skips_unchanged_dom(self):
        js = self.read("overlay.js")
        self.assertIn("250", js)
        self.assertIn("lastSignature", js)
        self.assertIn("textContent", js)
        self.assertIn("AI 正在思考", js)
        for state in ("idle", "thinking", "answer", "error"):
            self.assertIn(state, js)

    def test_overlay_page_has_no_external_assets_in_any_file(self):
        for name in ("overlay.html", "overlay.css", "overlay.js"):
            content = self.read(name)
            self.assertNotIn("http://", content, name)
            self.assertNotIn("https://", content, name)


class AiPanelFrontendContractTests(unittest.TestCase):
    """v1.5.1 补丁：AI 页必须暴露 all 档、等待语音开关、透明度 20% 口径。"""

    SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"

    def read(self):
        return self.SCRIPT.read_text(encoding="utf-8")

    def test_response_mode_has_all_option(self):
        js = self.read()
        self.assertIn('id="ira-ai-mode"', js)
        self.assertIn('value="all"', js)
        self.assertIn("全部交给 AI", js)
        # 说明必须讲清边界：安全/处罚/改车仍本地、要配语音合成。
        self.assertIn("安全、处罚、改车仍走本地规则", js)
        self.assertIn("需配好语音合成才听得到回答", js)

    def test_ack_switch_backfills_and_saves(self):
        js = self.read()
        self.assertIn('id="ira-ack-on"', js)
        self.assertIn("ack_enabled", js)
        self.assertIn("ack: { enabled:", js)
        self.assertIn("等待语音", js)

    def test_overlay_opacity_lower_bound_is_20_percent(self):
        js = self.read()
        self.assertIn('min="20" max="100" step="5"', js)
        self.assertIn("Math.max(0.2,", js)
        self.assertIn("最低 20%", js)
        self.assertNotIn("Math.max(0.3,", js)


if __name__ == "__main__":
    unittest.main()
