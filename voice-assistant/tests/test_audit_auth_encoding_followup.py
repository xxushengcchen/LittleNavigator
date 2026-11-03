"""S10 regression: non-ASCII credentials are rejected, never a 500."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from app.server import Handler, Server, Service, _token_matches


class TokenComparisonTests(unittest.TestCase):
    def test_non_ascii_values_compare_without_raising(self):
        self.assertFalse(_token_matches("密钥", "secret"))
        self.assertFalse(_token_matches("secret", "密钥"))
        self.assertTrue(_token_matches("密钥", "密钥"))
        self.assertFalse(_token_matches(None, "secret"))
        self.assertFalse(_token_matches("secret", None))

    def test_latin1_decoded_header_bytes_do_not_raise(self):
        # http.server 用 latin-1 解码请求头：非 ASCII 字节会变成 U+0080-U+00FF
        self.assertFalse(_token_matches("k\u00e9y", "secret"))


class LocalTokenEndpointTests(unittest.TestCase):
    def test_non_ascii_token_header_returns_403_json(self):
        """客户端直接发原始非 ASCII 字节：服务端必须回 403，而不是 500。"""
        import http.client

        class TestHandler(Handler):
            pass

        service = Service.__new__(Service)
        service.api_token = "synthetic-token"
        TestHandler.service = service
        server = Server(("127.0.0.1", 0), TestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        body = json.dumps({"running": True}).encode()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            conn.putrequest("POST", "/api/listen")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
            # bytes 原样下发，绕过 http.client 的 latin-1 编码限制
            conn.putheader(b"X-Assistant-Token", b"\xe6\x97\xa5\xe6\x9c\xac")
            conn.endheaders(body)
            resp = conn.getresponse()
            payload = json.loads(resp.read() or b"{}")
            self.assertEqual(resp.status, 403, payload)
            self.assertIn("凭据", payload.get("error", ""))
        finally:
            conn.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
