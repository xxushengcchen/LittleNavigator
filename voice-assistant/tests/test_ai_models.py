"""v1.6.0 模型列表拉取（GET /api/ai/models 的后端核心，不联网）。

原则：这里永远返回可序列化 dict、不抛异常、不泄露 api_key。
"""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from src import llm_engine

KEY = "sk-test-SECRET-1234567890"


def http_error(code: int, body: bytes = b'{"error":"unauthorized"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://example/models", code, "err", {}, io.BytesIO(body))


class UrlCandidateTests(unittest.TestCase):
    def test_root_base_tries_models_then_v1(self):
        self.assertEqual(
            llm_engine._models_url_candidates("https://api.deepseek.com"),
            ["https://api.deepseek.com/models",
             "https://api.deepseek.com/v1/models"])

    def test_v1_base_uses_single_candidate(self):
        self.assertEqual(
            llm_engine._models_url_candidates("https://api.openai.com/v1"),
            ["https://api.openai.com/v1/models"])

    def test_trailing_slash_is_ignored(self):
        self.assertEqual(
            llm_engine._models_url_candidates("https://api.deepseek.com/v1/"),
            ["https://api.deepseek.com/v1/models"])

    def test_models_suffix_is_not_doubled(self):
        self.assertEqual(
            llm_engine._models_url_candidates("https://api.openai.com/v1/models"),
            ["https://api.openai.com/v1/models"])

    def test_chat_completions_url_is_trimmed(self):
        self.assertEqual(
            llm_engine._models_url_candidates(
                "https://api.openai.com/v1/chat/completions"),
            ["https://api.openai.com/v1/models"])

    def test_blank_has_no_candidates(self):
        self.assertEqual(llm_engine._models_url_candidates(""), [])
        self.assertEqual(llm_engine._models_url_candidates("   "), [])


class ParseModelsTests(unittest.TestCase):
    def test_openai_data_shape(self):
        payload = {"object": "list", "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "owned_by": "deepseek"},
        ]}
        models = llm_engine._parse_models(payload)
        self.assertEqual([m["id"] for m in models],
                         ["deepseek-chat", "deepseek-reasoner"])
        self.assertEqual(models[0]["owned_by"], "deepseek")

    def test_models_shape_and_bare_list_and_strings(self):
        self.assertEqual(
            [m["id"] for m in llm_engine._parse_models(
                {"models": [{"id": "a"}]})], ["a"])
        self.assertEqual(
            [m["id"] for m in llm_engine._parse_models([{"id": "b"}])], ["b"])
        self.assertEqual(
            [m["id"] for m in llm_engine._parse_models(["c", "d"])], ["c", "d"])

    def test_dedupes_and_drops_invalid(self):
        payload = {"data": [
            {"id": "same"}, {"id": "same"}, {"id": "  second  "},
            {"id": None}, {"name": ""}, None, 42, {"name": "by-name"},
        ]}
        models = llm_engine._parse_models(payload)
        self.assertEqual([m["id"] for m in models],
                         ["same", "second", "by-name"])

    def test_weird_payloads_return_empty(self):
        for payload in (None, "x", 42, {"data": "x"}, {"data": None}):
            with self.subTest(payload=payload):
                self.assertEqual(llm_engine._parse_models(payload), [])


class ListModelsSuccessTests(unittest.TestCase):
    def test_success_returns_models_and_sends_bearer(self):
        payload = {"data": [{"id": "deepseek-chat"}]}
        with patch.object(llm_engine, "_get_json", return_value=payload) as get:
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertEqual(result, {"ok": True, "models": [{"id": "deepseek-chat"}]})
        url, headers, timeout = get.call_args.args
        self.assertEqual(url, "https://api.deepseek.com/models")
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
        self.assertGreater(timeout, 0)

    def test_404_retries_v1_candidate(self):
        with patch.object(llm_engine, "_get_json",
                          side_effect=[http_error(404),
                                       {"data": [{"id": "gpt-4o"}]}]) as get:
            result = llm_engine.list_models("https://api.openai.com", KEY)
        self.assertTrue(result["ok"])
        self.assertEqual(result["models"][0]["id"], "gpt-4o")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].args[0],
                         "https://api.openai.com/v1/models")

    def test_both_candidates_404_gives_readable_error(self):
        with patch.object(llm_engine, "_get_json",
                          side_effect=[http_error(404), http_error(404)]) as get:
            result = llm_engine.list_models("http://host", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("手动填写", result["error"])
        self.assertEqual(get.call_count, 2)

    def test_empty_model_list_is_readable_error(self):
        with patch.object(llm_engine, "_get_json", return_value={"data": []}):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("手动填写", result["error"])


class ListModelsErrorTests(unittest.TestCase):
    def test_missing_base_url(self):
        result = llm_engine.list_models("", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("接口地址", result["error"])

    def test_missing_api_key(self):
        result = llm_engine.list_models("https://api.deepseek.com", "")
        self.assertFalse(result["ok"])
        self.assertIn("API Key", result["error"])

    def test_whitespace_in_base_url_rejected(self):
        result = llm_engine.list_models("https://api.deepseek.com /v1", KEY)
        self.assertFalse(result["ok"])

    def test_401_does_not_retry_and_message_is_chinese(self):
        with patch.object(llm_engine, "_get_json",
                          side_effect=http_error(401)) as get:
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])
        self.assertIn("Key", result["error"])
        self.assertEqual(get.call_count, 1)

    def test_timeout_message(self):
        with patch.object(llm_engine, "_get_json", side_effect=TimeoutError("slow")):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])

    def test_urlerror_timeout_reason_message(self):
        error = urllib.error.URLError(TimeoutError("timed out"))
        with patch.object(llm_engine, "_get_json", side_effect=error):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("超时", result["error"])

    def test_urlerror_message(self):
        error = urllib.error.URLError("getaddrinfo failed")
        with patch.object(llm_engine, "_get_json", side_effect=error):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("无法连接", result["error"])

    def test_bad_json_message(self):
        error = llm_engine.LlmError("接口返回的不是合法 JSON：<html>")
        with patch.object(llm_engine, "_get_json", side_effect=error):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("JSON", result["error"])

    def test_unexpected_exception_is_wrapped(self):
        with patch.object(llm_engine, "_get_json", side_effect=RuntimeError("boom")):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertFalse(result["ok"])
        self.assertIn("拉取模型失败", result["error"])

    def test_api_key_never_appears_in_response(self):
        # 服务端可能在错误体里回显请求头；这里连「原样回显」都要脱敏
        error = llm_engine.LlmError(f"服务回显 Authorization: Bearer {KEY}")
        with patch.object(llm_engine, "_get_json", side_effect=error):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(KEY, dumped)
        self.assertIn("***", result["error"])

    def test_http_error_body_also_redacted(self):
        body = json.dumps({"error": f"bad key {KEY}"}).encode()
        with patch.object(llm_engine, "_get_json",
                          side_effect=http_error(401, body)):
            result = llm_engine.list_models("https://api.deepseek.com", KEY)
        self.assertNotIn(KEY, json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
