import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import yaml

from src import config


class AiConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "config.yaml").write_text(yaml.safe_dump({}), encoding="utf-8")

    def load(self):
        return config.load(self.root / "config.yaml")

    def test_defaults_are_disabled(self):
        cfg = self.load()
        self.assertFalse(cfg["ai"]["enabled"])
        self.assertEqual(cfg["ai"]["mode"], "whitelist")
        self.assertEqual(cfg["ai"]["tts"]["adapter"], "off")

    def test_default_whitelist_excludes_flags(self):
        cfg = self.load()
        self.assertNotIn("query_flags", cfg["ai"]["whitelist_intents"])
        self.assertIn("query_laptime", cfg["ai"]["whitelist_intents"])

    def test_timeout_is_clamped_to_hard_max(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"timeout_s": 10}}), encoding="utf-8")
        self.assertEqual(self.load()["ai"]["timeout_s"], 3.0)

    def test_max_answer_chars_is_clamped(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"max_answer_chars": 200}}), encoding="utf-8")
        self.assertEqual(self.load()["ai"]["max_answer_chars"], 60)

    def test_max_tokens_is_clamped(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"max_tokens": 9999}}), encoding="utf-8")
        self.assertEqual(self.load()["ai"]["max_tokens"], 300)

    def test_tts_timeout_is_clamped(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"tts": {"timeout_s": 99}}}), encoding="utf-8")
        self.assertEqual(self.load()["ai"]["tts"]["timeout_s"], 2.5)

    def test_default_timeouts_match_v151(self):
        cfg = self.load()
        self.assertEqual(cfg["ai"]["timeout_s"], 3.0)
        self.assertEqual(cfg["ai"]["tts"]["timeout_s"], 2.5)

    def test_smaller_values_are_kept(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"timeout_s": 0.7, "max_answer_chars": 30}}),
            encoding="utf-8")
        cfg = self.load()
        self.assertEqual(cfg["ai"]["timeout_s"], 0.7)
        self.assertEqual(cfg["ai"]["max_answer_chars"], 30)

    def test_env_key_overrides_file(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"api_key": "fromfile"}}), encoding="utf-8")
        with patch.dict(os.environ, {"LITTLE_NAVIGATOR_AI_KEY": "fromenv"}):
            cfg = self.load()
        self.assertEqual(cfg["ai"]["api_key"], "fromenv")

    def test_env_absent_keeps_file_value(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"api_key": "fromfile"}}), encoding="utf-8")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LITTLE_NAVIGATOR_AI_KEY", None)
            cfg = self.load()
        self.assertEqual(cfg["ai"]["api_key"], "fromfile")

    def test_ai_learn_dir_is_set(self):
        cfg = self.load()
        self.assertIn("_ai_learn_dir", cfg)
        self.assertTrue(cfg["_ai_learn_dir"].endswith("ai_learn"))

    def test_nan_timeout_is_rejected(self):
        (self.root / "config.local.yaml").write_text(
            "ai:\n  timeout_s: .nan\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_bad_mode_is_rejected(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"mode": "nope"}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_bad_tts_adapter_is_rejected(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"tts": {"adapter": "nope"}}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_bad_analysis_key_vk_is_rejected(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"analysis_key": {"vk": 999}}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_null_analysis_key_is_allowed(self):
        self.assertIsNone(self.load()["ai"]["analysis_key"])

    def test_whitelist_must_be_list_of_strings(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"whitelist_intents": "query_fuel"}}),
            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    # ---- v1.5.1：all 档 / 等待语音 / Overlay 配置 ----
    def test_all_mode_is_accepted(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"mode": "all"}}), encoding="utf-8")
        self.assertEqual(self.load()["ai"]["mode"], "all")

    def test_ack_defaults_to_enabled(self):
        # 默认开：等待语音就是为了填补等模型的空场，用户不配也该有。
        self.assertTrue(self.load()["ai"]["ack"]["enabled"])

    def test_ack_can_be_disabled(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"ack": {"enabled": False}}}), encoding="utf-8")
        self.assertFalse(self.load()["ai"]["ack"]["enabled"])

    def test_ack_enabled_must_be_bool(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"ack": {"enabled": "yes"}}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_ack_must_be_object(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"ack": True}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_overlay_defaults(self):
        ov = self.load()["overlay"]
        self.assertFalse(ov["enabled"])
        self.assertEqual(ov["opacity"], 0.85)
        self.assertEqual(ov["corner"], "top-right")
        self.assertEqual(ov["margin"], 24)
        self.assertEqual(ov["width"], 720)
        self.assertEqual(ov["height"], 200)
        self.assertEqual(ov["toggle_key"], "Ctrl+Alt+O")

    def test_overlay_accepts_custom_values(self):
        (self.root / "config.local.yaml").write_text(yaml.safe_dump({"overlay": {
            "enabled": True, "opacity": 0.5, "corner": "bottom-left",
            "margin": 0, "width": 480, "height": 400,
            "toggle_key": "F9"}}), encoding="utf-8")
        ov = self.load()["overlay"]
        self.assertTrue(ov["enabled"])
        self.assertEqual(ov["opacity"], 0.5)
        self.assertEqual(ov["corner"], "bottom-left")
        self.assertEqual(ov["width"], 480)
        self.assertEqual(ov["height"], 400)

    def test_overlay_bad_corner_is_rejected(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"overlay": {"corner": "center"}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_overlay_opacity_range_is_enforced(self):
        for bad in (0.19, 1.5, float("nan")):
            with self.subTest(opacity=bad):
                (self.root / "config.local.yaml").write_text(
                    yaml.safe_dump({"overlay": {"opacity": bad}}),
                    encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.load()

    def test_overlay_size_must_be_integer_in_range(self):
        for key, bad in (("width", 100), ("height", 100), ("margin", 401),
                         ("width", 720.5)):
            with self.subTest(key=key, value=bad):
                (self.root / "config.local.yaml").write_text(
                    yaml.safe_dump({"overlay": {key: bad}}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.load()

    def test_overlay_toggle_key_must_be_nonempty_string(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"overlay": {"toggle_key": "  "}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()


class AiOverrideMergeTests(unittest.TestCase):
    """局部覆盖不能把整段 ai 配置冲掉。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "config.yaml").write_text(
            yaml.safe_dump({"ai": {"enabled": True, "mode": "full"}}),
            encoding="utf-8")
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"api_key": "k"}}), encoding="utf-8")

    def test_deep_merge_keeps_other_keys(self):
        cfg = config.load(self.root / "config.yaml")
        self.assertTrue(cfg["ai"]["enabled"])
        self.assertEqual(cfg["ai"]["mode"], "full")
        self.assertEqual(cfg["ai"]["api_key"], "k")
        self.assertEqual(cfg["ai"]["max_tokens"], 300)


class ConfigV152Tests(unittest.TestCase):
    """v1.5.2 新字段：全局停止本地播报 / 思考模式 / Overlay 绝对坐标。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "config.yaml").write_text(yaml.safe_dump({}), encoding="utf-8")

    def load(self):
        return config.load(self.root / "config.yaml")

    def write_local(self, payload: dict):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    # ---- ai.global_stop_local
    def test_global_stop_local_defaults_true(self):
        self.assertIs(self.load()["ai"]["global_stop_local"], True)

    def test_global_stop_local_can_be_disabled(self):
        self.write_local({"ai": {"global_stop_local": False}})
        self.assertIs(self.load()["ai"]["global_stop_local"], False)

    def test_global_stop_local_must_be_bool(self):
        self.write_local({"ai": {"global_stop_local": "yes"}})
        with self.assertRaises(ValueError):
            self.load()

    # ---- ai.thinking_mode
    def test_thinking_mode_defaults_off(self):
        self.assertEqual(self.load()["ai"]["thinking_mode"], "off")

    def test_thinking_mode_accepts_low(self):
        self.write_local({"ai": {"thinking_mode": "low"}})
        self.assertEqual(self.load()["ai"]["thinking_mode"], "low")

    def test_bad_thinking_mode_is_rejected(self):
        for bad in ("high", "middle", "OFF", True, None):
            with self.subTest(value=bad):
                self.write_local({"ai": {"thinking_mode": bad}})
                with self.assertRaises(ValueError):
                    self.load()

    # ---- overlay.x / y
    def test_overlay_xy_default_to_null(self):
        ov = self.load()["overlay"]
        self.assertIsNone(ov["x"])
        self.assertIsNone(ov["y"])

    def test_overlay_xy_accept_integers(self):
        self.write_local({"overlay": {"x": -120, "y": 4321}})
        ov = self.load()["overlay"]
        self.assertEqual(ov["x"], -120)
        self.assertEqual(ov["y"], 4321)

    def test_overlay_xy_accept_extreme_bounds(self):
        self.write_local({"overlay": {"x": -10000, "y": 10000}})
        ov = self.load()["overlay"]
        self.assertEqual(ov["x"], -10000)
        self.assertEqual(ov["y"], 10000)

    def test_overlay_xy_reject_out_of_range(self):
        for bad in (10001, -10001):
            with self.subTest(value=bad):
                self.write_local({"overlay": {"x": bad}})
                with self.assertRaises(ValueError):
                    self.load()

    def test_overlay_xy_reject_non_integer(self):
        for bad in (12.5, "3", True):
            with self.subTest(value=bad):
                self.write_local({"overlay": {"y": bad}})
                with self.assertRaises(ValueError):
                    self.load()


class DisambiguateConfigTests(unittest.TestCase):
    """v1.5.5 意图仲裁：ai.disambiguate 默认值、校验与只读视图。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "config.yaml").write_text(yaml.safe_dump({}), encoding="utf-8")

    def load(self):
        return config.load(self.root / "config.yaml")

    def write_local(self, payload: dict):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    # ---- 默认值 / 正常路径
    def test_defaults(self):
        dis = self.load()["ai"]["disambiguate"]
        self.assertIs(dis["enabled"], True)
        self.assertEqual(dis["timeout_s"], 1.0)
        self.assertEqual(dis["min_confidence"], 0.6)

    def test_full_override_is_accepted(self):
        self.write_local({"ai": {"disambiguate": {
            "enabled": False, "timeout_s": 0.3, "min_confidence": 0.0}}})
        dis = self.load()["ai"]["disambiguate"]
        self.assertIs(dis["enabled"], False)
        self.assertEqual(dis["timeout_s"], 0.3)
        self.assertEqual(dis["min_confidence"], 0.0)

    def test_partial_override_keeps_other_defaults(self):
        self.write_local({"ai": {"disambiguate": {"timeout_s": 2.0}}})
        dis = self.load()["ai"]["disambiguate"]
        self.assertIs(dis["enabled"], True)
        self.assertEqual(dis["timeout_s"], 2.0)
        self.assertEqual(dis["min_confidence"], 0.6)

    def test_boundaries_are_accepted(self):
        self.write_local({"ai": {"disambiguate": {
            "timeout_s": 3.0, "min_confidence": 1.0}}})
        dis = self.load()["ai"]["disambiguate"]
        self.assertEqual(dis["timeout_s"], 3.0)
        self.assertEqual(dis["min_confidence"], 1.0)

    # ---- 拒绝非法值
    def test_rejects_non_bool_enabled(self):
        for bad in ("yes", 1, None):
            with self.subTest(value=bad):
                self.write_local({"ai": {"disambiguate": {"enabled": bad}}})
                with self.assertRaises(ValueError):
                    self.load()

    def test_rejects_timeout_out_of_range(self):
        for bad in (0.29, 3.01, -1.0):
            with self.subTest(value=bad):
                self.write_local({"ai": {"disambiguate": {"timeout_s": bad}}})
                with self.assertRaises(ValueError):
                    self.load()

    def test_rejects_non_numeric_timeout(self):
        for bad in ("1.0", True, None):
            with self.subTest(value=bad):
                self.write_local({"ai": {"disambiguate": {"timeout_s": bad}}})
                with self.assertRaises(ValueError):
                    self.load()

    def test_rejects_nan_timeout(self):
        (self.root / "config.local.yaml").write_text(
            "ai:\n  disambiguate:\n    timeout_s: .nan\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load()

    def test_rejects_confidence_out_of_range(self):
        for bad in (-0.01, 1.01):
            with self.subTest(value=bad):
                self.write_local({"ai": {"disambiguate": {
                    "min_confidence": bad}}})
                with self.assertRaises(ValueError):
                    self.load()

    def test_rejects_non_numeric_confidence(self):
        for bad in ("0.5", True):
            with self.subTest(value=bad):
                self.write_local({"ai": {"disambiguate": {
                    "min_confidence": bad}}})
                with self.assertRaises(ValueError):
                    self.load()

    def test_rejects_disambiguate_not_object(self):
        for bad in (False, [], "on"):
            with self.subTest(value=bad):
                self.write_local({"ai": {"disambiguate": bad}})
                with self.assertRaises(ValueError):
                    self.load()

    # ---- 只读视图（engine 接线用）
    def test_view_exposes_three_readonly_fields(self):
        view = config.ai_disambiguate_view(config.load(
            self.root / "config.yaml"))
        self.assertEqual(set(view), {
            "disambiguate_enabled", "disambiguate_timeout_s",
            "disambiguate_min_confidence"})
        self.assertIs(view["disambiguate_enabled"], True)
        self.assertEqual(view["disambiguate_timeout_s"], 1.0)
        self.assertEqual(view["disambiguate_min_confidence"], 0.6)

    def test_view_reads_overrides(self):
        self.write_local({"ai": {"disambiguate": {
            "enabled": False, "timeout_s": 2.5, "min_confidence": 0.25}}})
        view = config.ai_disambiguate_view(config.load(
            self.root / "config.yaml"))
        self.assertIs(view["disambiguate_enabled"], False)
        self.assertEqual(view["disambiguate_timeout_s"], 2.5)
        self.assertEqual(view["disambiguate_min_confidence"], 0.25)

    def test_view_defaults_on_empty_or_broken_cfg(self):
        for cfg in (None, {}, {"ai": None}, {"ai": {"disambiguate": None}},
                    {"ai": {"disambiguate": {"enabled": "x",
                                             "timeout_s": "y",
                                             "min_confidence": None}}}):
            with self.subTest(cfg=cfg):
                view = config.ai_disambiguate_view(cfg)
                self.assertIs(view["disambiguate_enabled"], True)
                self.assertEqual(view["disambiguate_timeout_s"], 1.0)
                self.assertEqual(view["disambiguate_min_confidence"], 0.6)

    def test_view_never_contains_key_material(self):
        cfg = config.load(self.root / "config.yaml")
        cfg["ai"]["api_key"] = "sk-SECRET-123"
        text = json.dumps(config.ai_disambiguate_view(cfg), ensure_ascii=False)
        self.assertNotIn("SECRET", text)


class ShippedConfigDisambiguateTests(unittest.TestCase):
    """仓库自带的两份配置必须把新段写出来（用户能看见、能改）。"""

    def test_shipped_configs_document_disambiguate(self):
        for name in ("config.yaml", "config.example.yaml"):
            with self.subTest(name=name):
                path = config.ROOT / name
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                dis = data["ai"]["disambiguate"]
                self.assertIs(dis["enabled"], True)
                self.assertEqual(dis["timeout_s"], 1.0)
                self.assertEqual(dis["min_confidence"], 0.6)

    def test_shipped_config_loads_cleanly(self):
        cfg = config.load(config.ROOT / "config.yaml")
        dis = cfg["ai"]["disambiguate"]
        self.assertIn("enabled", dis)


if __name__ == '__main__':
    unittest.main()
