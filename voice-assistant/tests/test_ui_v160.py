"""v1.6.0 前端 / 桌面契约：删除音量压低、关闭询问 + 托盘、日志层代号中文化、模型拉取。

纯静态 + 纯单元：不启动窗口、不联网、不读写用户配置。
真实浏览器行为（含 pywebview 桥接桩）由 `tools/test_ui_v160.cjs` 覆盖。
"""
import pathlib
import sys
import threading
import unittest

import app.desktop as desktop

STATIC = pathlib.Path(__file__).resolve().parents[1] / "app" / "static"
DESKTOP_SRC = pathlib.Path(desktop.__file__)


def read_static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


class DuckUiRemovedTests(unittest.TestCase):
    """item 1：播放设置里不再出现「压低游戏音量」，前端也不再写 playback.ducking。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read_static("app.js")

    def test_duck_toggle_is_gone(self):
        # 只检查可交互 UI；历史注释里可以说明「已删掉」。
        self.assertNotIn("ira-duck2", self.js)
        self.assertNotIn('id="ira-duck2"', self.js)
        self.assertNotIn('field("压低游戏音量"', self.js)

    def test_ducking_is_not_written_by_frontend(self):
        self.assertNotIn("playback: { ducking", self.js)
        self.assertNotIn("ducking: { enabled", self.js)

    def test_route_panel_still_saves_output_device(self):
        self.assertIn('id="ira-outdev2"', self.js)
        self.assertIn("save({ playback: { device:", self.js)
        self.assertIn("播报输出设备", self.js)
        # 播放设置块仍然只有输出设备一个可选项（自动静麦是固定说明）。
        block = self.js[self.js.index("function renderRoutePanel"):]
        block = block[:block.index("/* ---------- 启动 ---------- */")]
        self.assertNotIn('id="ira-duck2"', block)
        self.assertNotIn("playback: { ducking", block)


class ClosePromptContractTests(unittest.TestCase):
    """item 2 前端：关闭时三选一（托盘 / 退出 / 取消），不破坏 /api/quit。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read_static("app.js")
        cls.css = read_static("app.css")

    def test_three_choices_exist(self):
        for marker in ('id="ln-close-tray"', 'id="ln-close-quit"', 'id="ln-close-cancel"'):
            self.assertIn(marker, self.js)
        self.assertIn("隐藏到系统托盘", self.js)
        self.assertIn("直接退出", self.js)
        self.assertIn("取消", self.js)

    def test_x_button_opens_prompt_instead_of_quitting(self):
        self.assertIn("$('#window-close').onclick=function(){openCloseDialog();};", self.js)
        self.assertNotIn(
            "$('#window-close').onclick=async function(){await api('/api/quit',{});};",
            self.js,
        )

    def test_existing_quit_api_is_preserved(self):
        # 「直接退出」仍然走既有的 /api/quit，不新增/改动后端退出接口。
        self.assertIn('api("/api/quit", {})', self.js)
        self.assertIn('id="ln-close-quit"', self.js)

    def test_tray_uses_pywebview_bridge_with_browser_fallback(self):
        self.assertIn("window.pywebview.api.hide_to_tray", self.js)
        self.assertIn("浏览器预览模式", self.js)
        self.assertIn("无法隐藏到托盘", self.js)

    def test_desktop_closing_hook_is_exposed(self):
        # 桌面端 closing 事件会调这个钩子；必须返回 true 表示已经叫出选择框。
        self.assertIn("window.__lnClosePrompt = function () {", self.js)
        self.assertIn("return true;", self.js[self.js.index("window.__lnClosePrompt"):])

    def test_dialog_is_accessible(self):
        self.assertIn('role="dialog" aria-modal="true"', self.js)
        self.assertIn('aria-labelledby="ln-close-title"', self.js)
        self.assertIn('aria-describedby="ln-close-desc"', self.js)
        self.assertIn('id="ln-close-note" role="status"', self.js)
        self.assertIn('event.key === "Escape"', self.js)

    def test_styles_reuse_existing_tokens(self):
        self.assertIn(".ln-close-layer{", self.css)
        self.assertIn(".ln-close-dialog{", self.css)
        self.assertIn("var(--surface)", self.css[self.css.index(".ln-close-dialog"):])


class LayerLabelContractTests(unittest.TestCase):
    """item 3：内部层代号只在映射表里出现，界面统一显示中文。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read_static("app.js")

    def test_mapping_covers_known_codes(self):
        expected = {
            "phrase": "短语库",
            "l1_keyword": "关键词兜底",
            "ai_disambiguate": "AI 意图仲裁",
            "refuse": "未识别（已拒绝）",
            "segment_full_split": "分段全量（固定话术）",
            "miss": "未识别",
            "l2": "关键词兜底",
            "l3": "遥测路由",
        }
        for code, label in expected.items():
            self.assertIn('%s: "%s"' % (code, label), self.js)

    def test_log_rows_render_chinese_label_not_raw_code(self):
        self.assertIn("badge(cls, esc(layerLabel(r.path)))", self.js)
        # 行的 data-path 仍保留原始代号（便于排障/测试），但不进可见文案。
        self.assertIn('data-path="\' + esc(r.path) + \'"', self.js)

    def test_stats_and_debug_use_same_mapper(self):
        self.assertIn("layerLabel(k)", self.js)
        self.assertIn("layerLabel(tr.layer)", self.js)
        self.assertGreaterEqual(self.js.count("layerLabel("), 4)

    def test_unknown_code_falls_back_to_chinese(self):
        self.assertIn('return LAYER_LABELS[code] || "其他";', self.js)
        # 原始代号不能再作为兜底直接显示。
        self.assertNotIn("(label[k] || k)", self.js)


class AiModelFetchContractTests(unittest.TestCase):
    """lead 追加项：拉取模型 → 升级下拉，保留手填；失败中文提示且不阻塞。"""

    @classmethod
    def setUpClass(cls):
        cls.js = read_static("app.js")
        cls.css = read_static("app.css")

    def test_button_and_get_endpoint(self):
        self.assertIn('id="ira-ai-models"', self.js)
        self.assertIn("拉取模型", self.js)
        # GET：api() 只传一个参数（没有 body 才是 GET，见 app.js 顶部）。
        self.assertIn('api("/api/ai/models")', self.js)
        self.assertNotIn('api("/api/ai/models",', self.js)

    def test_select_upgrade_keeps_custom_fallback(self):
        self.assertIn('id="ira-ai-model-select"', self.js)
        self.assertIn("手动填写 / 自定义模型", self.js)
        self.assertIn('"__custom__"', self.js)
        # 自定义时把原输入框放出来，选择下拉时同步进输入框再保存。
        self.assertIn("input.hidden = false;", self.js)
        self.assertIn("input.value = value;", self.js)
        self.assertIn('model: $("#ira-ai-model", panel).value.trim()', self.js)

    def test_errors_are_chinese_and_manual_input_stays_usable(self):
        self.assertIn("function aiModelsError(", self.js)
        self.assertIn("拉取失败：密钥无效或没有权限", self.js)
        self.assertIn("可以直接手动填写模型名", self.js)
        self.assertIn("用已保存的接口地址与密钥拉取", self.js)

    def test_key_never_enters_the_models_flow(self):
        start = self.js.index('api("/api/ai/models")')
        block = self.js[start - 1200:start + 1200]
        self.assertNotIn("ira-ai-key", block)
        # 注释里可以写「不写 localStorage」；不能真的读写它。
        self.assertNotIn("localStorage.setItem", block)
        self.assertNotIn("localStorage.getItem", block)
        # 模型列表只放内存缓存，不落盘。
        self.assertIn("var aiModelCache = null;", self.js)
        self.assertIn("aiModelCache = list;", self.js)

    def test_model_control_uses_existing_style(self):
        self.assertIn(".ira-model-pick{", self.css)
        self.assertIn('class="ira-select" id="ira-ai-model-select"', self.js)
        self.assertIn('class="ira-input" id="ira-ai-model"', self.js)


class _FakeService:
    def __init__(self):
        self.quit_requested = False


class _FakeWindow:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def evaluate_js(self, script):
        self.calls.append(script)
        if "typeof" in script:
            return self.ready
        return True


class _FakeTray:
    running = True

    def __init__(self):
        self.balloons = []

    def show_balloon(self, text):
        self.balloons.append(text)


class DesktopCloseHookTests(unittest.TestCase):
    """item 2 桌面端：closing 拦截 + 提示 / 托盘 / 退出 的降级顺序（纯单元）。"""

    def test_quit_requested_allows_close(self):
        svc = _FakeService()
        svc.quit_requested = True
        handler = desktop.build_close_handler(
            _FakeWindow(), svc, lambda: self.fail("退出时不应再弹提示"))
        self.assertTrue(handler())

    def test_close_is_cancelled_and_prompt_spawned(self):
        svc = _FakeService()
        prompted = threading.Event()
        handler = desktop.build_close_handler(
            _FakeWindow(), svc, prompted.set)
        self.assertFalse(handler())
        self.assertTrue(prompted.wait(2.0), "关闭时应该异步叫出选择框")
        self.assertFalse(svc.quit_requested)

    def test_prompt_calls_page_hook(self):
        svc = _FakeService()
        window = _FakeWindow(ready=True)
        prompt = desktop.build_close_prompt(window, svc, ensure_tray=None)
        prompt()
        self.assertIn("show", window.calls)
        self.assertIn("window.__lnClosePrompt()", window.calls)
        self.assertFalse(svc.quit_requested)
        self.assertNotIn("hide", window.calls)

    def test_prompt_falls_back_to_quit_when_page_not_ready(self):
        svc = _FakeService()
        window = _FakeWindow(ready=False)
        prompt = desktop.build_close_prompt(window, svc, ensure_tray=None)
        prompt()
        self.assertTrue(svc.quit_requested, "页面不可用且无托盘时应直接退出，不能卡死")

    def test_prompt_falls_back_to_tray_when_page_not_ready(self):
        svc = _FakeService()
        window = _FakeWindow(ready=False)
        tray = _FakeTray()
        prompt = desktop.build_close_prompt(window, svc, ensure_tray=lambda: tray)
        prompt()
        self.assertIn("hide", window.calls)
        self.assertTrue(tray.balloons)
        self.assertFalse(svc.quit_requested)


class TrayIconContractTests(unittest.TestCase):
    """托盘实现不新增依赖（pythonnet + WinForms），且未启动时不冒充可用。"""

    def test_tray_is_lazy_and_off_by_default(self):
        tray = desktop.TrayIcon(None, lambda: None, log=lambda *_a: None)
        self.assertFalse(tray.running)
        for method in ("start", "stop", "show_balloon"):
            self.assertTrue(callable(getattr(tray, method)))

    def test_no_new_tray_dependencies(self):
        source = DESKTOP_SRC.read_text(encoding="utf-8")
        self.assertNotIn("import pystray", source)
        self.assertNotIn("from pystray", source)
        self.assertNotIn("from PIL", source)
        self.assertIn("NotifyIcon", source)
        self.assertIn("window.events.closing +=", source)

    def test_non_windows_degrades_cleanly(self):
        if sys.platform == "win32":
            self.skipTest("Windows 上会尝试真实托盘，这里只验证非 Windows 降级")
        tray = desktop.TrayIcon(None, lambda: None, log=lambda *_a: None)
        self.assertFalse(tray.start(timeout=0.2))


if __name__ == "__main__":
    unittest.main()
