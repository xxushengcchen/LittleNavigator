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
        self.assertEqual(self.load()["ai"]["timeout_s"], 2.0)

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
        self.assertEqual(self.load()["ai"]["tts"]["timeout_s"], 1.5)

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


if __name__ == '__main__':
    unittest.main()
