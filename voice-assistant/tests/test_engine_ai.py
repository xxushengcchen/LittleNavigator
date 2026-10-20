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


class RefusalMessageTests(unittest.TestCase):
    """三类拒绝话术必须不同 —— 「设置成软胎」听到「没听清」是最糟的体验。"""

    def test_operation_gets_capability_message(self):
        from src.engine import NO_CAR_CONTROL
        a = Assistant.__new__(Assistant)
        self.assertEqual(Assistant._refusal_for(QueryKind.OPERATION),
                         NO_CAR_CONTROL)

    def test_safety_gets_policy_message(self):
        from src.engine import REFUSE_CANT
        self.assertEqual(Assistant._refusal_for(QueryKind.SAFETY), REFUSE_CANT)

    def test_unknown_gets_heard_message(self):
        from src.engine import REFUSE_HEARD
        for kind in (QueryKind.UNKNOWN, QueryKind.ANALYSIS, QueryKind.KNOWN):
            with self.subTest(kind=kind.value):
                self.assertEqual(Assistant._refusal_for(kind), REFUSE_HEARD)

    def test_operation_message_differs_from_safety(self):
        self.assertNotEqual(Assistant._refusal_for(QueryKind.OPERATION),
                            Assistant._refusal_for(QueryKind.SAFETY))

    def _ask_with_pack(self, text: str, available: set[str]):
        """构造一个只「有」available 里这些素材的引擎，跑一次提问。"""
        pack = fake_pack(has=False)
        pack.has.side_effect = lambda m: m in available
        with patch("src.engine.VoicePack.load_many", return_value=pack):
            a = Assistant(make_cfg(pathlib.Path(tempfile.gettempdir())),
                          no_play=True, out=io.StringIO())
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(intent=None, score=0.2,
                                                 phrase="", margin=0.0,
                                                 reason="未命中", ranked=[])
        played = []
        with patch.object(a, "_say", side_effect=lambda m: played.append(m)):
            rec = a.ask(text, source="keyboard")
        return rec, played

    def test_car_control_request_selects_capability_message(self):
        # 驾驶建议类没有专用话术，走通用的「做不到」
        rec, played = self._ask_with_pack("这个弯该几档过", {"AST_NO_CAR_CONTROL"})
        self.assertEqual(rec["kind"], "operation")
        self.assertEqual(played, ["AST_NO_CAR_CONTROL"])

    def test_compound_request_selects_compound_message(self):
        # 配方请求有专用话术：通用「做不到」说不清为什么
        rec, played = self._ask_with_pack("设置成软胎", {"AST_CC_COMPOUND"})
        self.assertEqual(rec["kind"], "operation")
        self.assertEqual(played, ["AST_CC_COMPOUND"])

    def test_penalty_question_selects_policy_message(self):
        rec, played = self._ask_with_pack("为什么我被罚了", {"AST_REFUSE_CANT"})
        self.assertEqual(rec["kind"], "safety")
        self.assertEqual(played, ["AST_REFUSE_CANT"])

    def test_missing_capability_audio_falls_back_not_silent(self):
        # 素材还没重建时，宁可播一句措辞不完美的话，也不能让用户面对沉默
        rec, played = self._ask_with_pack("设置成软胎", {"AST_REFUSE_CANT"})
        self.assertEqual(rec["kind"], "operation")
        self.assertEqual(played, ["AST_REFUSE_CANT"])

    def test_missing_capability_and_policy_audio_falls_back_to_heard(self):
        rec, played = self._ask_with_pack("设置成软胎", {"AST_REFUSE_HEARD"})
        self.assertEqual(played, ["AST_REFUSE_HEARD"])

    def test_all_refusal_audio_missing_is_not_silent_crash(self):
        rec, played = self._ask_with_pack("设置成软胎", set())
        self.assertEqual(played, [])
        self.assertFalse(rec["ok"])       # 仍然返回记录，不抛异常


# ---------------------------------------------------------------- v1.5.1 新增
def make_assistant(root, ai_enabled=True, **ai):
    """构造一个不播真声的引擎；VoicePack 用最小替身。"""
    with patch("src.engine.VoicePack.load_many", return_value=fake_pack()):
        return Assistant(make_cfg(root, ai_enabled, **ai), no_play=True,
                         out=io.StringIO())


def hit(intent="query_fuel"):
    """短语库命中的替身：intent 非空 → classify 判 KNOWN。"""
    return MagicMock(intent=intent, score=0.9, phrase="油还够吗",
                     margin=0.2, reason="命中短语", ranked=[])


class AckPickerTests(unittest.TestCase):
    """洗牌袋抽取：一轮内不重复，抽完自动跨轮重置。"""

    def test_msg_ids_are_ast_ack_01_to_20(self):
        from src.ack_phrases import ACK_MSG_IDS
        self.assertEqual(len(ACK_MSG_IDS), 20)
        self.assertEqual(ACK_MSG_IDS[0], "AST_ACK_01")
        self.assertEqual(ACK_MSG_IDS[-1], "AST_ACK_20")

    def test_one_round_has_no_duplicates(self):
        import random as _random
        from src.ack_phrases import ACK_MSG_IDS, AckPicker
        p = AckPicker(rng=_random.Random(7))
        got = [p.pick() for _ in range(20)]
        self.assertEqual(len(set(got)), 20)
        self.assertEqual(set(got), set(ACK_MSG_IDS))

    def test_reset_starts_a_fresh_round(self):
        import random as _random
        from src.ack_phrases import ACK_MSG_IDS, AckPicker
        p = AckPicker(rng=_random.Random(7))
        p.pick()
        p.pick()
        p.reset()                       # 跨轮重置：袋子清空重新洗
        got = [p.pick() for _ in range(20)]
        self.assertEqual(set(got), set(ACK_MSG_IDS))

    def test_available_filter_limits_pool_and_refills(self):
        import random as _random
        from src.ack_phrases import AckPicker
        p = AckPicker(rng=_random.Random(1))
        avail = {"AST_ACK_01", "AST_ACK_02"}
        got = [p.pick(lambda m: m in avail) for _ in range(4)]
        self.assertEqual(set(got), avail)      # 只有两条就循环洗这两条，不卡死

    def test_no_available_assets_returns_none(self):
        from src.ack_phrases import AckPicker
        p = AckPicker()
        self.assertIsNone(p.pick(lambda m: False))


class LiveStateTests(unittest.TestCase):
    """Overlay 数据源：heard / state / answer / error 的迁移。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def test_live_starts_idle(self):
        a = make_assistant(self.root, ai_enabled=False)
        self.assertEqual(a.live, {"heard": "", "state": "idle",
                                  "answer": "", "error": ""})

    def test_set_heard_trims_and_records(self):
        a = make_assistant(self.root, ai_enabled=False)
        a.set_heard("  油还够吗 ")
        self.assertEqual(a.live["heard"], "油还够吗")

    def test_ask_ai_entry_is_thinking(self):
        a = make_assistant(self.root)
        seen = {}
        from src.ai_pipeline import AiResult

        def fake_handle(*args, **kwargs):
            seen["state"] = a.live["state"]
            return AiResult(ok=True, text="油够", source="model")

        with patch.object(a.ai, "handle", side_effect=fake_handle):
            a.ask_ai("油还够吗", QueryKind.KNOWN, "query_fuel", False)
        self.assertEqual(seen["state"], "thinking")

    def test_ask_ai_success_sets_answer(self):
        a = make_assistant(self.root)
        from src.ai_pipeline import AiResult
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=True, text="油够跑六圈",
                                                source="model")):
            a.ask_ai("油还够吗", QueryKind.KNOWN, "query_fuel", False)
        self.assertEqual(a.live["state"], "answer")
        self.assertEqual(a.live["answer"], "油够跑六圈")
        self.assertEqual(a.live["error"], "")

    def test_ask_ai_failure_sets_error(self):
        a = make_assistant(self.root)
        from src.ai_pipeline import AiResult
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=False, reason="超时")):
            rec = a.ask_ai("为什么慢", QueryKind.ANALYSIS, None, False)
        self.assertFalse(rec["ok"])
        self.assertEqual(a.live["state"], "error")
        self.assertEqual(a.live["error"], "超时")

    def test_ask_sets_thinking_when_question_goes_to_ai(self):
        # mode=full、识别不到的句子会转交 AI：ask() 入口就该是 thinking。
        a = make_assistant(self.root, mode="full")
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(intent=None, score=0.2,
                                                 phrase="", margin=0.0,
                                                 reason="未命中", ranked=[])
        rec = a.ask("今天天气不错", source="keyboard")
        self.assertTrue(rec["ai_eligible"])
        self.assertEqual(a.live["state"], "thinking")

    def test_rule_answer_sets_answer_before_playback(self):
        a = make_assistant(self.root, ai_enabled=False)
        a.matcher = MagicMock()
        a.matcher.match.return_value = hit()
        a.pack.has.return_value = True
        a.pack.path_of.return_value = self.root / "x.wav"
        a._pick_existing = MagicMock(return_value=MagicMock(text="油量十八升"))
        live_at_play = {}

        def fake_play_many(paths, gap_ms=60, on_start=None):
            live_at_play.update(a.live)
            if on_start:
                on_start()
            return 0.0

        a.player.play_many = MagicMock(side_effect=fake_play_many)
        from src import router
        with patch("src.engine.router.route",
                   return_value=router.Decision(["AST_FUEL"], "油量 18.4L")):
            rec = a.ask("油还够吗", source="keyboard")
        self.assertTrue(rec["ok"])
        self.assertEqual(live_at_play["state"], "answer")
        self.assertEqual(live_at_play["answer"], "油量十八升")


class AllModeRoutingTests(unittest.TestCase):
    """all 档：规则答得上也不播，转交上层走 AI；AI 不可用退回规则路径。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _engine(self, mode="all", available=True):
        a = make_assistant(self.root, mode=mode)
        if not available:
            for _ in range(3):
                a.ai.breaker.record(2000, timed_out=True)
        a.matcher = MagicMock()
        a.matcher.match.return_value = hit()
        return a

    def _decision(self):
        from src import router
        return router.Decision(["AST_FUEL", "AST_LITRE"], "油量 18.4L")

    def test_all_mode_holds_rule_answer_for_ai(self):
        a = self._engine()
        with patch("src.engine.router.route", return_value=self._decision()), \
             patch.object(a, "play_decision") as play:
            rec = a.ask("油还够吗", source="keyboard")
        play.assert_not_called()
        self.assertTrue(rec["ai_eligible"])
        self.assertEqual(rec["fallback_plan"], ["AST_FUEL", "AST_LITRE"])
        self.assertEqual(rec["fallback_text"], "油量 18.4L")
        self.assertEqual(a.live["state"], "thinking")

    def test_all_mode_falls_back_to_rule_when_ai_unavailable(self):
        a = self._engine(available=False)
        with patch("src.engine.router.route", return_value=self._decision()), \
             patch.object(a, "play_decision", return_value=True) as play:
            rec = a.ask("油还够吗", source="keyboard")
        play.assert_called_once()
        self.assertNotIn("ai_eligible", rec)

    def test_other_modes_still_play_rule_answer(self):
        a = self._engine(mode="full")
        with patch("src.engine.router.route", return_value=self._decision()), \
             patch.object(a, "play_decision", return_value=True) as play:
            a.ask("油还够吗", source="keyboard")
        play.assert_called_once()

    def test_all_mode_keeps_safety_and_operation_local(self):
        # SAFETY/OPERATION 永远留在本地：全量接管档也不能把红线交出去。
        a = self._engine()
        self.assertFalse(a._ai_should_engage(QueryKind.SAFETY, "为什么被罚"))
        self.assertFalse(a._ai_should_engage(QueryKind.OPERATION, "设置成软胎"))


class AiFallbackPlaybackTests(unittest.TestCase):
    """AI 失败时回退播规则答案：不能静默失败。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _engine(self):
        a = make_assistant(self.root)
        a.pack.has.return_value = True
        a.pack.path_of.return_value = self.root / "x.wav"
        a._pick_existing = MagicMock(return_value=MagicMock(text="油量十八升"))
        self.played = []

        def fake_play_many(paths, gap_ms=60, on_start=None):
            self.played.append(list(paths))
            if on_start:
                on_start()
            return 0.0

        a.player.play_many = MagicMock(side_effect=fake_play_many)
        return a

    def test_ai_failure_plays_fallback_and_marks_record(self):
        from src.ai_pipeline import AiResult
        a = self._engine()
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=False, reason="超时")):
            rec = a.ask_ai("油还够吗", QueryKind.KNOWN, "query_fuel", True,
                           extra={"fallback_plan": ["AST_FUEL"],
                                  "fallback_text": "油量 18.4L"})
        self.assertTrue(rec["ok"])
        self.assertTrue(rec["ai_fallback"])
        self.assertEqual(rec["ai_reason"], "超时")
        self.assertEqual(rec["plan"], ["AST_FUEL"])
        self.assertEqual(rec["text"], "油量十八升")
        self.assertEqual(self.played, [[self.root / "x.wav"]])
        self.assertEqual(a.live["state"], "answer")
        self.assertEqual(a.live["answer"], "油量十八升")

    def test_ai_failure_without_fallback_stays_failed(self):
        from src.ai_pipeline import AiResult
        a = self._engine()
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=False, reason="超时")):
            rec = a.ask_ai("为什么慢", QueryKind.ANALYSIS, None, False)
        self.assertFalse(rec["ok"])
        self.assertNotIn("ai_fallback", rec)
        self.assertEqual(self.played, [])
        self.assertEqual(a.live["state"], "error")

    def test_fallback_playback_failure_is_not_reported_as_success(self):
        from src.ai_pipeline import AiResult
        a = self._engine()
        a.pack.has.return_value = False       # 兜底素材也缺
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=False, reason="超时")):
            rec = a.ask_ai("油还够吗", QueryKind.KNOWN, "query_fuel", True,
                           extra={"fallback_plan": ["AST_MISSING"],
                                  "fallback_text": "x"})
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["reason"], "超时")


class AckPlaybackTests(unittest.TestCase):
    """等待语音：请求模型前先播一句，失败静默、不挡主流程。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _engine(self):
        a = make_assistant(self.root)
        a.pack.has.side_effect = lambda m: m.startswith("AST_ACK_")
        a.pack.path_of.return_value = self.root / "ack.wav"
        a._pick_existing = MagicMock(return_value=MagicMock(text="稍等喔"))
        return a

    def _run(self, a, events):
        from src.ai_pipeline import AiResult

        def fake_handle(*args, **kwargs):
            events.append("handle")
            return AiResult(ok=True, text="油够", source="model")

        with patch.object(a.ai, "handle", side_effect=fake_handle):
            return a.ask_ai("油还够吗", QueryKind.KNOWN, "query_fuel", False)

    def test_ack_is_played_before_model_call(self):
        a = self._engine()
        events = []
        a.player.play = MagicMock(side_effect=lambda path: events.append("ack"))
        self._run(a, events)
        self.assertEqual(events, ["ack", "handle"])

    def test_ack_can_be_disabled(self):
        a = self._engine()
        a.cfg["ai"]["ack"] = {"enabled": False}
        events = []
        a.player.play = MagicMock(side_effect=lambda path: events.append("ack"))
        self._run(a, events)
        self.assertEqual(events, ["handle"])

    def test_ack_skips_when_assets_missing(self):
        a = self._engine()
        a.pack.has.side_effect = None
        a.pack.has.return_value = False
        events = []
        a.player.play = MagicMock(side_effect=lambda path: events.append("ack"))
        self._run(a, events)
        self.assertEqual(events, ["handle"])

    def test_ack_playback_failure_does_not_block_ai(self):
        a = self._engine()
        a.player.play = MagicMock(side_effect=RuntimeError("device gone"))
        events = []
        self._run(a, events)
        self.assertEqual(events, ["handle"])


# ---------------------------------------------------------------- v1.5.2
def playable_engine(root, *, mode="all", global_stop_local=True,
                    available=True, ai_enabled=True):
    """带「可播语音包 + 可观测播放器」的引擎，用来断言「有没有出声」。"""
    a = make_assistant(root, ai_enabled=ai_enabled, mode=mode,
                       global_stop_local=global_stop_local)
    if ai_enabled and not available:
        for _ in range(3):
            a.ai.breaker.record(2000, timed_out=True)
    a.pack.has.return_value = True
    a.pack.texts.return_value = ["油量十八升"]
    a.pack.path_of.return_value = root / "x.wav"
    a._pick_existing = MagicMock(return_value=MagicMock(text="油量十八升"))
    a.player.play_many = MagicMock(return_value=0.0)
    a.player.play = MagicMock(return_value=0.0)
    return a


class NoDoubleResponseTests(unittest.TestCase):
    """问题 3：AI-eligible 的路径在转交前一个字都不许播。

    用户实测「本地拒绝」和「AI 回答」连着响——先听到「没听清」，
    一两秒后再听到真正的回答。修法是：凡是 ai_eligible 就闭嘴交出去。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _miss(self, a):
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(intent=None, score=0.2,
                                                 phrase="", margin=0.0,
                                                 reason="未命中", ranked=[])
        return a

    def test_intent_none_does_not_speak_before_ai(self):
        # global_stop_local=False：证明「不出声」来自 ai_eligible，不是全局静音。
        a = self._miss(playable_engine(self.root, global_stop_local=False))
        with patch.object(a, "_say") as say:
            rec = a.ask("今天天气不错", source="keyboard")
        say.assert_not_called()
        self.assertTrue(rec["ai_eligible"])
        self.assertEqual(a.live["state"], "thinking")

    def test_rule_cannot_answer_does_not_speak_before_ai(self):
        a = playable_engine(self.root, global_stop_local=False)
        a.matcher = MagicMock()
        a.matcher.match.return_value = hit()
        from src import router
        with patch("src.engine.router.route",
                   return_value=router.Decision([], "遥测里没有油量")), \
             patch.object(a, "_say") as say:
            rec = a.ask("油还够吗", source="keyboard")
        say.assert_not_called()
        self.assertTrue(rec["ai_eligible"])

    def test_refusal_still_plays_when_ai_not_eligible(self):
        # 安全类是红线：不转 AI，本地拒绝必须照常出声（非全局静音时）。
        a = playable_engine(self.root, global_stop_local=False)
        with patch.object(a, "_say") as say:
            rec = a.ask("为什么我被罚了", source="keyboard")
        say.assert_called_once()
        self.assertFalse(rec.get("ai_eligible"))

    def test_only_one_response_across_ask_and_ask_ai(self):
        a = self._miss(playable_engine(self.root, global_stop_local=False))
        # 只让等待语音素材「存在」：这样「看屏幕」提示不会额外插一句，
        # 断言才能干净地只盯「本地拒绝有没有先响」。
        a.pack.has.side_effect = lambda m: m.startswith("AST_ACK_")
        from src.ai_pipeline import AiResult
        played: list = []
        a.player.play_many.side_effect = lambda *args, **kwargs: played.append(args)
        with patch.object(a, "_say") as say, \
             patch.object(a.ai, "handle",
                          return_value=AiResult(ok=True, text="今天适合保胎",
                                                source="model")) as handle:
            rec = a.ask("现在该怎么保胎", source="keyboard")
            self.assertTrue(rec["ai_eligible"])
            ai_rec = a.ask_ai("现在该怎么保胎", QueryKind.UNKNOWN, None,
                              False, source="keyboard", extra=rec)
        self.assertTrue(ai_rec["ok"])
        self.assertEqual(handle.call_count, 1)           # AI 只出一次
        say.assert_not_called()                          # 本地一次都没响
        self.assertEqual(played, [])

    def test_no_ai_configured_still_speaks_refusal(self):
        # 没配 AI 就没有「转交」，本地拒答必须出声，否则用户完全没反馈。
        a = playable_engine(self.root, ai_enabled=False)
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(intent=None, score=0.2,
                                                 phrase="", margin=0.0,
                                                 reason="未命中", ranked=[])
        with patch.object(a, "_say") as say:
            rec = a.ask("今天天气不错", source="keyboard")
        say.assert_called_once()
        self.assertFalse(rec.get("ai_eligible"))


class GlobalStopLocalTests(unittest.TestCase):
    """问题 3 后半：全局交给 AI 时本地语音全停，但改车确认与 ack 保留。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _rule_decision(self):
        from src import router
        return router.Decision(["AST_FUEL"], "油量 18.4L")

    def test_flag_only_bites_in_all_mode(self):
        full = playable_engine(self.root, mode="full", global_stop_local=True)
        self.assertFalse(full.global_stop_local)
        all_mode = playable_engine(self.root, mode="all", global_stop_local=True)
        self.assertTrue(all_mode.global_stop_local)
        all_off = playable_engine(self.root, mode="all", global_stop_local=False)
        self.assertFalse(all_off.global_stop_local)

    def test_flag_requires_ai_pipeline(self):
        # AI 关了却留着 mode=all：不能再停本地播报，否则软件全程沉默。
        a = playable_engine(self.root, ai_enabled=False)
        self.assertFalse(a.global_stop_local)

    def test_rule_answer_is_text_only(self):
        # AI 熔断（不可用）→ 走规则兜底；全局静音时只写文字不播。
        a = playable_engine(self.root, available=False)
        a.matcher = MagicMock()
        a.matcher.match.return_value = hit()
        with patch("src.engine.router.route", return_value=self._rule_decision()):
            rec = a.ask("油还够吗", source="keyboard")
        self.assertTrue(rec["ok"])
        self.assertTrue(rec.get("silent"))
        self.assertEqual(rec["text"], "油量十八升")
        self.assertEqual(a.live["answer"], "油量十八升")
        a.player.play_many.assert_not_called()

    def test_ai_failure_fallback_is_text_only(self):
        a = playable_engine(self.root)
        from src.ai_pipeline import AiResult
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=False, reason="超时")):
            rec = a.ask_ai("油还够吗", QueryKind.KNOWN, "query_fuel", True,
                           extra={"fallback_plan": ["AST_FUEL"],
                                  "fallback_text": "油量 18.4L"})
        self.assertTrue(rec["ok"])
        self.assertTrue(rec["ai_fallback"])
        self.assertTrue(rec.get("silent"))
        self.assertEqual(a.live["answer"], "油量十八升")
        a.player.play_many.assert_not_called()

    def test_see_screen_prompt_is_silent(self):
        a = playable_engine(self.root)
        from src.ai_pipeline import AiResult
        with patch.object(a.ai, "handle",
                          return_value=AiResult(ok=True, text="今天适合保胎",
                                                source="model",
                                                checks={"tts": "fail"})), \
             patch.object(a, "_say") as say:
            rec = a.ask_ai("现在该怎么保胎", QueryKind.ANALYSIS, None, False)
        self.assertTrue(rec["ok"])
        say.assert_not_called()
        a.player.play.assert_called_once()        # 等待语音保留
        self.assertEqual(a.live["state"], "answer")

    def test_safety_refusal_shows_text_without_voice(self):
        a = playable_engine(self.root)
        with patch.object(a, "_say") as say:
            rec = a.ask("为什么我被罚了", source="keyboard")
        say.assert_not_called()
        a.player.play_many.assert_not_called()
        self.assertFalse(rec["ok"])
        self.assertFalse(rec.get("ai_eligible"))
        self.assertEqual(a.live["answer"], "油量十八升")   # 替身包里的文案

    def test_car_control_feedback_is_kept(self):
        a = playable_engine(self.root)
        with patch.object(a, "_say") as say:
            a._say_with_fallback("AST_CC_TYRE", force=True)
        say.assert_called_once_with("AST_CC_TYRE")

    def test_local_utterance_without_force_is_muted(self):
        a = playable_engine(self.root)
        with patch.object(a, "_say") as say:
            a._say_with_fallback("AST_REFUSE_CANT")
        say.assert_not_called()

    def test_flag_does_not_mute_other_modes(self):
        a = playable_engine(self.root, mode="full", global_stop_local=True)
        with patch.object(a, "_say") as say:
            a.ask("为什么我被罚了", source="keyboard")
        say.assert_called_once()

    def test_operation_confirmation_path_keeps_voice(self):
        # 改车成功/失败的话术都走 force=True，全局静音下仍要出声。
        a = playable_engine(self.root)
        calls = []
        with patch.object(a, "_say", side_effect=lambda m: calls.append(m)):
            a._say_with_fallback("AST_CC_FAIL", force=True)
        self.assertEqual(calls, ["AST_CC_FAIL"])
