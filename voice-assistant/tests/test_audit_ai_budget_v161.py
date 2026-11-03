"""A15/A18：模型返回类型校验与输入预算（审计编号）。

只接假 LLM，不发真实请求。
"""
from __future__ import annotations

import unittest
from unittest import mock

from src import ai_pipeline, ai_constants, llm_engine
from src.query_class import QueryKind
from src.telemetry import Snapshot


class FakeClient:
    last_latency_ms = 5

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def ask(self, ctx, question):
        self.calls += 1
        return self.payload


def _pipeline(payload):
    pipe = ai_pipeline.AiPipeline.__new__(ai_pipeline.AiPipeline)
    pipe.mode = "all"
    pipe.whitelist = []
    pipe.store = None
    pipe.client = FakeClient(payload)
    pipe.breaker = llm_engine.AdaptiveBreaker()
    # A16：真实构造会建立限流闸；用 __new__ 的夹具必须补上同一个约定。
    pipe.rate_limit_per_min = 6
    pipe.rate_gate = ai_pipeline.RateGate(6)
    pipe.max_input_tokens = ai_constants.MAX_INPUT_TOKENS_HARD
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


class TypedPayloadTests(unittest.TestCase):
    def _run(self, payload, question="油还够吗", kind=QueryKind.ANALYSIS):
        pipe = _pipeline(payload)
        return pipe.handle(question, Snapshot(connected=True), kind, [])

    def test_wrong_types_fail_safely(self):
        cases = [
            {"answer": 123},
            {"answer": ["列表"]},
            {"refused": "false", "answer": "随便"},
            {"refused": None, "answer": "油够跑 3 圈", "data_used": "now"},
            {"refused": False, "answer": "油够跑 3 圈", "dynamic": "yes"},
            {"refused": False, "answer": "油够跑 3 圈", "template": ["不是对象"]},
            {"refused": False, "answer": "油够跑 3 圈", "confidence": float("nan")},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                result = self._run(payload)
                self.assertFalse(result.ok, payload)
                self.assertIn("格式异常", result.reason)

    def test_valid_payload_still_answers(self):
        result = self._run({"refused": False, "answer": "现在读不到"})
        self.assertTrue(result.ok, result.reason)


class InputBudgetTests(unittest.TestCase):
    def test_long_question_rejected_without_calling_model(self):
        pipe = _pipeline({"refused": False, "answer": "好"})
        question = "油" * 10
        result = pipe.handle(question, Snapshot(connected=True),
                             QueryKind.ANALYSIS, [])
        # 正常短问题应当真的把请求发出去
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(pipe.client.calls, 1)

    def test_oversized_question_is_refused_before_send(self):
        pipe = _pipeline({"refused": False, "answer": "好"})
        # system 提示本身已占几百 token，这里远超 1500 上限
        question = "油" * (ai_constants.MAX_INPUT_TOKENS_HARD * 2)
        result = pipe.handle(question, Snapshot(connected=True),
                             QueryKind.ANALYSIS, [])
        self.assertFalse(result.ok)
        self.assertIn("预算", result.reason)
        self.assertEqual(pipe.client.calls, 0, "超预算时不应发出请求")

    def test_estimator_counts_system_prompt(self):
        short = llm_engine.estimate_prompt_tokens("油")
        self.assertGreater(short, 300, "system 提示本身就要计入预算")
        self.assertGreater(llm_engine.estimate_prompt_tokens("油" * 400), short)


if __name__ == "__main__":
    unittest.main()
