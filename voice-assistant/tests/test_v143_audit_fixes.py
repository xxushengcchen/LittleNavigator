"""v1.4.3 实测报告的回归测试。

每一条都对应 audit/v1.4.3-user-test/实测报告.md 里的一个可复现问题。
这些用例的价值不在「证明代码能跑」，而在把已经踩过的坑钉死：
改车解析和安全开关是这一个版本最危险的部分，退化一次就是换错轮胎。
"""
import io
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src import car_control as cc
from src.car_control import CarController
from src.command_match import match


class NegationTests(unittest.TestCase):
    """#1 取消/否定指令被反向解析成执行。"""

    def test_clear_fast_repair(self):
        for text in ("取消快修", "不要快修", "不用快修"):
            with self.subTest(text=text):
                self.assertEqual(match(text)["kind"], "clear_fr")

    def test_clear_fuel(self):
        for text in ("不要加油", "不加油", "取消加油"):
            with self.subTest(text=text):
                self.assertEqual(match(text)["kind"], "clear_fuel")

    def test_clear_tearoff(self):
        for text in ("别撕膜", "不要撕膜", "取消撕膜"):
            with self.subTest(text=text):
                self.assertEqual(match(text)["kind"], "clear_ws")

    def test_positive_commands_unchanged(self):
        self.assertEqual(match("快修")["kind"], "fast_repair")
        self.assertEqual(match("撕膜")["kind"], "tearoff")
        self.assertEqual(match("换左前胎")["corners"], ["LF"])


class LitreParsingTests(unittest.TestCase):
    """#2 加油数值的小数点与负号被吞掉。"""

    def test_decimal_is_kept(self):
        self.assertEqual(match("加1.5升油")["litres"], 1.5)

    def test_chinese_decimal(self):
        self.assertEqual(match("加零点五升油")["litres"], 0.5)
        self.assertEqual(match("加一点五升")["litres"], 1.5)

    def test_negative_is_refused(self):
        result = match("加-10升油")
        self.assertEqual(result["kind"], "unsupported")
        self.assertIn("负数", result["reason"])

    def test_zero_is_refused(self):
        self.assertEqual(match("加0升")["kind"], "unsupported")

    def test_over_max_is_refused_not_clamped(self):
        result = match("加999升")
        self.assertEqual(result["kind"], "unsupported")
        self.assertIn("超出", result["reason"])


class CornerScopeTests(unittest.TestCase):
    """#3 限定换胎范围时悄悄扩大执行范围。"""

    def test_left_side_only(self):
        self.assertEqual(match("只换左边轮胎")["corners"], ["LF", "LR"])

    def test_negated_corner_excluded(self):
        self.assertEqual(match("换左前胎，不要换右前胎")["corners"], ["LF"])

    def test_front_pair(self):
        self.assertEqual(match("换前胎")["corners"], ["LF", "RF"])

    def test_all_four_still_allowed(self):
        self.assertEqual(match("换胎")["corners"], ["LF", "RF", "LR", "RR"])


class ClearActionExecutionTests(unittest.TestCase):
    """#1 的落地部分：取消动作要发对指令码。"""

    class FakeSdk:
        def __init__(self):
            self.calls = []
            self.is_initialized = True
            self.is_connected = True

        def pit_command(self, mode, var=0):
            self.calls.append((mode, var))

    def _controller(self):
        sdk = self.FakeSdk()
        c = CarController({"car_control": {"enabled": True}}, sdk=sdk,
                          out=io.StringIO())
        return c, sdk

    def test_clear_fr_sends_correct_mode(self):
        c, sdk = self._controller()
        c.apply({"kind": "clear_fr"})
        self.assertEqual(sdk.calls, [(cc.PIT_CLEAR_FR, 0)])

    def test_clear_fuel_sends_correct_mode(self):
        c, sdk = self._controller()
        c.apply({"kind": "clear_fuel"})
        self.assertEqual(sdk.calls, [(cc.PIT_CLEAR_FUEL, 0)])

    def test_clear_ws_sends_correct_mode(self):
        c, sdk = self._controller()
        c.apply({"kind": "clear_ws"})
        self.assertEqual(sdk.calls, [(cc.PIT_CLEAR_WS, 0)])


class SwitchHotReloadTests(unittest.TestCase):
    """#4 AI/改车开关保存后不生效。"""

    def _assistant(self, tmp, cc_enabled=False):
        from src.engine import Assistant
        from tests.test_engine_car import make_cfg

        root = pathlib.Path(tmp).resolve()
        cfg = make_cfg(root, cc_enabled)
        from tests.test_engine_car import fake_pack

        with patch("src.engine.VoicePack.load_many", return_value=fake_pack()):
            with patch("src.engine.Assistant._build_ai", return_value=None):
                a = Assistant(cfg, no_play=True, out=io.StringIO())
        return a

    def test_reload_rebuilds_car_controller(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._assistant(tmp)
            self.assertFalse(bool(a.car and a.car.enabled))
            new_cfg = dict(a.cfg)
            new_cfg["car_control"] = {"enabled": True}
            a.reload_switchables(new_cfg)
            self.assertTrue(bool(a.car and a.car.enabled))

    def test_reload_can_disable(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._assistant(tmp, cc_enabled=True)
            self.assertTrue(bool(a.car and a.car.enabled))
            off = dict(a.cfg)
            off["car_control"] = {"enabled": False}
            a.reload_switchables(off)
            self.assertFalse(bool(a.car and a.car.enabled))


class EmptyTextGuardTests(unittest.TestCase):
    """#8 空识别仍请求大模型。"""

    def test_ai_not_engaged_for_blank_text(self):
        from src.engine import Assistant
        from src.query_class import QueryKind
        from tests.test_engine_car import make_cfg

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            cfg = make_cfg(root, False)
            cfg["ai"] = {"enabled": True}
            from tests.test_engine_car import fake_pack

            with patch("src.engine.VoicePack.load_many", return_value=fake_pack()):
                with patch("src.engine.Assistant._build_ai",
                           return_value=MagicMock(mode="full", whitelist=[])):
                    a = Assistant(cfg, no_play=True, out=io.StringIO())
            self.assertFalse(a._ai_should_engage(QueryKind.UNKNOWN, "   "))
            self.assertFalse(a._ai_should_engage(QueryKind.UNKNOWN, ""))


class TtsTests(unittest.TestCase):
    """#6 合成成功但没播放；#7 OpenAI 音色字段不生效。"""

    def test_openai_uses_speaker_when_voice_empty(self):
        from src.tts_engine import TtsEngine

        engine = TtsEngine("openai", {"base_url": "http://127.0.0.1:1",
                                      "api_key": "k", "speaker": "nova"})
        captured = {}

        def fake_post(url, body, headers, timeout):
            captured.update(body)
            return b"x" * 2048

        with patch("src.tts_engine._post", side_effect=fake_post):
            engine._synth_openai("测试一句")
        self.assertEqual(captured.get("voice"), "nova")

    def test_openai_prefers_voice_over_speaker(self):
        from src.tts_engine import TtsEngine

        engine = TtsEngine("openai", {"base_url": "http://127.0.0.1:1",
                                      "api_key": "k", "voice": "shimmer",
                                      "speaker": "nova"})
        captured = {}

        def fake_post(url, body, headers, timeout):
            captured.update(body)
            return b"x" * 2048

        with patch("src.tts_engine._post", side_effect=fake_post):
            engine._synth_openai("测试一句")
        self.assertEqual(captured.get("voice"), "shimmer")

    def test_synth_audio_is_handed_to_player(self):
        from src.ai_pipeline import AiPipeline, AiResult
        from tests.test_engine_car import make_cfg

        played = []

        class FakePlayer:
            def play_bytes(self, blob):
                played.append(blob)

        class FakeTts:
            def synth(self, text):
                return b"RIFF-fake-wav"

        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(pathlib.Path(tmp).resolve(), False)
            pipe = AiPipeline(cfg, out=io.StringIO(), player=FakePlayer())
            pipe.tts = FakeTts()
            result = AiResult(ok=True, text="答案")
            pipe._maybe_speak("答案", result)

        self.assertEqual(played, [b"RIFF-fake-wav"])
        self.assertEqual(result.checks.get("tts"), "ok")


if __name__ == "__main__":
    unittest.main()
