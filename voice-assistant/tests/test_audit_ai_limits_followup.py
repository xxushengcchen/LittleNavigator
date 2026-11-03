"""A16 rate limiting and A17 per-configuration learn directories."""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest import mock

import yaml

from src import ai_pipeline, config, llm_engine
from src.query_class import QueryKind
from src.telemetry import Snapshot


class RateGateTests(unittest.TestCase):
    def test_window_slides_and_zero_disables(self):
        now = [1000.0]
        gate = ai_pipeline.RateGate(2, clock=lambda: now[0])
        self.assertTrue(gate.acquire())
        self.assertTrue(gate.acquire())
        self.assertFalse(gate.acquire(), "第三分钟内请求应被拒")
        now[0] += 61
        self.assertTrue(gate.acquire(), "窗口滑过后应恢复")
        self.assertFalse(ai_pipeline.RateGate(0).acquire(), "0 表示禁止自动外发")


class PipelineRateLimitTests(unittest.TestCase):
    def _pipeline(self, limit):
        pipe = ai_pipeline.AiPipeline.__new__(ai_pipeline.AiPipeline)
        pipe.mode = "all"
        pipe.whitelist = []
        pipe.store = None
        pipe.max_answer_chars = 60
        pipe.max_input_tokens = 1500
        pipe.rate_limit_per_min = limit
        pipe.rate_gate = ai_pipeline.RateGate(limit)
        pipe.breaker = llm_engine.AdaptiveBreaker()
        pipe.snapshot_provider = None
        pipe.tts = None
        pipe.player = None
        pipe.out = None
        pipe.thinking_mode = "off"
        pipe._log = lambda *a, **k: None
        pipe._notify_live = lambda *a, **k: None
        pipe._maybe_speak = lambda *a, **k: None
        client = mock.Mock()
        client.ask.return_value = {"refused": False, "answer": "现在读不到"}
        pipe.client = client
        return pipe

    def test_limit_blocks_before_sending_request(self):
        pipe = self._pipeline(1)
        first = pipe.handle("油还够吗", Snapshot(connected=True), QueryKind.ANALYSIS, [])
        self.assertTrue(first.ok, first.reason)
        second = pipe.handle("油还够吗", Snapshot(connected=True), QueryKind.ANALYSIS, [])
        self.assertFalse(second.ok)
        self.assertIn("频繁", second.reason)
        self.assertEqual(pipe.client.ask.call_count, 1, "超限时不得再发请求")

    def test_zero_disables_automatic_model_calls(self):
        pipe = self._pipeline(0)
        result = pipe.handle("油还够吗", Snapshot(connected=True), QueryKind.ANALYSIS, [])
        self.assertFalse(result.ok)
        pipe.client.ask.assert_not_called()


class LearnDirIsolationTests(unittest.TestCase):
    def test_explicit_config_gets_its_own_learn_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            first, second = root / "a.yaml", root / "b.yaml"
            for path in (first, second):
                path.write_text(yaml.safe_dump({}), encoding="utf-8")
            cfg_a = config.load(first)
            cfg_b = config.load(second)
            self.assertEqual(os.path.realpath(cfg_a["_ai_learn_dir"]),
                             os.path.realpath(str(root / "ai_learn")))
            self.assertEqual(cfg_a["_ai_learn_dir"], cfg_b["_ai_learn_dir"])

    def test_default_config_keeps_shared_runtime_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = pathlib.Path(tmp)
            with mock.patch.object(config, "runtime_dir", return_value=runtime), \
                 mock.patch.object(config, "data_dir", return_value=runtime / "data"), \
                 mock.patch.object(config, "installed_mode", return_value=False):
                cfg = config.load()
            self.assertEqual(os.path.realpath(cfg["_ai_learn_dir"]),
                             os.path.realpath(str(runtime / "data" / "ai_learn")))


if __name__ == "__main__":
    unittest.main()
