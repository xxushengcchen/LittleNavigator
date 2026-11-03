"""v1.6.2 前端剩余 15 项（F04/F05/F07/F08/F09/F10/F11/F12/F13/F14/F16/F17/F18/F19/F20）回归。

前端没有常驻 DOM 环境，所以这里断言真实控制流与可计算契约：
- 源码顺序/结构：谁在 await 前后、谁清理草稿、哪个分支处理哪种状态；
- 纯函数行为：把 app.js 里的函数原文抽出来交给 node 执行，验证舍入结果；
- 颜色数学：按 WCAG 2.1 计算真实对比度；
- 前后端契约：从 src/config.py 读取 Overlay 范围，与前端表单比较。

每条断言都对应审计里的一个可复现事实；先红后绿，红证据见 tmp/v161/team2-frontend/。
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CHARTS = (STATIC / "charts.js").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CONFIG_PY = (ROOT / "src" / "config.py").read_text(encoding="utf-8")


def function_body(name: str, source: str = JS) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(name)


def block_after(marker: str, source: str = JS) -> str:
    """从 marker 起截到同层监听器的 `});`（只用于结构比较）。"""
    start = source.index(marker)
    return source[start:start + 900]


def wcag_contrast(hex_a: str, hex_b: str) -> float:
    def lum(color: str) -> float:
        raw = color.lstrip("#")
        channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    hi, lo = sorted((lum(hex_a), lum(hex_b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class SaveRaceTests(unittest.TestCase):
    """F04：保存请求发出后的新编辑不能被旧响应删掉。"""

    def test_save_snapshots_drafts_before_request_and_guards_delete(self):
        body = function_body("save")
        snapshot = body.index("submitted")
        request = body.index("await api(\"/api/config\"")
        conditional = body.index("pendingDrafts[id] !== submitted[id]")
        delete = body.index("delete pendingDrafts[id]")
        self.assertLess(snapshot, request, "必须在发请求前抓提交值快照")
        self.assertLess(request, conditional, "响应回来后要按快照判断")
        self.assertLess(conditional, delete, "只有没被新编辑覆盖的草稿才删除")

    def test_edit_revision_is_tracked_by_input_listener(self):
        self.assertIn("draftRevision", JS, "要有草稿代次")
        listener = JS[JS.index("$('#ira-main').addEventListener('input'"):]
        listener = listener[:listener.index("});")]
        self.assertIn("draftRevision", listener, "任何输入都要推进草稿代次")

    def test_late_settings_load_cannot_overwrite_newer_render(self):
        body = function_body("loadSettings")
        self.assertIn("settingsGeneration", body, "旧 loadSettings 响应要作废")
        guard = body.index("settingsGeneration")
        self.assertIn("generation", body[:guard + 200])
        self.assertGreater(JS.count("settingsGeneration"), 1)


class PresetDraftTests(unittest.TestCase):
    """F05：服务预设派生出的地址/模型必须登记为草稿。"""

    def test_preset_writes_derived_values_into_draft_store(self):
        preset = JS[JS.index('preset.addEventListener("change"'):]
        preset = preset[:preset.index("});")]
        self.assertIn('pendingDrafts["ira-ai-url"]', preset, "派生地址要进草稿存储")
        self.assertIn('pendingDrafts["ira-ai-model"]', preset, "派生模型要进草稿存储")
        self.assertIn("draftRevision", preset, "程序写入也要推进代次")


class OverlayBoundsTests(unittest.TestCase):
    """F07：Overlay 表单范围必须与后端校验一致。"""

    def test_form_attributes_match_backend_bounds(self):
        self.assertIn('id="ira-ov-width" inputmode="numeric" min="480" max="1200"', JS)
        self.assertIn('id="ira-ov-height" inputmode="numeric" min="140" max="400"', JS)
        self.assertIn("宽度 480–1200，高度 140–400", JS)

    def test_js_clamp_uses_backend_bounds(self):
        self.assertIn("overlayIntInput($(\"#ira-ov-width\", panel), 720, 480, 1200)", JS)
        self.assertIn("overlayIntInput($(\"#ira-ov-height\", panel), 200, 140, 400)", JS)

    def test_backend_really_declares_those_bounds(self):
        match = re.search(r'\("width", (\d+), (\d+)\),\s*\n\s*\("height", (\d+), (\d+)\)',
                          CONFIG_PY)
        self.assertIsNotNone(match, "config.py 的 overlay 范围要能解析")
        self.assertEqual(tuple(map(int, match.groups())), (480, 1200, 140, 400))


class TtsVoiceTests(unittest.TestCase):
    """F08：表单显示/保存的音色必须能压过后端残留的旧 voice。"""

    def test_speaker_patch_also_sets_voice(self):
        body = function_body("ttsPatchFromPanel")
        self.assertIn("speaker", body)
        self.assertIn("t.voice", body, "只存 speaker 会被旧 voice 覆盖；两份都要写")

    def test_form_still_prefers_speaker_for_display(self):
        self.assertIn("tts.speaker || tts.voice", JS)


class PlaybackFailureTests(unittest.TestCase):
    """F09：playback.ok=false 必须把后端原因显示出来，而不是静默成功。"""

    def test_render_trace_surfaces_failed_playback_reason(self):
        body = function_body("renderTrace")
        self.assertIn("playback.ok === false", body, "要识别播放失败")
        self.assertIn("reportError", body, "失败要有全局可见错误")
        failed = body[body.index("playback.ok === false"):]
        self.assertIn("reason", failed[:600], "要显示后端给的 reason")


class KnowledgeFocusTests(unittest.TestCase):
    """F10：重建图谱/矩阵后焦点必须回到同一节点或格子。"""

    def test_kb_select_restores_focus_after_rerender(self):
        body = function_body("kbSelect")
        render = body.index("kbRenderAll()")
        focus = body.index("kbFocusSelection")
        self.assertLess(render, focus, "先重建再恢复焦点")

    def test_focus_helper_queries_graph_node_and_matrix_controls(self):
        body = function_body("kbFocusSelection")
        self.assertIn("data-key", body, "图谱节点要按 data-key 找回")
        self.assertIn("data-kb-row", body, "矩阵行要能找回")
        self.assertIn("data-kb-cell", body, "矩阵格子要能找回")
        self.assertIn(".focus", body)


class MatrixContrastTests(unittest.TestCase):
    """F11：10.5px 计数文字对每个最终背景都必须 >= 4.5:1。"""

    def test_all_final_cell_colours_pass_aa_small_text(self):
        body = function_body("renderKbMatrix")
        colours = re.findall(r'return "(#[0-9a-fA-F]{6})";', body)
        self.assertEqual(len(colours), 4, "四档背景色都要参与计算")
        ink = "#f6f6f7"
        for colour in colours:
            ratio = wcag_contrast(ink, colour)
            self.assertGreaterEqual(round(ratio, 2), 4.5, f"{colour} 对比度只有 {ratio:.2f}:1")

    def test_cell_font_stays_small_but_measured(self):
        self.assertIn(".kb-cell{width:100%;height:30px", CSS)
        self.assertIn("font:10.5px/1 var(--mono)", CSS)


class GraphReadabilityTests(unittest.TestCase):
    """F12：小窗口不许把 11.5px 名义字号压到 5px。"""

    def test_canvas_has_minimum_width_and_internal_scroll(self):
        viewbox = re.search(r'viewBox: "0 0 (\d+) "', JS)
        self.assertIsNotNone(viewbox)
        canvas_width = int(viewbox.group(1))
        css_svg = CSS[CSS.index(".kb-view svg{"):]
        css_svg = css_svg[:css_svg.index("}")]
        min_width = re.search(r"min-width:(\d+)px", css_svg)
        self.assertIsNotNone(min_width, "图谱画布要有最小宽度，避免被压扁")
        self.assertGreaterEqual(int(min_width.group(1)), canvas_width,
                                "最小宽度不能小于 viewBox 宽度，否则 CTM<1")
        self.assertIn("[data-kb-graph]{overflow", CSS, "小窗口依靠容器内部滚动")


class LogStatsErrorTests(unittest.TestCase):
    """F13：日志/统计接口失败不能被伪装成空数据。"""

    def test_log_error_is_distinct_from_empty(self):
        body = function_body("loadLog")
        self.assertIn("logError", body)
        self.assertIn("logError =", body, "catch 要记录失败")
        render = function_body("renderLog")
        self.assertIn("logError", render)
        error_branch = render.index("logError")
        empty_branch = render.index("还没有记录")
        self.assertLess(error_branch, empty_branch, "错误分支要先于空状态")

    def test_stats_error_is_not_swallowed(self):
        body = function_body("loadStats")
        self.assertNotRegex(body, r"catch \(e\) \{\s*return;\s*\}", "不能静默吞掉统计错误")
        self.assertIn("statsError", body)
        self.assertIn("读取失败", body)


class BusModeTests(unittest.TestCase):
    """F14：总线四个档位标签都要正确，all 不能显示成白名单。"""

    def test_bus_has_all_four_mode_labels(self):
        body = function_body("renderBus")
        self.assertIn('ai.mode === "all"', body)
        for label in ("白名单", "按键触发", "全部交给 AI"):
            self.assertIn(label, body)


class DeviceNameTests(unittest.TestCase):
    """F16：合法设备名配置不能被显示成「系统默认」。"""

    def test_device_options_select_configured_name(self):
        body = function_body("deviceOptions")
        self.assertIn("typeof current === \"string\"", body)
        self.assertIn("selected", body)
        self.assertIn("name", body)

    def test_device_save_preserves_string_names(self):
        self.assertIn("function deviceValue", JS)
        asr = function_body("renderAsrPanel")
        self.assertIn("deviceValue(", asr, "麦克风保存必须保留设备名")
        route = function_body("renderRoutePanel")
        self.assertIn("deviceValue(", route, "输出设备保存必须保留设备名")


class IntegerValidationTests(unittest.TestCase):
    """F17：非法整数必须报错，不能悄悄变成默认值。"""

    def test_strict_int_helper_exists_and_reports(self):
        self.assertIn("function intFieldValue", JS)
        body = function_body("intFieldValue")
        self.assertIn("reportError", body)
        self.assertIn("Number.isInteger", body)
        self.assertIn("value =", body, "非法输入要回填保存值，显示与保存一致")

    def test_handlers_use_strict_helper(self):
        asr = function_body("renderAsrPanel")
        self.assertNotIn("parseInt(this.value, 10) || 4", asr)
        self.assertNotIn("parseInt(this.value, 10) || 250", asr)
        self.assertIn('intFieldValue($("#ira-asrthreads2"', asr)
        self.assertIn('intFieldValue($("#ira-hold"', asr)


class TimeRoundingTests(unittest.TestCase):
    """F18：分钟边界必须向分钟进位，不能出现 0:60。"""

    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("node 不可用")

    def _run(self, cases):
        source = function_body("formatTime")
        script = source + "\nconst cases = JSON.parse(process.argv[1]);" \
                 "\nprocess.stdout.write(JSON.stringify(cases.map(c => formatTime(c[0], c[1]))));"
        out = subprocess.run([self.node, "-e", script, json.dumps(cases)],
                             capture_output=True, text=True, timeout=30, check=True)
        return json.loads(out.stdout)

    def test_minute_carry(self):
        cases = [[59.5, 0], [59.999, 2], [119.999, 2], [59.49, 0], [0, 0],
                 [65.256, 2], [125.4, 1]]
        expected = ["1:00", "1:00.00", "2:00.00", "0:59", "0:00", "1:05.26", "2:05.4"]
        self.assertEqual(self._run(cases), expected)


class ChartsObserverTests(unittest.TestCase):
    """F19：每次重绘不能新增一个永不释放的 ResizeObserver。"""

    def test_watch_disconnects_previous_observer(self):
        body = function_body("watch", CHARTS)
        self.assertEqual(body.count("new ResizeObserver"), 1)
        self.assertIn("disconnect", body, "新的观察者出现前必须释放旧的")
        self.assertIn("el._chartObserver", body, "观察者要挂在元素上便于复用/释放")

    def test_stats_refresh_path_rewatches_without_leak(self):
        stats = function_body("loadStats")
        self.assertIn("Charts.bars", stats)


class KnowledgeTabsTests(unittest.TestCase):
    """F20：role=tablist 的选项卡契约要完整（ARIA + 方向键 + roving tabindex）。"""

    def test_tabs_declare_controls_and_panels(self):
        self.assertIn('id="kb-mode-graph" aria-selected="true" aria-controls="kb-view-graph"', HTML)
        self.assertIn('id="kb-mode-matrix" aria-selected="false" aria-controls="kb-view-matrix"', HTML)
        self.assertIn('role="tabpanel" aria-labelledby="kb-mode-graph"', HTML)
        self.assertIn('role="tabpanel" aria-labelledby="kb-mode-matrix"', HTML)

    def test_arrow_key_navigation_and_roving_tabindex(self):
        self.assertIn('"ArrowRight"', JS)
        self.assertIn('"ArrowLeft"', JS)
        self.assertIn('"Home"', JS)
        self.assertIn('"End"', JS)
        self.assertIn("tabIndex", JS, "roving tabindex 需要一个在 Tab 序列内")
        kb_mode = function_body("kbSetMode")
        self.assertIn("tabIndex", kb_mode, "切换模式时同步 roving tabindex")


if __name__ == "__main__":
    unittest.main()
