"""A05/A07/A08：学习库入库与复用必须 fail-closed。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src import ai_pipeline, ai_learn, llm_engine
from src.query_class import QueryKind
from src.telemetry import Snapshot


class ReviewIntakeTests(unittest.TestCase):
    """A05：说不清是不是动态的回答，不能当静态知识存下来。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ai_learn.LearnStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_dynamic_without_template_is_rejected(self):
        record = {"question": "油还够吗", "answer": "够跑 5 圈",
                  "dynamic": True, "template": None}
        self.assertEqual(self.store.add_from_review(record), "rejected")

    def test_missing_dynamic_flag_is_rejected(self):
        record = {"question": "油还够吗", "answer": "够跑 5 圈"}
        self.assertEqual(self.store.add_from_review(record), "rejected")

    def test_non_bool_dynamic_is_rejected(self):
        record = {"question": "油还够吗", "answer": "够跑 5 圈",
                  "dynamic": "false"}
        self.assertEqual(self.store.add_from_review(record), "rejected")

    def test_explicit_static_is_stored(self):
        record = {"question": "什么旗是绿旗", "answer": "绿旗表示正常比赛",
                  "dynamic": False}
        self.assertEqual(self.store.add_from_review(record), "knowledge")

    def test_dynamic_with_valid_template_is_stored(self):
        record = {
            "question": "油还够吗", "answer": "x", "dynamic": True,
            "template": {"params": ["fuel_level"],
                         "rules": [{"when": "fuel_level < 5",
                                    "say": "油只剩 {fuel_level} 升"},
                                   {"when": "true", "say": "油还够"}]},
        }
        self.assertEqual(self.store.add_from_review(record), "template")


class TemplateConditionTests(unittest.TestCase):
    """A08：条件字段缺失时不能用兜底分支冒充结论。"""

    TEMPLATE = {"params": ["fuel_level"],
                "rules": [{"when": "fuel_level < 5", "say": "油只剩 {fuel_level} 升"},
                          {"when": "true", "say": "油还够"}]}

    def test_missing_field_returns_none(self):
        self.assertIsNone(ai_learn.fill_template(self.TEMPLATE, {"now": {}}))

    def test_false_condition_falls_through_to_fallback(self):
        self.assertEqual(ai_learn.fill_template(self.TEMPLATE, {"now": {"fuel_level": 20}}),
                         "油还够")

    def test_true_condition_uses_branch(self):
        self.assertEqual(ai_learn.fill_template(self.TEMPLATE, {"now": {"fuel_level": 3}}),
                         "油只剩 3 升")


class LearnedTemplateGateTests(unittest.TestCase):
    """A07：学习模板填出来的句子也要过三道闸。"""

    def _pipeline(self, store):
        pipe = ai_pipeline.AiPipeline.__new__(ai_pipeline.AiPipeline)
        pipe.mode = "all"
        pipe.whitelist = []
        pipe.store = store
        pipe.client = None
        pipe.breaker = llm_engine.AdaptiveBreaker()
            # A16：真实构造会建立限流闸；用 __new__ 的夹具必须补上同一个约定。
        pipe.rate_limit_per_min = 6
        pipe.rate_gate = ai_pipeline.RateGate(6)
        pipe.max_input_tokens = 1500
        pipe.max_answer_chars = 60
        pipe.snapshot_provider = None
        pipe.tts = None
        pipe.player = None
        pipe.out = None
        pipe.thinking_mode = "off"
        pipe._log = lambda *a, **k: None
        pipe._notify_live = lambda *a, **k: None
        pipe._maybe_speak = lambda *a, **k: None
        return pipe

    def test_learned_template_with_invented_number_is_not_used(self):
        """模板里写死一个上下文中不存在的数字 → 应被数字闸挡下，退回模型。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = ai_learn.LearnStore(Path(tmp))
            store.add_template("油还够吗", {
                "params": ["fuel_level"],
                "rules": [{"when": "true",
                           "say": "还剩 {fuel_level} 升，够跑 99 圈"}],
            })
            pipe = self._pipeline(store)
            result = pipe._from_learn("油还够吗", Snapshot(connected=True), [],
                                      QueryKind.ANALYSIS)
            self.assertIsNone(result, "凭空数字的模板不应被直接播报")

    def test_learned_template_with_real_value_still_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ai_learn.LearnStore(Path(tmp))
            store.add_template("还剩多少油", {
                "params": ["fuel_level"],
                "rules": [{"when": "true", "say": "还剩 {fuel_level} 升"}],
            })
            pipe = self._pipeline(store)
            snap = Snapshot(connected=True, fuel_level=18.4)
            result = pipe._from_learn("还剩多少油", snap, [], QueryKind.ANALYSIS)
            self.assertIsNotNone(result)
            self.assertTrue(result.ok)
            self.assertIn("18.4", result.text)


if __name__ == "__main__":
    unittest.main()
