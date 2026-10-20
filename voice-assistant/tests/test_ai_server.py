import json
import pathlib
import tempfile
import threading
import unittest
import unittest.mock

import yaml

from src import config
from src.engine import Assistant


class KeyIsolationTests(unittest.TestCase):
    """密钥不得出现在任何对外视图里。

    这条必须打真实路径：只验证「自己构造的字典里没有 key」毫无意义。
    """

    SECRET = "sk-SECRET-123"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        (self.root / "config.yaml").write_text(
            yaml.safe_dump({"ai": {"enabled": True, "base_url": "http://x",
                                   "model": "m"}}), encoding="utf-8")
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"api_key": self.SECRET,
                                   "tts": {"adapter": "openai",
                                           "base_url": "http://t",
                                           "api_key": self.SECRET}}}),
            encoding="utf-8")

    def _assistant(self):
        cfg = config.load(self.root / "config.yaml")
        with unittest.mock.patch("src.engine.VoicePack.load_many") as load:
            pack = unittest.mock.MagicMock()
            pack.variants = {}
            pack.stats.return_value = {"msgIds": 0, "clips": 0, "avg_variants": 0}
            pack.sample_rate.return_value = 16000
            pack.has.return_value = False
            load.return_value = pack
            return Assistant(cfg, no_play=True, out=unittest.mock.MagicMock())

    def test_config_load_keeps_key_internally(self):
        cfg = config.load(self.root / "config.yaml")
        self.assertEqual(cfg["ai"]["api_key"], self.SECRET)

    def test_config_view_never_exposes_key(self):
        view = self._assistant().config_view()
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(self.SECRET, text)
        self.assertTrue(view["ai"]["api_key_set"])

    def test_tts_key_is_also_masked(self):
        view = self._assistant().config_view()
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(self.SECRET, text)
        self.assertTrue(view["ai"]["tts"]["api_key_set"])

    def test_ai_status_has_no_key_field(self):
        status = self._assistant().ai_status()
        self.assertNotIn("api_key", status)

    def test_ai_config_view_reports_presence_only(self):
        view = self._assistant().ai_config_view()
        self.assertIsInstance(view["api_key_set"], bool)
        self.assertNotIn(self.SECRET, json.dumps(view, ensure_ascii=False))



class ReviewShapeTests(unittest.TestCase):
    def test_review_rows_carry_id(self):
        """复盘条目必须带 id，否则界面无法标记。"""
        from app.server import Service
        import inspect
        source = inspect.getsource(Service.ai_review)
        self.assertIn('"id"', source)


class ConfigureAckTests(unittest.TestCase):
    """v1.5.1 补丁：等待语音开关 ai.ack.enabled 的保存路径。

    用轻量 Service（跳过 Assistant 初始化）验证 configure() 的 allowed /
    嵌套子键校验；真正的落盘与 validate 由既有配置测试覆盖。
    """

    def _service(self):
        from app.server import Service
        svc = object.__new__(Service)
        svc.cfg = config.load()
        svc.listener_lock = threading.RLock()
        svc._configure = unittest.mock.MagicMock(
            return_value={"ok": True, "saved": "x", "restart_hint": [],
                          "applied": {}})
        return svc

    def test_configure_accepts_ack_enabled(self):
        svc = self._service()
        result = svc.configure({"ai": {"ack": {"enabled": False}}})
        self.assertTrue(result["ok"])
        svc._configure.assert_called_once()
        self.assertEqual(svc._configure.call_args.args[0],
                         {"ai": {"ack": {"enabled": False}}})

    def test_configure_rejects_unknown_ack_key(self):
        svc = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"ai": {"ack": {"play_sound": True}}})
        svc._configure.assert_not_called()

    def test_configure_rejects_ack_not_object(self):
        svc = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"ai": {"ack": False}})
        svc._configure.assert_not_called()

    def test_configure_accepts_20_percent_opacity(self):
        """透明度口径已统一为 20%（0.2–1.0），保存 0.2 不能再被旧下限拒绝。"""
        from app.server import Service
        svc = object.__new__(Service)
        svc.cfg = config.load()
        svc.listener_lock = threading.RLock()
        svc.overlay_controller = None
        svc._configure = unittest.mock.MagicMock(
            return_value={"ok": True, "saved": "x", "restart_hint": [],
                          "applied": {}})
        result = svc.configure({"overlay": {"opacity": 0.2}})
        self.assertTrue(result["ok"])
        svc._configure.assert_called_once()

    def test_configure_rejects_19_percent_opacity(self):
        from app.server import Service
        svc = object.__new__(Service)
        svc.cfg = config.load()
        svc.listener_lock = threading.RLock()
        svc.overlay_controller = None
        svc._configure = unittest.mock.MagicMock(return_value={"ok": True})
        with self.assertRaises(ValueError):
            svc.configure({"overlay": {"opacity": 0.19}})
        svc._configure.assert_not_called()


class OverlayAndModeShapeTests(unittest.TestCase):
    """v1.5.1：AI 接管模式 all + 游戏内 Overlay 的既有接口面不能漏。

    真实行为在 tests/test_overlay_api.py 里用假 controller 测；
    这里只守住 Service 这类「配置允许字段 / 状态摘要」的结构性契约。
    """

    def test_ai_mode_all_is_supported(self):
        from src import config as cfg_mod
        self.assertIn("all", cfg_mod._AI_MODES)

    def test_configure_allows_overlay_section(self):
        from app.server import Service
        import inspect
        source = inspect.getsource(Service.configure)
        for key in ('"overlay"', '"enabled"', '"opacity"', '"corner"',
                    '"margin"', '"width"', '"height"', '"toggle_key"'):
            self.assertIn(key, source)

    def test_state_exposes_overlay_summary(self):
        from app.server import Service
        import inspect
        source = inspect.getsource(Service.state)
        self.assertIn('"overlay"', source)


if __name__ == '__main__':
    unittest.main()
