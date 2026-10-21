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

    def test_configure_accepts_disambiguate(self):
        """v1.5.5：AI 意图仲裁的开关/超时/置信度走同一条配置保存路径。"""
        svc = self._service()
        patch = {"ai": {"disambiguate": {"enabled": False,
                                          "timeout_s": 0.8,
                                          "min_confidence": 0.7}}}
        result = svc.configure(patch)
        self.assertTrue(result["ok"])
        svc._configure.assert_called_once()
        self.assertEqual(svc._configure.call_args.args[0], patch)

    def test_configure_rejects_unknown_disambiguate_key(self):
        svc = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"ai": {"disambiguate": {"enabled": True,
                                                   "temperature": 0}}})
        svc._configure.assert_not_called()

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


class TtsTestPlaybackTests(unittest.TestCase):
    """v1.5.2 问题 5：测试合成必须真的播放，并回报 played。

    旧实现只调 TtsEngine.test()，合成成功就 ok=true，音频字节被丢掉——
    界面显示「合成成功」，扬声器一声没有。测试按钮必须验证整条链路。
    """

    def _service(self, *, tts=None, player=None, enabled=True,
                 play_error=None):
        from app.server import Service
        import types
        svc = object.__new__(Service)
        if tts is None:
            tts = unittest.mock.MagicMock()
            tts.synth.return_value = b"x" * 600
        if player is None:
            player = unittest.mock.MagicMock()
            player.enabled = enabled
            if play_error is not None:
                player.play_bytes.side_effect = play_error
        svc.asst = types.SimpleNamespace(
            ai=types.SimpleNamespace(tts=tts), player=player)
        return svc, player, tts

    def test_success_plays_the_audio(self):
        svc, player, _ = self._service()
        result = svc.ai_tts_test()
        self.assertTrue(result["ok"])
        self.assertTrue(result["played"])
        self.assertEqual(result["bytes"], 600)
        self.assertIn("latency_ms", result)
        player.play_bytes.assert_called_once_with(b"x" * 600)

    def test_tts_not_configured(self):
        svc, player, _ = self._service(tts=None)
        svc.asst.ai.tts = None
        result = svc.ai_tts_test()
        self.assertFalse(result["ok"])
        self.assertFalse(result["played"])
        self.assertIn("未配置", result["error"])
        player.play_bytes.assert_not_called()

    def test_ai_pipeline_missing_is_reported(self):
        from app.server import Service
        import types
        svc = object.__new__(Service)
        svc.asst = types.SimpleNamespace(ai=None, player=unittest.mock.MagicMock())
        result = svc.ai_tts_test()
        self.assertFalse(result["ok"])
        self.assertFalse(result["played"])
        self.assertIn("error", result)

    def test_synth_failure_is_reported(self):
        tts = unittest.mock.MagicMock()
        tts.synth.side_effect = RuntimeError("boom")
        svc, player, _ = self._service(tts=tts)
        result = svc.ai_tts_test()
        self.assertFalse(result["ok"])
        self.assertFalse(result["played"])
        self.assertIn("boom", result["error"])
        player.play_bytes.assert_not_called()

    def test_play_failure_is_reported(self):
        svc, player, _ = self._service(play_error=RuntimeError("no device"))
        result = svc.ai_tts_test()
        self.assertFalse(result["ok"])
        self.assertFalse(result["played"])
        self.assertIn("no device", result["error"])

    def test_playback_disabled_is_not_reported_as_played(self):
        # playback.enabled=false 时合成会成功但没有声音，不能显示「已播放」。
        svc, player, _ = self._service(enabled=False)
        result = svc.ai_tts_test()
        self.assertFalse(result["ok"])
        self.assertFalse(result["played"])
        self.assertIn("播放已关闭", result["error"])
        player.play_bytes.assert_not_called()


class ConfigureV152Tests(unittest.TestCase):
    """v1.5.2 新配置字段必须进 configure 白名单，并且校验生效。"""

    def _service(self):
        from app.server import Service
        svc = object.__new__(Service)
        svc.cfg = config.load()
        svc.listener_lock = threading.RLock()
        svc.overlay_controller = None
        svc._configure = unittest.mock.MagicMock(
            return_value={"ok": True, "saved": "x", "restart_hint": [],
                          "applied": {}})
        return svc

    def test_accepts_global_stop_local_and_thinking_mode(self):
        svc = self._service()
        result = svc.configure({"ai": {"global_stop_local": False,
                                       "thinking_mode": "low"}})
        self.assertTrue(result["ok"])
        svc._configure.assert_called_once_with(
            {"ai": {"global_stop_local": False, "thinking_mode": "low"}})

    def test_rejects_bad_thinking_mode(self):
        svc = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"ai": {"thinking_mode": "high"}})
        svc._configure.assert_not_called()

    def test_rejects_non_bool_global_stop_local(self):
        svc = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"ai": {"global_stop_local": "yes"}})
        svc._configure.assert_not_called()

    def test_accepts_overlay_xy(self):
        svc = self._service()
        result = svc.configure({"overlay": {"x": -40, "y": 300,
                                            "width": 800, "height": 220}})
        self.assertTrue(result["ok"])
        svc._configure.assert_called_once()

    def test_rejects_out_of_range_overlay_x(self):
        svc = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"overlay": {"x": 20000}})
        svc._configure.assert_not_called()

    def test_rejects_unknown_overlay_key(self):
        svc = self._service()
        with self.assertRaises(ValueError):
            svc.configure({"overlay": {"z": 10}})
        svc._configure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
