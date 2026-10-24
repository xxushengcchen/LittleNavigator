"""引擎层的改车链路：从一句话到「发指令 → 回读 → 播确认」。

重点验证的是**失败时不会假装成功**：
未启用、未连接、游戏没受理——三种情况都必须播「没改成」而不是确认音。
"""
import io
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.engine import Assistant


def make_cfg(root: pathlib.Path, cc_enabled: bool, verify: bool = True) -> dict:
    return {
        "voice_pack": {"dir": "x", "extra_dir": None, "telemetry_dir": None,
                       "cooldown": 0, "state_file": str(root / "cd.json")},
        "telemetry": {"provider": "sim", "poll_hz": 1,
                      "ring_buffer": {"keep_laps": 5}},
        "intent": {"l1_keyword": {"enabled": False}, "voice_strict": True,
                   "miss_log": {"enabled": False, "path": str(root / "m.jsonl")}},
        "playback": {"enabled": False, "volume": 1.0},
        "logging": {"dir": str(root / "logs"),
                    "qa_log": str(root / "logs" / "qa.jsonl")},
        "asr": {"model": "sense-voice", "num_threads": 1, "models_dir": "m"},
        "ptt": {"vk": 0xA3, "poll_ms": 10, "min_hold_ms": 250, "max_hold_s": 8},
        "car_control": {"enabled": cc_enabled, "verify": verify},
        "ai": {"enabled": False},
        "_root": str(root), "_local_path": str(root / "l.json"),
        "_ai_learn_dir": str(root / "ai_learn"),
    }


def fake_pack(available=()):
    pack = MagicMock()
    pack.variants = {}
    pack.stats.return_value = {"msgIds": 0, "clips": 0, "avg_variants": 0}
    pack.sample_rate.return_value = 16000
    pack.has.side_effect = lambda m: m in available
    return pack


class FakeSdk:
    def __init__(self, flags=0xFF, connected=True):
        self.calls = []
        self.is_initialized = connected
        self.is_connected = connected
        self.flags = flags

    def pit_command(self, mode, var=0):
        self.calls.append((mode, var))
        if mode == 2:
            self.fuel = var


class EngineCarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def _assistant(self, cc_enabled=True, sdk=None, available=(), verify=True):
        with patch("src.engine.VoicePack.load_many",
                   return_value=fake_pack(available)):
            a = Assistant(make_cfg(self.root, cc_enabled, verify),
                          no_play=True, out=io.StringIO())
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(intent=None, score=0.2,
                                                 phrase="", margin=0.0,
                                                 reason="未命中", ranked=[])
        if sdk is not None:
            a.car.sdk = sdk
        return a

    def _ask(self, a, text):
        played = []
        with patch.object(a, "_say", side_effect=lambda m: played.append(m)), \
             patch.object(a, "snapshot") as snap:
            def current_snapshot():
                s = MagicMock()
                s.pit_sv_flags = getattr(a.car.sdk, "flags", 0) if a.car else 0
                s.pit_sv_fuel = getattr(a.car.sdk, "fuel", None) if a.car else None
                return s
            snap.side_effect = current_snapshot
            rec = a.ask(text, source="voice")
        return rec, played

    # ---------------------------------------------------------------- 成功路径
    def test_tyre_command_sends_and_confirms(self):
        sdk = FakeSdk(flags=0x01)
        a = self._assistant(sdk=sdk, available={"AST_CC_TYRE"})
        rec, played = self._ask(a, "换左前胎")
        self.assertTrue(rec["ok"])
        self.assertEqual(sdk.calls, [(7, 0), (3, 0)])  # 清掉其他胎，再选 LF
        self.assertEqual(played, ["AST_CC_TYRE"])

    def test_fuel_command_passes_litres(self):
        sdk = FakeSdk(flags=0x10)
        a = self._assistant(sdk=sdk, available={"AST_CC_FUEL"})
        rec, played = self._ask(a, "加二十升油")
        self.assertTrue(rec["ok"])
        self.assertEqual(sdk.calls, [(2, 20)])
        self.assertEqual(played, ["AST_CC_FUEL"])

    def test_fast_repair_and_tearoff(self):
        for text, mode, msg in (("快修", 8, "AST_CC_FR"),
                                ("撕膜", 1, "AST_CC_WS")):
            with self.subTest(text=text):
                sdk = FakeSdk(flags=0xFF)
                a = self._assistant(sdk=sdk, available={msg})
                rec, played = self._ask(a, text)
                self.assertTrue(rec["ok"])
                self.assertEqual(sdk.calls, [(mode, 0)])
                self.assertEqual(played, [msg])

    def test_clear_command(self):
        sdk = FakeSdk(flags=0)
        a = self._assistant(sdk=sdk, available={"AST_CC_CLEAR"})
        rec, played = self._ask(a, "清空进站")
        self.assertTrue(rec["ok"])
        self.assertEqual(sdk.calls, [(0, 0)])
        self.assertEqual(played, ["AST_CC_CLEAR"])

    # ---------------------------------------------------------------- 失败路径
    def test_disabled_sends_nothing_and_says_cant(self):
        sdk = FakeSdk(flags=0xFF)
        a = self._assistant(cc_enabled=False, sdk=sdk,
                            available={"AST_NO_CAR_CONTROL"})
        rec, played = self._ask(a, "换胎")
        self.assertFalse(rec["ok"])
        self.assertEqual(sdk.calls, [])
        self.assertEqual(played, ["AST_NO_CAR_CONTROL"])

    def test_game_ignores_command_reports_failure(self):
        # 游戏没点亮勾选位 —— 绝不能播确认音
        sdk = FakeSdk(flags=0x00)
        a = self._assistant(sdk=sdk, available={"AST_CC_FAIL", "AST_CC_TYRE"})
        rec, played = self._ask(a, "换左前胎")
        self.assertFalse(rec["ok"])
        self.assertEqual(sdk.calls, [(7, 0), (3, 0)])  # 发了，但游戏未受理
        self.assertEqual(played, ["AST_CC_FAIL"])      # 但没说成功

    def test_verify_disabled_trusts_send(self):
        sdk = FakeSdk(flags=0x00)
        a = self._assistant(sdk=sdk, verify=False,
                            available={"AST_CC_TYRE", "AST_CC_FAIL"})
        rec, played = self._ask(a, "换左前胎")
        self.assertTrue(rec["ok"])
        self.assertEqual(played, ["AST_CC_TYRE"])

    def test_compound_request_never_touches_sdk(self):
        sdk = FakeSdk(flags=0xFF)
        a = self._assistant(sdk=sdk, available={"AST_CC_COMPOUND"})
        rec, played = self._ask(a, "设置成软胎")
        self.assertFalse(rec["ok"])
        self.assertEqual(sdk.calls, [])                # 一个字节都没发
        self.assertEqual(played, ["AST_CC_COMPOUND"])
        self.assertIn("配方", rec["reason"])

    def test_compound_request_uses_dedicated_message(self):
        sdk = FakeSdk(flags=0xFF)
        a = self._assistant(sdk=sdk, available={"AST_CC_COMPOUND"})
        rec, played = self._ask(a, "换雨胎")
        self.assertFalse(rec["ok"])
        self.assertEqual(sdk.calls, [])
        self.assertEqual(played, ["AST_CC_COMPOUND"])

    def test_car_setup_request_never_touches_sdk(self):
        sdk = FakeSdk(flags=0xFF)
        a = self._assistant(sdk=sdk, available={"AST_NO_CAR_CONTROL"})
        rec, _ = self._ask(a, "调刹车平衡")
        self.assertFalse(rec["ok"])
        self.assertEqual(sdk.calls, [])

    def test_unrecognised_operation_falls_through_to_refusal(self):
        # 「帮我分析一下处罚原因」之类不是改车指令，不能误执行
        sdk = FakeSdk(flags=0xFF)
        a = self._assistant(sdk=sdk, available={"AST_REFUSE_CANT"})
        rec, played = self._ask(a, "帮我分析处罚原因")
        self.assertEqual(sdk.calls, [])

    # ---------------------------------------------------------------- 不干扰规则层
    def test_normal_question_does_not_trigger_car_control(self):
        sdk = FakeSdk(flags=0xFF)
        a = self._assistant(sdk=sdk, available=set())
        rec, _ = self._ask(a, "现在第几名")
        self.assertEqual(sdk.calls, [])
        self.assertNotIn("cc_action", rec)

    def test_question_about_refuelling_is_not_a_command(self):
        sdk = FakeSdk(flags=0xFF)
        a = self._assistant(sdk=sdk, available=set())
        self._ask(a, "要不要加油")
        self.assertEqual(sdk.calls, [])


if __name__ == '__main__':
    unittest.main()
