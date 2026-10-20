import time
import unittest
from unittest.mock import patch

from src.llm_engine import AdaptiveBreaker, LlmClient, LlmError


class BreakerTests(unittest.TestCase):
    def test_opens_after_three_consecutive_timeouts(self):
        b = AdaptiveBreaker()
        for _ in range(3):
            b.record(2000, timed_out=True)
        self.assertTrue(b.should_skip())
        self.assertEqual(b.state, "open")

    def test_success_resets_counter(self):
        b = AdaptiveBreaker()
        b.record(2000, timed_out=True)
        b.record(2000, timed_out=True)
        b.record(300, timed_out=False)
        b.record(2000, timed_out=True)
        b.record(2000, timed_out=True)
        self.assertFalse(b.should_skip())

    def test_half_open_allows_one_probe(self):
        b = AdaptiveBreaker()
        for _ in range(3):
            b.record(2000, timed_out=True)
        self.assertTrue(b.should_skip())
        b.reset_half_open()
        self.assertFalse(b.should_skip())

    def test_success_after_half_open_closes(self):
        b = AdaptiveBreaker()
        for _ in range(3):
            b.record(2000, timed_out=True)
        b.reset_half_open()
        b.record(400, timed_out=False)
        self.assertEqual(b.state, "closed")


class ClampTests(unittest.TestCase):
    def test_timeout_is_clamped(self):
        from src import ai_constants as const
        c = LlmClient("http://x", "k", "m", timeout_s=99, max_tokens=9999)
        # 用常量断言：v1.5.1 把 LLM 硬上限从 2.0s 放宽到 3.0s。
        self.assertEqual(c.timeout_s, const.LLM_DEADLINE_HARD_MAX)
        self.assertEqual(c.max_tokens, const.MAX_OUTPUT_TOKENS_HARD)

    def test_smaller_timeout_is_kept(self):
        c = LlmClient("http://x", "k", "m", timeout_s=0.8, max_tokens=50)
        self.assertEqual(c.timeout_s, 0.8)
        self.assertEqual(c.max_tokens, 50)


class EndpointTests(unittest.TestCase):
    def test_appends_chat_completions(self):
        c = LlmClient("https://api.deepseek.com", "k", "m")
        self.assertEqual(c._endpoint(), "https://api.deepseek.com/chat/completions")

    def test_does_not_double_append(self):
        c = LlmClient("https://api.deepseek.com/v1/chat/completions", "k", "m")
        self.assertEqual(c._endpoint(), "https://api.deepseek.com/v1/chat/completions")

    def test_strips_trailing_slash(self):
        c = LlmClient("https://api.deepseek.com/", "k", "m")
        self.assertEqual(c._endpoint(), "https://api.deepseek.com/chat/completions")


class AskTests(unittest.TestCase):
    def test_missing_key_raises(self):
        c = LlmClient("http://x", "", "m")
        with self.assertRaises(LlmError):
            c.ask({"now": {}}, "test")

    def test_missing_base_url_raises(self):
        c = LlmClient("", "k", "m")
        with self.assertRaises(LlmError):
            c.ask({"now": {}}, "test")

    def test_good_response_is_parsed(self):
        reply = {"choices": [{"message": {"content": '{"answer": "ok", "refused": false}'}}]}
        with patch("src.llm_engine._post_json", return_value=reply):
            c = LlmClient("http://x", "k", "m")
            out = c.ask({"now": {}}, "test")
        self.assertEqual(out["answer"], "ok")

    def test_non_json_content_raises_cleanly(self):
        reply = {"choices": [{"message": {"content": "not json"}}]}
        with patch("src.llm_engine._post_json", return_value=reply):
            c = LlmClient("http://x", "k", "m", timeout_s=1.0, max_tokens=10)
            with self.assertRaises(LlmError):
                c.ask({"now": {}}, "test")

    def test_malformed_envelope_raises(self):
        with patch("src.llm_engine._post_json", return_value={"oops": 1}):
            c = LlmClient("http://x", "k", "m")
            with self.assertRaises(LlmError):
                c.ask({"now": {}}, "test")

    def test_latency_is_recorded(self):
        reply = {"choices": [{"message": {"content": '{"answer": "ok"}'}}]}
        with patch("src.llm_engine._post_json", return_value=reply):
            c = LlmClient("http://x", "k", "m")
            c.ask({"now": {}}, "test")
        self.assertIsNotNone(c.last_latency_ms)


class TimeoutTests(unittest.TestCase):
    def test_unreachable_endpoint_raises_within_budget(self):
        c = LlmClient("http://127.0.0.1:1", "k", "m", timeout_s=0.3, max_tokens=10)
        start = time.monotonic()
        with self.assertRaises(LlmError):
            c.ask({"now": {}}, "test")
        self.assertLess(time.monotonic() - start, 2.0)


class PayloadTests(unittest.TestCase):
    def test_payload_carries_constraints(self):
        c = LlmClient("http://x", "k", "m", timeout_s=2.0, max_tokens=300)
        body = c._payload({"now": {"lap": 1}}, "问题")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], 300)
        system = body["messages"][0]["content"]
        self.assertIn("现在读不到", system)
        self.assertIn("阿拉伯数字", system)
        self.assertIn("不要解释处罚原因", system)

    def test_payload_prefers_spoken_duration_fields(self):
        """v1.5.2：圈速/时长必须优先引用 race_context 的 *_spoken 字段。"""
        c = LlmClient("http://x", "k", "m")
        system = c._payload({}, "问题")["messages"][0]["content"]
        self.assertIn("_spoken", system)
        self.assertIn("lap_last_spoken", system)
        self.assertIn("分秒", system)


class ThinkingProviderTests(unittest.TestCase):
    """服务商判定：只认 base_url / model 两条线索，不联网探测。"""

    def test_deepseek_by_base_url(self):
        from src.llm_engine import thinking_provider
        self.assertEqual(thinking_provider("https://api.deepseek.com",
                                           "whatever"), "deepseek")

    def test_deepseek_by_model_prefix(self):
        from src.llm_engine import thinking_provider
        self.assertEqual(thinking_provider("http://local",
                                           "deepseek-reasoner"), "deepseek")

    def test_openai_by_base_url(self):
        from src.llm_engine import thinking_provider
        self.assertEqual(thinking_provider("https://api.openai.com/v1",
                                           "gpt-4o"), "openai")

    def test_openai_by_reasoning_model_prefix(self):
        from src.llm_engine import thinking_provider
        for model in ("o1-mini", "o3", "o4-mini", "gpt-5"):
            with self.subTest(model=model):
                self.assertEqual(thinking_provider("http://x", model), "openai")

    def test_unknown_provider(self):
        from src.llm_engine import thinking_provider
        self.assertEqual(thinking_provider("https://ark.cn-beijing.volces.com/api/v3",
                                           "ep-2024"), "unknown")
        self.assertEqual(thinking_provider("http://127.0.0.1:11434/v1",
                                           "qwen2.5:7b"), "unknown")

    def test_supported_flag_matches_provider(self):
        from src.llm_engine import thinking_supported
        self.assertTrue(thinking_supported("https://api.deepseek.com", "deepseek-chat"))
        self.assertTrue(thinking_supported("", "gpt-5"))
        self.assertFalse(thinking_supported("http://x", "qwen2.5:7b"))


class ThinkingRequestBodyTests(unittest.TestCase):
    """fake HTTP：直接看真正发出去的请求体（契约 §3）。"""

    def _sent_body(self, base_url, model, mode):
        captured = {}

        def fake_post(url, body, headers, timeout):
            captured["url"] = url
            captured["body"] = body
            return {"choices": [{"message": {"content": '{"answer": "ok"}'}}]}

        c = LlmClient(base_url, "k", model, thinking_mode=mode)
        with patch("src.llm_engine._post_json", side_effect=fake_post):
            c.ask({"now": {"lap": 1}}, "测试")
        return captured["body"]

    def test_deepseek_off_sends_disabled_thinking(self):
        body = self._sent_body("https://api.deepseek.com", "deepseek-chat", "off")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", body)

    def test_deepseek_low_sends_enabled_and_low_effort(self):
        body = self._sent_body("https://api.deepseek.com", "deepseek-chat", "low")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "low")

    def test_openai_low_sends_only_reasoning_effort(self):
        body = self._sent_body("https://api.openai.com/v1", "gpt-5", "low")
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertNotIn("thinking", body)

    def test_openai_off_sends_nothing(self):
        body = self._sent_body("https://api.openai.com/v1", "o3", "off")
        self.assertNotIn("thinking", body)
        self.assertNotIn("reasoning_effort", body)

    def test_unknown_provider_sends_nothing_in_both_modes(self):
        for mode in ("off", "low"):
            with self.subTest(mode=mode):
                body = self._sent_body("http://127.0.0.1:11434/v1",
                                       "qwen2.5:7b", mode)
                self.assertNotIn("thinking", body)
                self.assertNotIn("reasoning_effort", body)

    def test_invalid_thinking_mode_falls_back_to_off(self):
        c = LlmClient("https://api.deepseek.com", "k", "deepseek-chat",
                      thinking_mode="high")
        self.assertEqual(c.thinking_mode, "off")
        self.assertEqual(c._payload({}, "q")["thinking"], {"type": "disabled"})

    def test_body_keeps_protocol_fields(self):
        """思考参数不能挤掉原有的 model/messages/temperature。"""
        body = self._sent_body("https://api.deepseek.com", "deepseek-chat", "low")
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["messages"][0]["role"], "system")


class ConnectionTestTests(unittest.TestCase):
    def test_success_reports_latency(self):
        reply = {"choices": [{"message": {"content": "ok"}}]}
        with patch("src.llm_engine._post_json", return_value=reply):
            c = LlmClient("http://x", "k", "m")
            out = c.test_connection()
        self.assertTrue(out["ok"])
        self.assertIn("latency_ms", out)

    def test_failure_is_reported_not_raised(self):
        with patch("src.llm_engine._post_json", side_effect=OSError("boom")):
            c = LlmClient("http://x", "k", "m")
            out = c.test_connection()
        self.assertFalse(out["ok"])
        self.assertIn("error", out)


if __name__ == '__main__':
    unittest.main()


class SingleFlightTests(unittest.TestCase):
    """单飞必须是互斥，不是「标记取消就并行」。

    实测过一个真 bug：用取消标志实现时，两个请求并发在路上，
    第二个问题会拿到第一个问题的答案（`_inflight` 互相覆盖）。
    """

    def test_concurrent_asks_do_not_cross_answers(self):
        import threading

        def post(url, body, headers, timeout):
            q = body["messages"][1]["content"].rsplit("问题：", 1)[-1]
            time.sleep(0.25)
            return {"choices": [{"message": {"content": '{"answer": "%s"}' % q}}]}

        c = LlmClient("http://x", "k", "m", timeout_s=2.0)
        out: dict[str, str] = {}

        def first():
            out["q1"] = c.ask({"now": {}}, "q1")["answer"]

        with patch("src.llm_engine._post_json", side_effect=post):
            t = threading.Thread(target=first)
            t.start()
            time.sleep(0.05)
            out["q2"] = c.ask({"now": {}}, "q2")["answer"]
            t.join(3)

        self.assertEqual(out["q1"], "q1")
        self.assertEqual(out["q2"], "q2")

    def test_second_ask_waits_instead_of_failing(self):
        # 闸门等待上限是 timeout_s，正常排队不应报错
        import threading

        def post(url, body, headers, timeout):
            time.sleep(0.1)
            return {"choices": [{"message": {"content": '{"answer": "ok"}'}}]}

        c = LlmClient("http://x", "k", "m", timeout_s=2.0)
        errors: list[str] = []

        def first():
            try:
                c.ask({"now": {}}, "q1")
            except Exception as exc:      # noqa: BLE001
                errors.append(str(exc))

        with patch("src.llm_engine._post_json", side_effect=post):
            t = threading.Thread(target=first)
            t.start()
            time.sleep(0.02)
            c.ask({"now": {}}, "q2")
            t.join(3)

        self.assertEqual(errors, [])
