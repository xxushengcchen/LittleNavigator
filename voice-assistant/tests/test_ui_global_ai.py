"""v1.5.2 设置界面静态契约（UI 字段/接口/回填）。

不启动浏览器、不联网：只读 `app/static/app.js` 文本，守护「全局交给 AI」开关、
「关闭思考模式」开关、语音合成试听反馈、Overlay 布局/尺寸/位置设置的关键契约。
真实点击行为由 `tools/test_ui.cjs`（浏览器）覆盖。
"""
import pathlib
import re
import unittest

STATIC = pathlib.Path(__file__).resolve().parents[1] / "app" / "static"
APP_JS = STATIC / "app.js"
INDEX_HTML = STATIC / "index.html"


class GlobalAiSwitchContractTests(unittest.TestCase):
    """AI 工程师页顶部「全局交给 AI」：开=all，关=恢复原档位。"""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_global_switch_exists_with_explicit_on_off_options(self):
        self.assertIn('id="ira-ai-global"', self.js)
        # 显眼入口：放在模型接入标题之前，并用现有 ira-notice / ira-switchrow 风格。
        self.assertIn('class="ira-notice" id="ira-global-ai"', self.js)
        self.assertIn("全局交给 AI", self.js)
        self.assertLess(self.js.index('id="ira-global-ai"'),
                        self.js.index("<h2>模型接入</h2>"))

    def test_switch_explains_local_rules_stay_silent(self):
        self.assertIn("所有问答只走 AI，本地规则不再出声；安全/操作类只显示文字", self.js)

    def test_turn_on_saves_mode_all(self):
        # 必须是字面量 { ai: { mode: "all" } }，方便评审一眼核对线上请求体。
        self.assertIn('{ ai: { mode: "all" } }', self.js)
        self.assertIn('mode: "all"', self.js)

    def test_turn_off_restores_remembered_mode_else_whitelist(self):
        self.assertIn("var globalAiRestoreMode = null;", self.js)
        # 进入页面时记住非 all 的档位（renderAiPanel 里），关闭时兜底 whitelist。
        self.assertIn('if (aiMode !== "all") globalAiRestoreMode = aiMode;', self.js)
        self.assertIn("var restore = globalAiRestoreMode || \"whitelist\";", self.js)
        self.assertIn('{ ai: { mode: restore } }', self.js)

    def test_backfill_from_config_ai(self):
        # 回填走 state.config.ai.mode；缺字段时按 whitelist 兜底。
        self.assertIn('var aiMode = a.mode || "whitelist";', self.js)
        self.assertIn("a.thinking_mode", self.js)
        self.assertIn("a.global_stop_local", self.js)
        # mode 的四个档位保持原样，不因新开关删减。
        for mode in ("whitelist", "analysis_key", "full", "all"):
            self.assertIn('value="%s"' % mode, self.js)
        self.assertIn('id="ira-ai-mode"', self.js)


class ThinkingModeSwitchContractTests(unittest.TestCase):
    """模型接入区「关闭思考模式」：开=off，关=low，并进 ai patch。"""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_switch_exists_inside_model_section(self):
        self.assertIn('id="ira-ai-thinking"', self.js)
        self.assertIn("思考模式", self.js)
        start = self.js.index('id="ira-global-ai"')
        end = self.js.index('<summary>语音合成')
        self.assertTrue(start < self.js.index('id="ira-ai-thinking"') < end)

    def test_hint_states_tradeoff(self):
        self.assertIn("关闭最快；不关闭时使用最低思考强度", self.js)

    def test_values_are_off_and_low_and_go_into_ai_patch(self):
        self.assertIn('p.thinking_mode', self.js)
        self.assertIn('=== "low" ? "low" : "off"', self.js)
        self.assertIn('option value="off"', self.js)
        self.assertIn('option value="low"', self.js)
        # 默认开（关闭思考）：只有配置为 low 才显示「不关闭」。
        self.assertIn('var thinkingOff = a.thinking_mode !== "low";', self.js)


class TtsTestFeedbackContractTests(unittest.TestCase):
    """语音合成试听：成功必须显示「已播放」，失败显示 error，不只报耗时。"""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_calls_tts_test_endpoint(self):
        self.assertIn('id="ira-tts-test"', self.js)
        self.assertIn('"/api/ai/tts/test"', self.js)

    def test_success_says_played(self):
        self.assertIn('"已播放"', self.js)
        # 明确区分「服务端没真播」的情况，避免只看耗时误判成功。
        self.assertIn("r.played === false", self.js)

    def test_failure_shows_error_not_just_latency(self):
        block = self.js[self.js.index('"/api/ai/tts/test"'):]
        block = block[:900]
        self.assertIn('"失败：" + (r.error || "未知错误")', block)
        # 旧实现只显示「成功 N 字节，N ms」，没有「已播放」语义。
        self.assertNotIn('("成功 " + r.bytes + " 字节，" + r.latency_ms + " ms")', self.js)


class OverlayLayoutContractTests(unittest.TestCase):
    """Overlay 设置区：布局模式、宽高、X/Y（可空）、重置、回填。"""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_layout_mode_switch_calls_runtime_endpoint(self):
        self.assertIn('id="ira-ov-layout"', self.js)
        self.assertIn('"/api/overlay/layout_mode"', self.js)
        self.assertIn("{ enabled: want }", self.js)
        self.assertIn("layout_mode", self.js)

    def test_geometry_inputs_exist_with_empty_position_semantics(self):
        for field in ("ira-ov-width", "ira-ov-height", "ira-ov-x", "ira-ov-y"):
            self.assertIn('id="%s"' % field, self.js)
        self.assertIn('class="ira-input ira-num" id="ira-ov-width"', self.js)
        self.assertIn('placeholder="留空 = 按角落"', self.js)
        self.assertIn("留空表示按「显示位置」的角落加边距排布", self.js)

    def test_empty_position_saves_null(self):
        helper = self.js[self.js.index("function overlayPositionInput"):]
        self.assertIn("if (!text) return null;", helper[:500])
        self.assertIn("x: overlayPositionInput(", self.js)
        self.assertIn("y: overlayPositionInput(", self.js)

    def test_save_overlay_includes_geometry_and_clamps(self):
        block = self.js[self.js.index("var patch = { overlay: {"):]
        block = block[:600]
        for key in ("width:", "height:", "x:", "y:"):
            self.assertIn(key, block)
        self.assertIn("function overlayIntInput(el, fallback, min, max)", self.js)
        self.assertIn('overlayIntInput($("#ira-ov-width", panel), 720, 240, 4096)', self.js)
        self.assertIn('overlayIntInput($("#ira-ov-height", panel), 200, 80, 2160)', self.js)

    def test_reset_restores_default_position_and_size(self):
        self.assertIn('id="ira-ov-reset"', self.js)
        self.assertIn("恢复默认位置与尺寸", self.js)
        self.assertIn('save({ overlay: { x: null, y: null, width: 720, height: 200 } }', self.js)

    def test_backfill_from_state_overlay(self):
        # 回填 state.overlay：layout_mode / x / y / width / height，缺字段时回落到 config。
        self.assertIn("var ovLayout = !!ov.layout_mode;", self.js)
        for key in ("width", "height", "x", "y"):
            self.assertIn('ovValue("%s"' % key, self.js)
        self.assertIn("var ovCfg = (st.config && st.config.overlay) || {};", self.js)
        self.assertIn("var ovWidth = ovValue(\"width\", 720);", self.js)
        self.assertIn("var ovHeight = ovValue(\"height\", 200);", self.js)


class ExistingUiStyleContractTests(unittest.TestCase):
    """沿用现有设置块风格，不重排页面、不新造样式体系。"""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_new_controls_use_shared_field_helpers(self):
        # 所有新控件都通过 field() 生成（ira-field 结构）。
        self.assertIn("function field(label, inner, hint) {", self.js)
        block = self.js[self.js.index('id="ira-global-ai"'):]
        block = block[:self.js.index("bindAiPanel(panel, hint);") - self.js.index('id="ira-global-ai"')]
        self.assertIn('class="ira-select"', block)
        self.assertIn("field('思考模式'", block)
        self.assertIn("field('布局模式'", block)
        self.assertIn("field('窗口尺寸'", block)
        self.assertIn("field('位置 X / Y'", block)

    def test_no_inline_style_system_or_new_stylesheet(self):
        # 只在必要处用内联宽度兜住下拉框；不新造 CSS 类/样式表。
        self.assertNotIn("<style", self.js)
        for forbidden in (".ai-global-", "ai-global-card", "ira-settingcard"):
            self.assertNotIn(forbidden, self.js)

    def test_page_structure_unchanged(self):
        # AI 页仍是单张 settings-sheet 卡片，未重排页面。
        self.assertIn('data-view-panel="ai"', self.html)
        self.assertIn('<div class="ira-card settings-sheet"></div>', self.html)
        self.assertEqual(self.html.count('data-view-panel="ai"'), 1)

    def test_no_hardcoded_secrets_or_network_urls(self):
        # 静态契约只做卫生检查：设置面板不应出现明文密钥或外部地址。
        self.assertIsNone(re.search(r"sk-[A-Za-z0-9]{20,}", self.js))
        self.assertNotIn("api.deepseek.com/v1/chat", self.js)


if __name__ == "__main__":
    unittest.main()
