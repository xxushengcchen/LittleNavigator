import io
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.engine import Assistant
from src.query_class import QueryKind


def make_cfg(root: pathlib.Path, ai_enabled: bool = True, **ai) -> dict:
    cfg = {
        "voice_pack": {"dir": "x", "extra_dir": None, "telemetry_dir": None,
                       "cooldown": 0,
                       "state_file": str(root / "cooldown.json")},
        "telemetry": {"provider": "sim", "poll_hz": 1,
                      "ring_buffer": {"keep_laps": 5}},
        "intent": {"l1_keyword": {"enabled": False}, "voice_strict": True,
                   "miss_log": {"enabled": False,
                                "path": str(root / "miss.jsonl")}},
        "playback": {"enabled": False, "volume": 1.0,
                     "ducking": {"enabled": False}},
        "logging": {"dir": str(root / "logs"),
                    "qa_log": str(root / "logs" / "qa.jsonl")},
        "asr": {"model": "sense-voice", "num_threads": 1, "models_dir": "m"},
        "ptt": {"vk": 0xA3, "poll_ms": 10, "min_hold_ms": 250, "max_hold_s": 8},
        "ai": {"enabled": ai_enabled, "mode": "full", "base_url": "http://x",
               "api_key": "k", "model": "m", "timeout_s": 2.0,
               "max_tokens": 300, "max_answer_chars": 60,
               "rate_limit_per_min": 6, "whitelist_intents": [],
               "analysis_key": None,
               "tts": {"adapter": "off"}},
        "_root": str(root), "_local_path": str(root / "l.json"),
        "_ai_learn_dir": str(root / "ai_learn"),
    }
    cfg["ai"].update(ai)
    return cfg


def fake_pack(has: bool = True):
    """最小可用的 VoicePack 替身。

    `has()` 与 `variants` 必须一致：只让 has() 返回 True 而 variants 为空，
    `_say()` 会走到 picker 并抛 KeyError——那是 mock 的锅，不是实现的锅。
    """
    pack = MagicMock()
    pack.variants = {}
    pack.stats.return_value = {"msgIds": 0, "clips": 0, "avg_variants": 0}
    pack.sample_rate.return_value = 16000
    pack.has.return_value = False        # 没有素材：_say() 会静默跳过
    pack.has.side_effect = None
    if has:
        pack.has.return_value = False    # 默认仍按「素材未合成」处理
    return pack


class AssistantAiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _assistant(self, ai_enabled=True, **ai):
        with patch("src.engine.VoicePack.load_many", return_value=fake_pack()):
            return Assistant(make_cfg(self.root, ai_enabled, **ai),
                             no_play=True, out=io.StringIO())

    def test_ai_disabled_means_no_pipeline(self):
        a = self._assistant(ai_enabled=False)
        self.assertFalse(a.ai_enabled)
        self.assertIsNone(a.ai)

    def test_ai_enabled_builds_pipeline(self):
        a = self._assistant(ai_enabled=True)
        self.assertTrue(a.ai_enabled)
        self.assertIsNotNone(a.ai)

    def test_ai_status_reports_disabled(self):
        a = self._assistant(ai_enabled=False)
        s = a.ai_status()
        self.assertFalse(s["enabled"])
        self.assertFalse(s["available"])

    def test_should_engage_is_false_when_disabled(self):
        a = self._assistant(ai_enabled=False)
        self.assertFalse(a._ai_should_engage(QueryKind.ANALYSIS, "为什么慢"))

    def test_should_engage_blocks_safety(self):
        a = self._assistant(ai_enabled=True)
        self.assertFalse(a._ai_should_engage(QueryKind.SAFETY, "为什么被罚"))

    def test_should_engage_allows_analysis(self):
        a = self._assistant(ai_enabled=True)
        self.assertTrue(a._ai_should_engage(QueryKind.ANALYSIS, "为什么慢"))

    def test_ask_returns_ai_eligible_flag(self):
        a = self._assistant(ai_enabled=True)
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(intent=None, score=0.2,
                                                 phrase="", margin=0.0,
                                                 reason="未命中", ranked=[])
        a.player.enabled = False
        rec = a.ask("今天天气不错", source="keyboard")
        self.assertFalse(rec["ok"])
        self.assertIn("ai_eligible", rec)
        self.assertIn("kind", rec)

    def test_ask_does_not_call_model(self):
        a = self._assistant(ai_enabled=True)
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(intent=None, score=0.2,
                                                 phrase="", margin=0.0,
                                                 reason="未命中", ranked=[])
        with patch("src.ai_pipeline.AiPipeline.handle") as handle:
            a.ask("今天天气不错", source="keyboard")
        handle.assert_not_called()


class AskAiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _assistant(self, has_screen: bool = True):
        with patch("src.engine.VoicePack.load_many",
                   return_value=fake_pack(has=has_screen)):
            return Assistant(make_cfg(self.root), no_play=True,
                             out=io.StringIO())

    def test_ask_ai_clears_busy_while_waiting(self):
        """关键：等模型期间 busy 必须是 False，否则 PTT 被丢弃、麦克风变聋。"""
        a = self._assistant()
        observed = {}

        def fake_handle(*args, **kwargs):
            observed["busy"] = a.busy
            from src.ai_pipeline import AiResult
            return AiResult(ok=True, text="ok", source="model")

        with patch.object(a.ai, "handle", side_effect=fake_handle):
            a.ask_ai("为什么慢", QueryKind.ANALYSIS, "query_laptime", False)
        self.assertFalse(observed["busy"])

    def test_ask_ai_reports_text(self):
        a = self._assistant()
        from src.ai_pipeline import AiResult
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=True, text="油够跑 6 圈",
                                                source="model")):
            rec = a.ask_ai("油还够吗", QueryKind.ANALYSIS, "query_fuel", False)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["ai_text"], "油够跑 6 圈")
        self.assertEqual(rec["ai_source"], "model")

    def test_ask_ai_handles_failure(self):
        a = self._assistant()
        from src.ai_pipeline import AiResult
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=False, reason="超时")):
            rec = a.ask_ai("为什么慢", QueryKind.ANALYSIS, None, False)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["reason"], "超时")

    def test_ask_ai_survives_pipeline_exception(self):
        a = self._assistant()
        with patch.object(a.ai, "handle", side_effect=RuntimeError("boom")):
            rec = a.ask_ai("为什么慢", QueryKind.ANALYSIS, None, False)
        self.assertFalse(rec["ok"])
        self.assertIn("boom", rec["reason"])

    def test_ask_ai_without_pipeline_returns_cleanly(self):
        with patch("src.engine.VoicePack.load_many", return_value=fake_pack()):
            a = Assistant(make_cfg(self.root, ai_enabled=False), no_play=True,
                          out=io.StringIO())
        rec = a.ask_ai("为什么慢", QueryKind.ANALYSIS, None, False)
        self.assertFalse(rec["ok"])
        self.assertIn("未启用", rec["reason"])

    def test_missing_screen_asset_does_not_crash(self):
        a = self._assistant(has_screen=False)
        from src.ai_pipeline import AiResult
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=True, text="x", source="model")):
            rec = a.ask_ai("为什么慢", QueryKind.ANALYSIS, None, False)
        self.assertTrue(rec["ok"])


if __name__ == '__main__':
    unittest.main()
