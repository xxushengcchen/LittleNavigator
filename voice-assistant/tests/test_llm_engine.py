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
        c = LlmClient("http://x", "k", "m", timeout_s=99, max_tokens=9999)
        self.assertLessEqual(c.timeout_s, 2.0)
        self.assertLessEqual(c.max_tokens, 300)

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
