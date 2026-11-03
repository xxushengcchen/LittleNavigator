"""外部 HTTP 取数的安全边界：不跟随跳转、限字节、按墙钟返回。

对应审计 S02（跳转泄漏凭据）、S03（错误脱敏）、A13（总时长）、A14（响应体上限）。
全部只连 127.0.0.1，不发任何真实请求。
"""
from __future__ import annotations

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src import http_transport


class _Handler(BaseHTTPRequestHandler):
    hits: list = []
    mode = "ok"

    def log_message(self, fmt, *args):  # noqa: ANN001
        pass

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        if self.mode == "redirect":
            # 跳到「另一个」地址时，客户端绝不能把 Authorization 带过去。
            self.send_response(302)
            self.send_header("Location", "/should-not-be-followed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.mode == "slow":
            time.sleep(3.0)
            body = b"late"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.mode == "huge":
            body = b"x" * 20000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HttpTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        _Handler.hits = []
        _Handler.mode = "ok"

    def test_redirect_is_refused_and_not_followed(self):
        """302 必须当场失败：既不跟随，也不会把凭据送到第二个地址。"""
        _Handler.mode = "redirect"
        with self.assertRaises(http_transport.HttpFailure) as caught:
            http_transport.fetch_bytes(
                "GET", self.url + "/v1/models",
                {"Authorization": "Bearer sk-very-secret-value"})
        self.assertEqual(caught.exception.status, 302)
        self.assertIn("跳转", caught.exception.message)
        self.assertNotIn("sk-very-secret-value", caught.exception.message)
        # 只打了第一个地址一次，跳转目标从未被请求。
        self.assertEqual(_Handler.hits, ["/v1/models"])

    def test_success_returns_bytes(self):
        self.assertEqual(http_transport.fetch_bytes("GET", self.url + "/x"), b'{"ok": true}')

    def test_declared_and_incremental_size_caps(self):
        """已知 Content-Length 超限先拒；未知长度也要边读边拒。"""
        _Handler.mode = "huge"
        with self.assertRaises(http_transport.HttpFailure):
            http_transport.fetch_bytes("GET", self.url + "/x", max_bytes=4096)

    def test_wall_clock_deadline_not_socket_timeout(self):
        """慢响应按总时长返回，而不是被单次 socket 超时拖住。"""
        _Handler.mode = "slow"
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            http_transport.fetch_bytes("GET", self.url + "/x", deadline_s=0.3)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_bad_url_is_rejected_without_echo(self):
        for bad in ("ftp://example.com/x", "http://user:pw@example.com/x", "not-a-url"):
            with self.assertRaises(http_transport.HttpFailure) as caught:
                http_transport.fetch_bytes("GET", bad)
            self.assertNotIn("pw", caught.exception.message)

    def test_cancel_raises_cancelled(self):
        with self.assertRaises(http_transport.Cancelled):
            http_transport.fetch_bytes("GET", self.url + "/x", cancelled=lambda: True)


if __name__ == "__main__":
    unittest.main()
