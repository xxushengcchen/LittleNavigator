"""Regression tests for the independent-review findings (v1.6.5 follow-up)."""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "voice-assistant" / "packaging"
sys.path.insert(0, str(PKG))


class Q23GateTests(unittest.TestCase):
    """评审发现：yes/on/TRUE/1、flow 写法、access_key 都能绕过发行闸门。"""

    def _sanitize(self, text):
        import importlib
        import build_dist
        importlib.reload(build_dist)
        return build_dist.sanitize_release_config(text)

    def test_yaml_boolean_variants_are_forced_off(self):
        import yaml
        for raw in ("ai:\n  enabled: yes\n",
                    "ai:\n  enabled: on\n",
                    "ai:\n  enabled: TRUE\n",
                    "ai:\n  enabled: 1\n",
                    "ai: {enabled: true}\n"):
            with self.subTest(raw=raw):
                out, warnings = self._sanitize(raw)
                parsed = yaml.safe_load(out) or {}
                self.assertFalse(parsed["ai"]["enabled"])
                self.assertTrue(warnings)

    def test_cloud_credential_field_names_are_blocked(self):
        for field in ("api_key", "access_key", "app_key", "secret_key",
                      "private_key", "password"):
            with self.subTest(field=field):
                with self.assertRaises(Exception):
                    self._sanitize(f"ai:\n  {field}: sk-live-abcdef123456\n")

    def test_counting_fields_are_not_mistaken_for_secrets(self):
        out, _ = self._sanitize("ai:\n  max_tokens: 512\n  token_budget: 900\n")
        self.assertIn("max_tokens: 512", out)


class Q11QcAudioExitTests(unittest.TestCase):
    """评审发现：--fix 成功后仍退出 1。"""

    def test_successful_fix_returns_zero(self):
        sys.path.insert(0, str(ROOT / "voice-assistant"))
        from tools.qc_audio import outcome_exit
        self.assertEqual(outcome_exit([], [], True, 0, []), 0)
        self.assertEqual(outcome_exit([], [], True, 1, []), 1)
        self.assertEqual(outcome_exit(["a"], [], True, 0, []), 1)


class A06GuardTests(unittest.TestCase):
    """评审发现：最快/最慢仍会被当成同一对象。"""

    def test_fastest_and_slowest_are_different_signatures(self):
        sys.path.insert(0, str(ROOT / "voice-assistant"))
        from src.ai_learn import _guard_signature
        self.assertNotEqual(_guard_signature("我最快圈多少"),
                            _guard_signature("我最慢圈多少"))

    def test_first_person_pronouns_do_not_block_learning_hit(self):
        from src.ai_learn import _guard_signature
        self.assertEqual(_guard_signature("我上一圈圈速多少"),
                         _guard_signature("上一圈圈速多少"))


class A19FlattenTests(unittest.TestCase):
    """评审发现：best_loss 有两段时模板永远算不出来。"""

    def test_two_array_items_are_indexed_not_dropped(self):
        from src.ai_learn import _flatten_values
        values = _flatten_values({"segments": {"best_loss": [
            {"delta_s": 0.2}, {"delta_s": 0.4}]}})
        self.assertEqual(values["segments.best_loss[0].delta_s"], 0.2)
        self.assertEqual(values["segments.best_loss[1].delta_s"], 0.4)
        self.assertNotIn("delta_s", values)      # 无索引短名仍有歧义，不填

    def test_indexed_template_renders(self):
        from src.ai_learn import fill_template
        tpl = {"params": ["segments.best_loss[0].delta_s"],
               "rules": [{"when": "true",
                          "say": "第 1 段损失 {segments.best_loss[0].delta_s} 秒"}]}
        self.assertEqual(fill_template(tpl, {"segments": {"best_loss": [
            {"delta_s": 0.2}, {"delta_s": 0.4}]}}), "第 1 段损失 0.2 秒")


class A10UnitTests(unittest.TestCase):
    """评审发现：候选字段都不属于已知量纲时 fail-open。"""

    def test_unmapped_candidates_do_not_pass_unit_check(self):
        sys.path.insert(0, str(ROOT / "voice-assistant"))
        from src.answer_checks import _unit_matches
        self.assertFalse(_unit_matches("laps", [("segments.count", 3.0)]))
        self.assertTrue(_unit_matches("laps", [("laps_remain", 3.0)]))
        # 圈号例外：lap 不在单位映射里，保持宽容以免误杀「12 秒」
        self.assertTrue(_unit_matches("seconds", [("lap", 12.0)]))


class A12CancelLiveTests(unittest.TestCase):
    """评审发现：取消后 Overlay 停在 thinking / 旧答案。"""

    def test_cancel_callback_clears_live(self):
        sys.path.insert(0, str(ROOT / "voice-assistant"))
        from src.engine import Assistant
        seen = {}
        asst = Assistant.__new__(Assistant)
        asst.live = {"heard": "", "state": "thinking", "answer": "", "error": ""}
        asst._on_ai_cancel()
        seen = dict(asst.live)
        self.assertEqual(seen["state"], "idle")
        self.assertEqual(seen["answer"], "")
        self.assertIn("取消", seen["error"])


class A09ReviewMarkTests(unittest.TestCase):
    """评审发现：模型回答点 bad/meh 被当成错误；ok 的 rejected 仍报成功。"""

    def test_model_answer_mark_is_ignored_not_error(self):
        sys.path.insert(0, str(ROOT / "voice-assistant"))
        from app.server import Service
        import inspect
        source = inspect.getsource(Service.ai_review_mark)
        self.assertIn('"stored_as": "ignored"', source)
        self.assertIn("stored_as", source)


if __name__ == "__main__":
    unittest.main()
