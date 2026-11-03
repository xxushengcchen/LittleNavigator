"""配置事务与 HTTP 资源边界（审计 S07 / S09 / S11 / S04）。

全部用临时目录与假连接，不碰真实用户配置、不开真实网络。
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest

import yaml

from http.server import ThreadingHTTPServer
from unittest import mock

from app.server import Server, Service
from src import config


def _base_config_file(directory: Path) -> Path:
    base = config.load()
    for key in ("dir", "extra_dir", "telemetry_dir"):
        if base["voice_pack"].get(key):
            base["voice_pack"][key] = str(config.resolve(base, base["voice_pack"][key]))
    base["playback"]["enabled"] = False
    path = directory / "config.yaml"
    path.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    return path


class DefaultsIsolationTests(unittest.TestCase):
    """S09：环境变量里的密钥不能污染 DEFAULTS。"""

    def test_env_key_does_not_mutate_defaults(self):
        snapshot = copy.deepcopy(config.DEFAULTS)
        old = os.environ.get("LITTLE_NAVIGATOR_AI_KEY")
        try:
            os.environ["LITTLE_NAVIGATOR_AI_KEY"] = "sk-env-only-placeholder"
            first = config.load()
            self.assertEqual(first["ai"]["api_key"], "sk-env-only-placeholder")
            # 关键断言：DEFAULTS 本身不能变
            self.assertEqual(config.DEFAULTS["ai"]["api_key"], snapshot["ai"]["api_key"])
            del os.environ["LITTLE_NAVIGATOR_AI_KEY"]
            second = config.load()
            self.assertEqual(second["ai"]["api_key"], snapshot["ai"]["api_key"])
        finally:
            if old is None:
                os.environ.pop("LITTLE_NAVIGATOR_AI_KEY", None)
            else:
                os.environ["LITTLE_NAVIGATOR_AI_KEY"] = old
            self.assertEqual(config.DEFAULTS["ai"]["api_key"], snapshot["ai"]["api_key"])

    def test_merge_does_not_alias_nested_defaults(self):
        merged = config._merge(config.DEFAULTS, {})
        merged["ai"]["api_key"] = "mutated"
        self.assertNotEqual(config.DEFAULTS["ai"]["api_key"], "mutated")


class NumericValidationTests(unittest.TestCase):
    """S11：超大整数要稳定报配置错误，不能抛 OverflowError。"""

    def test_huge_int_is_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _base_config_file(Path(tmp))
            service = Service(str(path), sim=True)
            try:
                before = path.read_bytes()
                for patch in ({"playback": {"volume": 10 ** 400}},
                              {"ptt": {"poll_ms": 10 ** 400}},
                              {"overlay": {"margin": 10 ** 400}}):
                    with self.assertRaises(ValueError):
                        service.configure(patch)
                self.assertEqual(path.read_bytes(), before)
            finally:
                service.close()

    def test_tts_nan_timeout_rejected_before_write(self):
        """S07：非法 TTS 超时必须在写盘前被拒，覆盖层保持原样。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _base_config_file(Path(tmp))
            service = Service(str(path), sim=True)
            try:
                local = Path(service.cfg["_local_path"])
                before = local.read_bytes() if local.exists() else None
                for bad in (float("nan"), float("inf"), -1, "2.5", 99):
                    with self.assertRaises(ValueError):
                        service.configure({"ai": {"tts": {"timeout_s": bad}}})
                after = local.read_bytes() if local.exists() else None
                self.assertEqual(after, before)
                # 合法值仍然能存
                service.configure({"ai": {"tts": {"timeout_s": 1.5}}})
                self.assertEqual(service.cfg["ai"]["tts"]["timeout_s"], 1.5)
            finally:
                service.close()


class _OneSlotServer(Server):
    max_connections = 1
    request_idle_timeout_s = 3.5


class _FakeSocket:
    def __init__(self):
        self.timeout = None
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True


class ServerBoundTests(unittest.TestCase):
    """S04：连接槽位封顶，满了直接关连接；空闲超时写进 socket。"""

    def _server(self):
        return _OneSlotServer(("127.0.0.1", 0), type("H", (), {"service": None}))

    def test_full_slot_closes_connection_without_spawning(self):
        server = self._server()
        try:
            self.assertTrue(server._slots.acquire(blocking=False))
            fake = _FakeSocket()
            with mock.patch.object(ThreadingHTTPServer, "process_request") as spawn:
                server.process_request(fake, ("127.0.0.1", 1))
            self.assertTrue(fake.closed, "槽位用满时必须拒绝新连接")
            spawn.assert_not_called()
        finally:
            server._slots.release()
            server.server_close()

    def test_idle_timeout_applied_before_spawn(self):
        server = self._server()
        try:
            fake = _FakeSocket()
            with mock.patch.object(ThreadingHTTPServer, "process_request") as spawn:
                server.process_request(fake, ("127.0.0.1", 2))
            self.assertEqual(fake.timeout, 3.5)
            self.assertFalse(fake.closed)
            self.assertTrue(spawn.called)
        finally:
            server._slots.release()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
