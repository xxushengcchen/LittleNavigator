"""v1.6.2 审计批 2：A06/A09/A10/A11/A12/A19 回归测试。

全部纯内存：
  · LLM 只替换 ``LlmClient.ask`` / ``_post_json``，不访问网络；
  · TTS 用返回固定字节的假实现，播放器用 MagicMock 收集音频；
  · 学习库一律在 tmp_path 里新建；
  · A12 用「假 LLM 的 ask 回调里同步触发取消」制造确定时序，不 sleep。
"""
from __future__ import annotations

import collections
import io
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.ai_learn import LearnStore, fill_template, validate_template
from src.ai_pipeline import AiPipeline, should_engage
from src.answer_checks import check_numbers, run_all
from src.engine import Assistant
from src.query_class import QueryKind, classify
from src.telemetry import Snapshot


def snap():
    return Snapshot(connected=True, lap=12, laps_remain=5, fuel_level=18.4,
                    lap_last=83.512, lap_best=83.104, laps_of_fuel=6.2,
                    position=4)


def ai_cfg(**ai):
    base = {"ai": {"enabled": True, "mode": "all",
                   "base_url": "http://x", "api_key": "k", "model": "m",
                   "timeout_s": 2.0, "max_tokens": 300,
                   "max_answer_chars": 60, "whitelist_intents": [],
                   "tts": {"adapter": "off"}}}
    base["ai"].update(ai)
    return base


class FailingTts:
    """假 TTS：记录 synth 调用，返回固定字节，不碰网络。"""

    def __init__(self, on_synth=None):
        self.synths: list[str] = []
        self._on_synth = on_synth

    def synth(self, text: str) -> bytes:
        self.synths.append(text)
        if self._on_synth is not None:
            self._on_synth(text)
        return b"fake-audio"


# ---------------------------------------------------------------- A06


class LearnTargetGuardTests(unittest.TestCase):
    """A06：学习相似匹配不能把前/后车、左右轮、否定、时间目标互换。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LearnStore(pathlib.Path(self.tmp.name))

    def test_front_and_behind_are_not_swapped(self):
        self.store.add_knowledge("前车上一圈圈速多少", "前车 83.2 秒")
        self.assertIsNone(self.store.match("后车上一圈圈速多少"))
        self.assertIsNotNone(self.store.match("前车上一圈圈速多少啊"))

    def test_left_and_right_wheels_are_not_swapped(self):
        self.store.add_knowledge("左前轮胎温多少", "左前 82 度")
        self.assertIsNone(self.store.match("右前轮胎温多少"))
        self.assertIsNotNone(self.store.match("左前轮胎温多少啊"))

    def test_negation_is_not_dropped(self):
        self.store.add_knowledge("需要进站吗", "需要")
        self.assertIsNone(self.store.match("不需要进站吗"))

    def test_lap_time_target_is_not_swapped(self):
        self.store.add_knowledge("上一圈圈速多少", "83.5 秒")
        self.assertIsNone(self.store.match("下一圈圈速多少"))

    def test_pipeline_does_not_answer_other_target_from_learned_row(self):
        self.store.add_template("前车圈速多少", {
            "params": ["laps_of_fuel"],
            "rules": [{"when": "true", "say": "够跑 {laps_of_fuel} 圈"}],
        })
        pipe = AiPipeline(ai_cfg(), store=self.store, out=io.StringIO())
        self.assertIsNone(pipe._from_learn("后车圈速多少", snap(), [],
                                           QueryKind.ANALYSIS))


# ---------------------------------------------------------------- A09


class ReviewFeedbackIdTests(unittest.TestCase):
    """A09：复盘 ok 入库后，bad 必须落到真正条目并且两次后停用。"""

    REVIEW = {"question": "蓝旗什么意思", "answer": "后方有快车要放行",
              "dynamic": False, "review_id": "rv-audit-0001"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.store = LearnStore(self.root)

    def test_one_ok_then_two_bad_disable_actual_entry(self):
        self.assertEqual(self.store.add_from_review(dict(self.REVIEW)), "knowledge")
        self.assertTrue(self.store.mark("rv-audit-0001", ok=False))
        self.assertTrue(self.store.mark("rv-audit-0001", ok=False))
        rows = self.store.stats()["knowledge"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["disabled"])
        self.assertIsNone(self.store.match("蓝旗什么意思"))

    def test_repeated_ok_is_idempotent(self):
        self.assertEqual(self.store.add_from_review(dict(self.REVIEW)), "knowledge")
        self.assertEqual(self.store.add_from_review(dict(self.REVIEW)), "knowledge")
        self.assertEqual(len(self.store.stats()["knowledge"]), 1)

    def test_absent_mark_returns_not_found(self):
        self.assertFalse(self.store.mark("no-such-id", ok=False))

    def test_mark_accepts_entry_id_as_before(self):
        entry = self.store.add_knowledge("蓝旗什么意思", "后方有快车要放行")
        self.assertTrue(self.store.mark(entry["id"], ok=False))

    def test_learned_answer_carries_source_entry_id(self):
        entry = self.store.add_knowledge("蓝旗什么意思", "后方有快车要放行")
        pipe = AiPipeline(ai_cfg(), store=self.store, out=io.StringIO())
        result = pipe._from_learn("蓝旗什么意思", snap(), [],
                                  QueryKind.KNOWN)
        self.assertIsNotNone(result)
        self.assertEqual(result.evidence.get("learn_entry_id"), entry["id"])

    def test_reviewing_learned_answer_does_not_duplicate_entry(self):
        entry = self.store.add_knowledge("蓝旗什么意思", "后方有快车要放行")
        record = {"question": "蓝旗什么意思", "answer": "后方有快车要放行",
                  "dynamic": False, "review_id": entry["id"],
                  "source_id": entry["id"]}
        self.assertEqual(self.store.add_from_review(record), "knowledge")
        rows = self.store.stats()["knowledge"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], entry["id"])
        self.assertEqual(rows[0]["ok"], 1)


# ---------------------------------------------------------------- A10


class NumberGateBypassTests(unittest.TestCase):
    """A10：六个数字闸绕过边界逐条封死，正向引用仍合法。"""

    CTX = {"now": {"lap_last": 83.512, "laps_remain": 5, "fuel_level": 18.4,
                   "position": 4, "relative_humidity": 0.62},
           "derived": {"laps_of_fuel": 6.2, "delta_to_best": 0.4,
                       "laps_spare": 1.2}}

    def test_cross_unit_number_is_rejected(self):
        # ① 18.4 是油量（升），不能说成 18.4 圈
        self.assertIsNotNone(check_numbers("油量还剩 18.4 圈", self.CTX, "油还够吗"))

    def test_cross_unit_positive_still_passes(self):
        self.assertIsNone(check_numbers("油量 18.4 升", self.CTX, "油还够吗"))
        self.assertIsNone(check_numbers("油还够跑 6.2 圈", self.CTX, "油还够吗"))

    def test_negative_sign_flip_is_rejected(self):
        # ② 说成负数的值不能被当成同绝对值的正数
        self.assertIsNotNone(
            check_numbers("比最佳快 -0.4 秒", self.CTX, "圈速怎么样"))

    def test_signed_value_still_passes_when_context_is_negative(self):
        ctx = {"derived": {"trend_per_lap": -0.2}}
        self.assertIsNone(check_numbers("每圈 -0.2 秒", ctx, "圈速趋势"))

    def test_question_number_claim_is_not_exempt(self):
        # ③ 问题里出现的数字不能给模型的断言当豁免
        self.assertIsNotNone(
            check_numbers("还能跑 99 圈", self.CTX, "还能跑99圈吗"))

    def test_question_premise_echo_is_still_allowed(self):
        self.assertIsNone(
            check_numbers("确实慢了 0.3 秒", self.CTX, "为什么慢了0.3秒"))

    def test_minute_second_does_not_exempt_other_numbers(self):
        # ④ 1分23.5秒 只能豁免它自己，不能把别处的 1 升也放行
        self.assertIsNotNone(
            check_numbers("上一圈1分23.5秒，油还剩1升", self.CTX, "上一圈多少"))

    def test_minute_second_alone_passes(self):
        self.assertIsNone(check_numbers("上一圈1分23.5秒", self.CTX, "上一圈多少"))

    def test_chinese_two_is_rejected(self):
        # ⑤ 「两」不在旧中文数字表里，是稳定绕过
        self.assertIsNotNone(check_numbers("油还够两圈", self.CTX, "油还够吗"))

    def test_long_decimal_does_not_grow_tolerance(self):
        # ⑥ 小数位越多容差反而越大：6.300000000000 不能匹配 6.2
        self.assertIsNotNone(
            check_numbers("油还够跑6.300000000000圈", self.CTX, "油还够吗"))

    def test_range_with_negative_endpoint_is_rejected(self):
        # 范围两端都要能对上，负号不能因为另一端而混过去
        self.assertIsNotNone(
            check_numbers("差距在 -0.4 到 0.4 秒之间", self.CTX, "差距多少"))

    def test_range_of_present_values_passes(self):
        ctx = {"derived": {"delta_to_best": 0.4, "trend_per_lap": 0.3}}
        self.assertIsNone(
            check_numbers("差距在 0.3 到 0.4 秒之间", ctx, "差距多少"))

    def test_percentage_must_match_ratio(self):
        self.assertIsNone(check_numbers("湿度 62%", self.CTX, "湿度多少"))
        self.assertIsNotNone(check_numbers("湿度 91%", self.CTX, "湿度多少"))

    def test_ordinal_and_time_still_checked(self):
        self.assertIsNone(check_numbers("现在第 4 名", self.CTX, "第几名"))
        self.assertIsNotNone(check_numbers("现在第 2 名", self.CTX, "第几名"))


# ---------------------------------------------------------------- A11


class SafetyContentGateTests(unittest.TestCase):
    """A11：处罚/范围外问题分类 + 输出内容闸。"""

    def test_penalty_flag_question_is_safety(self):
        for text in ("为什么给我黑旗", "这次超车会不会罚时", "为什么判我犯规"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.SAFETY)

    def test_out_of_domain_time_question_is_safety(self):
        for text in ("现在几月", "现在是什么时间", "今天几号", "现在日期是多少"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.SAFETY)

    def test_safety_blocks_all_modes(self):
        for text in ("为什么给我黑旗", "现在几月"):
            kind = classify(text, None)
            for mode in ("full", "analysis_key", "whitelist", "all"):
                with self.subTest(text=text, mode=mode):
                    self.assertFalse(should_engage(kind, mode, rule_ok=False))

    def test_factual_flag_question_still_answerable(self):
        self.assertIs(classify("现在是什么旗", None), QueryKind.UNKNOWN)
        ok, checks = run_all({"answer": "当前黄旗", "data_used": []},
                             {"now": {"position": 4}}, "现在是什么旗", 60)
        self.assertTrue(ok, checks)

    def test_operation_advice_in_answer_is_refused(self):
        ok, checks = run_all({"answer": "把尾翼调软两格", "data_used": []},
                             {"now": {"position": 4}}, "为什么推头", 60)
        self.assertFalse(ok)
        self.assertEqual(checks.get("content"), "fail")

    def test_penalty_explanation_in_answer_is_refused(self):
        ok, checks = run_all({"answer": "这次超车会被罚时五秒", "data_used": []},
                             {"now": {"position": 4}}, "为什么被罚", 60)
        self.assertFalse(ok)
        self.assertEqual(checks.get("content"), "fail")

    def test_telemetry_state_statement_is_not_operation_advice(self):
        # 遥测事实（维修区已关闭 / DRS 已打开）不是操作建议，不能被内容闸误杀。
        ok, checks = run_all(
            {"answer": "维修区已经关闭，DRS 已打开", "data_used": []},
            {"now": {"position": 4}}, "赛道状态怎么样", 60)
        self.assertTrue(ok, checks)

    def test_imperative_switch_advice_is_refused(self):
        ok, checks = run_all({"answer": "建议把 TC 关掉", "data_used": []},
                             {"now": {"position": 4}}, "为什么打滑", 60)
        self.assertFalse(ok)
        self.assertEqual(checks.get("content"), "fail")

    def test_all_pass_checks_shape_unchanged(self):
        # 兼容既有调用方：通过时 checks 仍只有三道闸三个键。
        ok, checks = run_all(
            {"answer": "油还够跑 6.2 圈", "data_used": ["laps_of_fuel"]},
            {"now": {"laps_of_fuel": 6.2}}, "油还够吗", 60)
        self.assertTrue(ok)
        self.assertEqual(checks, {"length": "pass", "data": "pass",
                                  "number": "pass"})


# ---------------------------------------------------------------- A12


class PipelineCancellationTests(unittest.TestCase):
    """A12：在途旧回答被取消后不写 live、不播任何音频。

    确定时序：假 LLM 的 ask 回调里同步把取消标志置真，再返回正常 payload，
    因此不依赖 sleep 或线程调度。
    """

    def _pipeline(self, player=None, live=None, tts=None):
        cfg = ai_cfg(tts={"adapter": "openai", "base_url": "http://t",
                          "api_key": "k"})
        p = AiPipeline(cfg, out=io.StringIO(), player=player, on_live=live)
        p.tts = tts or FailingTts()
        return p

    def test_cancel_during_llm_call_drops_answer_without_live_or_audio(self):
        events = []
        flag = {"cancelled": False}
        player = MagicMock()
        player.enabled = True
        player.play_bytes.side_effect = lambda blob: events.append(("player", blob))
        tts = FailingTts()
        p = self._pipeline(player=player,
                           live=lambda text: events.append(("live", text)),
                           tts=tts)

        def fake_ask(ctx, question):
            flag["cancelled"] = True          # 用户在等模型期间关掉了 AI
            return {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"]}

        with patch.object(p.client, "ask", side_effect=fake_ask):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [],
                         cancelled=lambda: flag["cancelled"])
        self.assertFalse(r.ok)
        self.assertIn("取消", r.reason)
        self.assertEqual(events, [])
        self.assertEqual(tts.synths, [])
        self.assertEqual(p.breaker.state, "closed")

    def test_cancel_during_tts_synth_does_not_play(self):
        events = []
        flag = {"cancelled": False}
        player = MagicMock()
        player.enabled = True
        player.play_bytes.side_effect = lambda blob: events.append(("player", blob))
        tts = FailingTts(on_synth=lambda _text: flag.__setitem__("cancelled", True))
        p = self._pipeline(player=player,
                           live=lambda text: events.append(("live", text)),
                           tts=tts)

        with patch.object(p.client, "ask",
                          return_value={"answer": "油够跑 6.2 圈",
                                        "data_used": ["laps_of_fuel"]}):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [],
                         cancelled=lambda: flag["cancelled"])
        self.assertFalse(r.ok)
        self.assertIn("取消", r.reason)
        self.assertEqual(events, [("live", "油够跑 6.2 圈")])
        self.assertFalse(player.play_bytes.called)

    def test_no_cancellation_keeps_normal_flow(self):
        events = []
        player = MagicMock()
        player.enabled = True
        player.play_bytes.side_effect = lambda blob: events.append(("player", blob))
        p = self._pipeline(player=player,
                           live=lambda text: events.append(("live", text)))
        with patch.object(p.client, "ask",
                          return_value={"answer": "油够跑 6.2 圈",
                                        "data_used": ["laps_of_fuel"]}):
            r = p.handle("油还够吗", snap(), QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertEqual([e[0] for e in events], ["live", "player"])


# ---------------------------------------------------------------- A19


class TemplateFillerFieldTests(unittest.TestCase):
    """A19：白名单里的口语字符串与数组叶子字段要真的能填值。"""

    def test_spoken_string_field_fills(self):
        tpl = {"params": ["lap_last_spoken"],
               "rules": [{"when": "true", "say": "上一圈{lap_last_spoken}"}]}
        ctx = {"now": {"lap_last": 83.512,
                       "lap_last_spoken": "1分23.5秒"}}
        self.assertIsNone(validate_template(tpl))
        self.assertEqual(fill_template(tpl, ctx), "上一圈1分23.5秒")

    def test_array_leaf_number_fills(self):
        tpl = {"params": ["delta_s"],
               "rules": [{"when": "delta_s > 0.1", "say": "第1段慢了{delta_s}秒"},
                         {"when": "true", "say": "没有明显损失"}]}
        ctx = {"segments": {"best_loss": [
            {"sector": 1, "delta_s": 0.4, "delta_spoken": "0.40秒"}]}}
        self.assertIsNone(validate_template(tpl))
        self.assertEqual(fill_template(tpl, ctx), "第1段慢了0.4秒")

    def test_ambiguous_repeated_array_leaf_is_rejected(self):
        tpl = {"params": ["delta_s"],
               "rules": [{"when": "true", "say": "丢了{delta_s}秒"}]}
        ctx = {"segments": {"best_loss": [
            {"sector": 1, "delta_s": 0.4},
            {"sector": 4, "delta_s": 0.3}]}}
        self.assertIsNone(fill_template(tpl, ctx))

    def test_spoken_field_in_when_is_rejected_at_registration(self):
        tpl = {"params": ["lap_last_spoken"],
               "rules": [{"when": "lap_last_spoken > 1", "say": "x"},
                         {"when": "true", "say": "y"}]}
        self.assertIsNotNone(validate_template(tpl))

    def test_non_spoken_string_is_not_substituted(self):
        tpl = {"params": ["flags"],
               "rules": [{"when": "true", "say": "当前{flags}"}]}
        ctx = {"session": {"flags": ["黄旗"]}}
        # flags 不是白名单里的口语字符串字段；不能把任意字符串塞进播报。
        self.assertIsNone(fill_template(tpl, ctx))


# ---------------------------------------------------------------- engine e2e
#
# A09/A12 的 engine.py 侧补丁（review_id 透传、_ai_epoch/取消探针）已由 Lead
# 串行应用；这里用最小 Assistant 夹具跑真实 engine.ask_ai 链路：
#   · LLM 只在 _post_json / client.ask 边界替换；
#   · TTS 用不联网的 FailingTts；
#   · 取消由假 ask 回调同步触发 reload/close，无 sleep、无线程。


def _engine_config() -> dict:
    return {
        "ai": {"enabled": True, "mode": "all", "base_url": "http://x",
               "api_key": "k", "model": "m", "timeout_s": 2.0,
               "max_tokens": 300, "max_answer_chars": 60,
               "rate_limit_per_min": 6, "whitelist_intents": [],
               "tts": {"adapter": "off"}},
        "intent": {"miss_log": {"enabled": False}},
    }


def _engine_assistant(root: pathlib.Path):
    """最小 Assistant 夹具：真实 ask_ai + 真实 AiPipeline，外部边界全假。"""
    cfg = _engine_config()
    snap = Snapshot(connected=True, lap=12, laps_remain=5, fuel_level=18.4,
                    lap_last=83.512, lap_best=83.104, laps_of_fuel=6.2,
                    position=4)
    assistant = Assistant.__new__(Assistant)
    assistant.cfg = cfg
    assistant.out = io.StringIO()
    assistant.live = {"heard": "", "state": "idle", "answer": "", "error": ""}
    assistant.history = collections.deque()
    assistant.busy = False
    assistant.car = None
    assistant.sampler = MagicMock()
    assistant.derive = SimpleNamespace(lap_records=[])
    assistant.snapshot = lambda: snap
    assistant._play_ack = lambda: None
    assistant.pack = SimpleNamespace(has=lambda _msg: False)
    assistant.qa_log = root / "qa.jsonl"
    assistant.miss_log = root / "miss.jsonl"
    player = MagicMock()
    player.enabled = True
    assistant.player = player
    assistant._ai_epoch = 0
    store = LearnStore(root / "learn")
    assistant.ai = AiPipeline(cfg, store=store, out=assistant.out,
                              player=player,
                              snapshot_provider=assistant.snapshot,
                              on_live=assistant._on_ai_answer)
    assistant.ai.tts = FailingTts()
    return assistant, snap, player, store


class EngineCancellationE2ETests(unittest.TestCase):
    """A12：engine.ask_ai 的真实取消链路（reload / close）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.assistant, self.snap, self.player, self.store = _engine_assistant(self.root)

    def test_ai_switched_off_midflight_discards_answer(self):
        assistant = self.assistant
        disabled = {"ai": {"enabled": False, "mode": "all",
                           "tts": {"adapter": "off"}},
                    "intent": {"miss_log": {"enabled": False}}}

        def fake_ask(ctx, question):
            # 用户在等待模型期间保存配置把 AI 关掉（与 Service 串行路径一致）。
            with patch("src.engine.car_control.CarController",
                       return_value=MagicMock(enabled=False)):
                assistant.reload_switchables(disabled)
            return {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"]}

        with patch.object(assistant.ai.client, "ask", side_effect=fake_ask):
            rec = assistant.ask_ai("油还够吗", QueryKind.ANALYSIS, None, False)
        self.assertFalse(rec["ok"])
        self.assertTrue(rec.get("cancelled"))
        self.assertEqual(rec.get("ai_text"), "")
        self.assertNotEqual(assistant.live.get("answer"), "油够跑 6.2 圈")
        self.assertFalse(self.player.play_bytes.called)
        self.assertIsNone(assistant.ai)

    def test_close_midflight_discards_answer(self):
        assistant = self.assistant

        def fake_ask(ctx, question):
            assistant.close()          # 退出程序，撤销在途 AI
            return {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"]}

        with patch.object(assistant.ai.client, "ask", side_effect=fake_ask):
            rec = assistant.ask_ai("油还够吗", QueryKind.ANALYSIS, None, False)
        self.assertFalse(rec["ok"])
        self.assertTrue(rec.get("cancelled"))
        self.assertNotEqual(assistant.live.get("answer"), "油够跑 6.2 圈")
        self.assertFalse(self.player.play_bytes.called)

    def test_normal_model_answer_still_publishes_live_and_audio(self):
        assistant = self.assistant
        payload = {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"],
                   "dynamic": False}
        response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
        with patch("src.llm_engine._post_json", return_value=response):
            rec = assistant.ask_ai("油还够吗", QueryKind.ANALYSIS, None, False)
        self.assertTrue(rec["ok"])
        self.assertFalse(rec.get("cancelled"))
        self.assertEqual(assistant.live["state"], "answer")
        self.assertEqual(assistant.live["answer"], "油够跑 6.2 圈")
        self.assertTrue(self.player.play_bytes.called)


class EngineReviewIdE2ETests(unittest.TestCase):
    """A09：engine 复盘链路里 bad 反馈必须落到真正条目并两次停用。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.assistant, self.snap, self.player, self.store = _engine_assistant(self.root)

    def test_learn_hit_review_id_is_source_entry_and_bad_disables_it(self):
        assistant = self.assistant
        entry = self.store.add_knowledge("蓝旗什么意思", "后方有快车要放行")
        with patch.object(assistant.ai.client, "ask") as ask:
            rec = assistant.ask_ai("蓝旗什么意思", QueryKind.KNOWN, None, False)
        ask.assert_not_called()
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["ai_source"], "learn")
        # 学习命中的复盘 ID 直接就是源条目 ID，反馈才有对象。
        self.assertEqual(rec["review_id"], entry["id"])

        candidate = assistant.learn_candidate(entry["id"])
        self.assertEqual(candidate["review_id"], entry["id"])
        self.assertEqual(candidate["source_id"], entry["id"])
        # 对学习命中再点「确认」不能复制出第二条。
        self.assertEqual(self.store.add_from_review(candidate), "knowledge")
        self.assertEqual(len(self.store.stats()["knowledge"]), 1)

        self.assertTrue(self.store.mark(rec["review_id"], ok=False))
        self.assertTrue(self.store.mark(rec["review_id"], ok=False))
        rows = self.store.stats()["knowledge"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["disabled"])
        self.assertIsNone(self.store.match("蓝旗什么意思"))

    def test_model_answer_one_ok_two_bad_disables_actual_entry(self):
        assistant = self.assistant
        payload = {"answer": "油够跑 6.2 圈", "data_used": ["laps_of_fuel"],
                   "dynamic": False}
        response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
        with patch("src.llm_engine._post_json", return_value=response):
            rec = assistant.ask_ai("油还够吗", QueryKind.ANALYSIS, None, False)
        self.assertTrue(rec["ok"])
        self.assertNotEqual(rec["review_id"], "")

        candidate = assistant.learn_candidate(rec["review_id"])
        self.assertEqual(candidate["review_id"], rec["review_id"])
        self.assertIsNone(candidate["source_id"])
        self.assertEqual(self.store.add_from_review(candidate), "knowledge")

        self.assertTrue(self.store.mark(rec["review_id"], ok=False))
        self.assertTrue(self.store.mark(rec["review_id"], ok=False))
        rows = self.store.stats()["knowledge"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["disabled"])


if __name__ == "__main__":
    unittest.main()

