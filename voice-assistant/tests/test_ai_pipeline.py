import io
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.ai_pipeline import AiPipeline, AiResult, should_engage
from src.query_class import QueryKind


class EngageMatrixTests(unittest.TestCase):
    def test_safety_never_engages_in_any_mode(self):
        for mode in ("full", "analysis_key", "whitelist", "all"):
            with self.subTest(mode=mode):
                self.assertFalse(should_engage(QueryKind.SAFETY, mode, rule_ok=False))

    def test_analysis_engages_in_all_modes(self):
        for mode in ("full", "analysis_key", "whitelist", "all"):
            with self.subTest(mode=mode):
                self.assertTrue(should_engage(QueryKind.ANALYSIS, mode, rule_ok=False))

    def test_whitelist_does_not_engage_unknown(self):
        self.assertFalse(should_engage(QueryKind.UNKNOWN, "whitelist", rule_ok=False))

    def test_full_engages_unknown(self):
        self.assertTrue(should_engage(QueryKind.UNKNOWN, "full", rule_ok=False))

    def test_analysis_key_does_not_engage_unknown(self):
        # 没听懂就照实说没听懂，比花 3 秒猜更好
        self.assertFalse(should_engage(QueryKind.UNKNOWN, "analysis_key", rule_ok=False))

    def test_known_with_rule_ok_not_engaged_except_all_mode(self):
        for mode in ("full", "analysis_key", "whitelist"):
            with self.subTest(mode=mode):
                self.assertFalse(should_engage(QueryKind.KNOWN, mode, rule_ok=True))

    def test_all_mode_engages_known_even_with_rule_answer(self):
        # 「全部交给 AI」的核心区别：规则层答得上的 KNOWN 也要交出去，
        # 否则用户听到的仍是旧规则答案，档位等于没生效。
        self.assertTrue(should_engage(QueryKind.KNOWN, "all", rule_ok=True))
        self.assertTrue(should_engage(QueryKind.KNOWN, "all", rule_ok=False))

    def test_all_mode_engages_unknown(self):
        self.assertTrue(should_engage(QueryKind.UNKNOWN, "all", rule_ok=False))

    def test_all_mode_keeps_safety_and_operation_local(self):
        # 两条红线在任何档位都不放行；all 档也不例外。
        for kind in (QueryKind.SAFETY, QueryKind.OPERATION):
            with self.subTest(kind=kind.value):
                self.assertFalse(should_engage(kind, "all", rule_ok=False))

    def test_all_mode_matrix_is_exhaustive(self):
        """all 档完整矩阵：除 SAFETY/OPERATION 外全放行。"""
        expected = {QueryKind.SAFETY: False, QueryKind.OPERATION: False,
                    QueryKind.ANALYSIS: True, QueryKind.KNOWN: True,
                    QueryKind.UNKNOWN: True}
        for kind, want in expected.items():
            for rule_ok in (False, True):
                with self.subTest(kind=kind.value, rule_ok=rule_ok):
                    self.assertEqual(
                        should_engage(kind, "all", rule_ok=rule_ok), want)

    def test_known_without_rule_answer_full_engages(self):
        self.assertTrue(should_engage(QueryKind.KNOWN, "full", rule_ok=False))

    def test_known_without_rule_answer_whitelist_needs_intent(self):
        self.assertTrue(should_engage(QueryKind.KNOWN, "whitelist", rule_ok=False,
                                      intent="query_laptime",
                                      whitelist=["query_laptime"]))
        self.assertFalse(should_engage(QueryKind.KNOWN, "whitelist", rule_ok=False,
                                       intent="query_flags",
                                       whitelist=["query_laptime"]))

    def test_known_without_rule_answer_analysis_key_gated(self):
        self.assertFalse(should_engage(QueryKind.KNOWN, "analysis_key", rule_ok=False))


def cfg(**ai):
    base = {"ai": {"enabled": True, "mode": "whitelist",
                   "base_url": "http://x", "api_key": "k", "model": "m",
                   "timeout_s": 2.0, "max_tokens": 300,
                   "max_answer_chars": 60, "whitelist_intents": ["query_laptime"]}}
    base["ai"].update(ai)
    return base


def snap():
    from src.telemetry import Snapshot
    return Snapshot(connected=True, lap=12, laps_remain=5, fuel_level=18.4,
                    lap_last=83.5, lap_best=83.1, laps_of_fuel=6.2, position=4)


class HandleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _store(self):
        from src.ai_learn import LearnStore
        return LearnStore(pathlib.Path(self.tmp.name))

    def test_safety_returns_refused_without_calling_model(self):
        p = AiPipeline(cfg(), out=io.StringIO())
        with patch("src.ai_pipeline.LlmClient.ask") as ask:
            r = p.handle("为什么我被罚了", snap(), QueryKind.SAFETY, [])
        self.assertFalse(r.ok)
        ask.assert_not_called()

    def test_no_config_returns_refused(self):
        p = AiPipeline({"ai": {"enabled": True, "mode": "full"}},
                       out=io.StringIO())
        r = p.handle("为什么慢", snap(), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        self.assertIn("未配置", r.reason)

    def test_model_answer_passes_through_checks(self):
        p = AiPipeline(cfg(mode="full"), out=io.StringIO())
        reply = {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"],
                 "refused": False}
        with patch("src.ai_pipeline.LlmClient.ask", return_value=reply):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertEqual(r.source, "model")
        self.assertEqual(r.text, "油够跑 6.2 圈")

    def test_failed_check_downgrades(self):
        p = AiPipeline(cfg(mode="full"), out=io.StringIO())
        reply = {"answer": "油够跑 9.9 圈", "data_used": ["laps_of_fuel"]}
        with patch("src.ai_pipeline.LlmClient.ask", return_value=reply):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        self.assertIn("校验", r.reason)

    def test_model_refusal_is_respected(self):
        p = AiPipeline(cfg(mode="full"), out=io.StringIO())
        reply = {"answer": "", "refused": True, "reason": "读不到"}
        with patch("src.ai_pipeline.LlmClient.ask", return_value=reply):
            r = p.handle("x", snap(), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)

    def test_learned_template_is_used_before_model(self):
        from src.ai_learn import LearnStore
        store = self._store()
        store.add_template("油还够吗", {
            "params": ["laps_of_fuel"],
            "rules": [{"when": "true", "say": "够跑 {laps_of_fuel} 圈"}],
        })
        p = AiPipeline(cfg(mode="full"), store=store, out=io.StringIO())
        with patch("src.ai_pipeline.LlmClient.ask") as ask:
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertEqual(r.source, "learn")
        self.assertEqual(r.text, "够跑 6.2 圈")
        ask.assert_not_called()

    def test_unfillable_template_falls_back_to_model(self):
        store = self._store()
        store.add_template("油还够吗", {
            "params": ["laps_of_fuel"],
            "rules": [{"when": "true", "say": "够跑 {laps_of_fuel} 圈"}],
        })
        p = AiPipeline(cfg(mode="full"), store=store, out=io.StringIO())
        from src.telemetry import Snapshot
        empty = Snapshot(connected=True)      # 没有 laps_of_fuel
        reply = {"answer": "读不到", "data_used": []}
        with patch("src.ai_pipeline.LlmClient.ask", return_value=reply):
            r = p.handle("油还够吗", empty, QueryKind.ANALYSIS, [])
        self.assertEqual(r.source, "model")

    def test_breaker_opens_after_three_timeouts(self):
        p = AiPipeline(cfg(mode="full"), out=io.StringIO())
        from src.llm_engine import LlmError
        with patch("src.ai_pipeline.LlmClient.ask",
                   side_effect=LlmError("timeout")):
            for _ in range(3):
                p.handle("x", snap(), QueryKind.ANALYSIS, [])
        self.assertEqual(p.breaker.state, "open")
        self.assertFalse(p.available)

    def test_breaker_open_short_circuits(self):
        p = AiPipeline(cfg(mode="full"), out=io.StringIO())
        for _ in range(3):
            p.breaker.record(2000, timed_out=True)
        with patch("src.ai_pipeline.LlmClient.ask") as ask:
            r = p.handle("x", snap(), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        ask.assert_not_called()

    def test_tts_failure_does_not_lose_text(self):
        c = cfg(mode="full")
        c["ai"]["tts"] = {"adapter": "openai", "base_url": "http://x",
                          "api_key": "k"}
        p = AiPipeline(c, out=io.StringIO())
        reply = {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"]}
        with patch("src.ai_pipeline.LlmClient.ask", return_value=reply), \
             patch("src.ai_pipeline.TtsEngine.synth", side_effect=Exception("boom")):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertIn("tts", r.checks)


class StatusTests(unittest.TestCase):
    def test_unconfigured_reports_unavailable(self):
        p = AiPipeline({"ai": {"enabled": True}}, out=io.StringIO())
        s = p.status()
        self.assertFalse(s["configured"])
        self.assertFalse(s["available"])

    def test_configured_reports_available(self):
        p = AiPipeline(cfg(), out=io.StringIO())
        s = p.status()
        self.assertTrue(s["configured"])
        self.assertTrue(s["available"])
        self.assertEqual(s["mode"], "whitelist")


if __name__ == '__main__':
    unittest.main()


class OperationEngageTests(unittest.TestCase):
    """操作请求任何档位都不放给模型。

    软件不执行只是没帮上忙；模型给出「前翼调软两格」这种建议，
    在 200km/h 的弯里照做是要出事的。
    """

    def test_operation_never_engages_in_any_mode(self):
        for mode in ("full", "analysis_key", "whitelist", "all"):
            with self.subTest(mode=mode):
                self.assertFalse(
                    should_engage(QueryKind.OPERATION, mode, rule_ok=False))

    def test_operation_is_blocked_alongside_safety(self):
        for kind in (QueryKind.SAFETY, QueryKind.OPERATION):
            with self.subTest(kind=kind.value):
                self.assertFalse(should_engage(kind, "full", rule_ok=False))

    def test_analysis_still_engages(self):
        # 确认拆分没有误伤：分析类照旧放行
        self.assertTrue(should_engage(QueryKind.ANALYSIS, "full", rule_ok=False))


class MalformedPayloadTests(unittest.TestCase):
    """模型返回坏数据不能让流水线崩，只能降级。

    实测：payload 是 list 时 pipeline 抛 AttributeError。
    虽然 LlmClient.ask 已保证 dict，但流水线是汇合点，不该假设上游永远对。
    """

    def _pipeline(self):
        return AiPipeline({"ai": {"enabled": True, "mode": "full",
                                  "base_url": "http://x", "api_key": "k",
                                  "model": "m", "timeout_s": 2.0,
                                  "max_tokens": 300, "max_answer_chars": 60,
                                  "whitelist_intents": [],
                                  "tts": {"adapter": "off"}}},
                          out=io.StringIO())

    def test_non_dict_payload_degrades(self):
        from src.telemetry import Snapshot
        for bad in (["a"], "text", 42, None, ("t",)):
            with self.subTest(bad=type(bad).__name__):
                p = self._pipeline()
                with patch.object(p.client, "ask", return_value=bad):
                    r = p.handle("为什么慢", Snapshot(connected=True),
                                 QueryKind.ANALYSIS, [])
                self.assertFalse(r.ok)
                self.assertIn("格式异常", r.reason)


class LiveNotifyTests(unittest.TestCase):
    """Overlay 时序：答案必须在 TTS 之前写进 live。

    真实链路里 TTS 是同步阻塞的（播完才返回），晚写等于 Overlay 落后
    一整句的时长，「实时状态」就名不副实。
    """

    def _pipeline(self, events):
        from src.ai_pipeline import AiPipeline
        c = {"ai": {"enabled": True, "mode": "full",
                    "base_url": "http://x", "api_key": "k", "model": "m",
                    "timeout_s": 3.0, "max_tokens": 300,
                    "max_answer_chars": 60, "whitelist_intents": [],
                    "tts": {"adapter": "openai", "base_url": "http://t",
                            "api_key": "k"}}}
        player = MagicMock()
        player.enabled = True
        player.play_bytes.side_effect = lambda blob: events.append(("player", blob))
        p = AiPipeline(c, out=io.StringIO(), player=player,
                       on_live=lambda text: events.append(("live", text)))
        return p

    def test_live_is_written_before_tts_playback(self):
        events = []
        p = self._pipeline(events)
        reply = {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"]}
        with patch.object(p.client, "ask", return_value=reply), \
             patch.object(p.tts, "synth", side_effect=lambda text: text.encode()):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertEqual([e[0] for e in events], ["live", "player"])
        self.assertEqual(events[0][1], "油够跑 6.2 圈")

    def test_live_not_written_when_answer_fails(self):
        from src.llm_engine import LlmError
        events = []
        p = self._pipeline(events)
        with patch.object(p.client, "ask", side_effect=LlmError("timeout")):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        # 失败态由 engine 写 error；流水线只负责成功出声前的 answer。
        self.assertEqual(events, [])

    def test_live_callback_exception_does_not_lose_answer(self):
        # Overlay 是装饰层：回调里抛异常，答案也不能丢。
        from src.ai_pipeline import AiPipeline
        c = {"ai": {"enabled": True, "mode": "full",
                    "base_url": "http://x", "api_key": "k", "model": "m",
                    "timeout_s": 3.0, "max_tokens": 300,
                    "max_answer_chars": 60, "whitelist_intents": []}}
        def broken_ui(text):
            raise RuntimeError("ui")

        p = AiPipeline(c, out=io.StringIO(), on_live=broken_ui)
        reply = {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"]}
        with patch.object(p.client, "ask", return_value=reply):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "油够跑 6.2 圈")
