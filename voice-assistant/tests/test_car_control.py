"""语音改车：执行层与指令解析。

这是全项目唯一会写游戏状态的地方，所以测试盯三件事：
  1. 未启用 / 未连接时**绝不**发指令；
  2. 发的数值必须与 iRacing SDK 定义一致（错一位就是改错服务）；
  3. 「发送成功」不等于「生效」——回读没点亮对应位就不能说成功。
"""
import io
import unittest

from src import car_control as cc
from src.car_control import CarControlError, CarController
from src.command_match import clamp_litres, match, parse_litres


class FakeSdk:
    """记录调用的 pyirsdk 替身。"""

    def __init__(self, flags_fn=None, connected=True):
        self.calls = []
        self.is_initialized = connected
        self.is_connected = connected
        self._flags_fn = flags_fn or (lambda: 0xFF)

    def pit_command(self, mode, var=0):
        self.calls.append((mode, var))
        return 0


def controller(sdk=None, enabled=True, verify=True):
    cfg = {"car_control": {"enabled": enabled, "verify": verify}}
    return CarController(cfg, sdk=sdk if sdk is not None else FakeSdk(),
                         out=io.StringIO())


class SdkConstantTests(unittest.TestCase):
    """数值必须与 iRacing SDK 官方定义一致。改错一位就是改错服务。"""

    def test_pit_command_mode_values(self):
        self.assertEqual(cc.PIT_CLEAR, 0)
        self.assertEqual(cc.PIT_WS, 1)
        self.assertEqual(cc.PIT_FUEL, 2)
        self.assertEqual(cc.PIT_LF, 3)
        self.assertEqual(cc.PIT_RF, 4)
        self.assertEqual(cc.PIT_LR, 5)
        self.assertEqual(cc.PIT_RR, 6)
        self.assertEqual(cc.PIT_CLEAR_TIRES, 7)
        self.assertEqual(cc.PIT_FR, 8)

    def test_pitsvflags_values(self):
        self.assertEqual(cc.FLAG_TYRES, {"LF": 0x01, "RF": 0x02,
                                          "LR": 0x04, "RR": 0x08})
        self.assertEqual(cc.FLAG_FUEL, 0x10)
        self.assertEqual(cc.FLAG_WS, 0x20)
        self.assertEqual(cc.FLAG_FR, 0x40)


class AvailabilityTests(unittest.TestCase):
    def test_disabled_by_default(self):
        cfg = {"car_control": {}}
        self.assertFalse(CarController(cfg, sdk=FakeSdk()).enabled)

    def test_not_available_without_sdk(self):
        c = CarController({"car_control": {"enabled": True}}, sdk=None,
                          out=io.StringIO())
        self.assertFalse(c.available)

    def test_not_available_when_disconnected(self):
        c = controller(sdk=FakeSdk(connected=False))
        self.assertFalse(c.available)

    def test_available_when_enabled_and_connected(self):
        self.assertTrue(controller().available)

    def test_apply_refuses_when_disabled(self):
        c = controller(enabled=False)
        r = c.apply({"kind": "tyre", "corners": ["LF"]})
        self.assertFalse(r["ok"])
        self.assertEqual(c.sdk.calls, [])      # 一条都没发

    def test_apply_refuses_without_sdk(self):
        c = CarController({"car_control": {"enabled": True}}, sdk=None,
                          out=io.StringIO())
        r = c.apply({"kind": "tyre", "corners": ["LF"]})
        self.assertFalse(r["ok"])


class SendTests(unittest.TestCase):
    def test_single_corner(self):
        c = controller()
        r = c.apply({"kind": "tyre", "corners": ["LF"]})
        self.assertTrue(r["ok"])
        self.assertEqual(c.sdk.calls, [(cc.PIT_LF, 0)])

    def test_all_corners_sends_four_commands(self):
        c = controller()
        c.apply({"kind": "tyre", "corners": ["LF", "RF", "LR", "RR"]})
        self.assertEqual(c.sdk.calls,
                         [(cc.PIT_LF, 0), (cc.PIT_RF, 0),
                          (cc.PIT_LR, 0), (cc.PIT_RR, 0)])

    def test_fuel_with_litres(self):
        c = controller()
        c.apply({"kind": "fuel", "litres": 25})
        self.assertEqual(c.sdk.calls, [(cc.PIT_FUEL, 25)])

    def test_fuel_without_litres_uses_existing(self):
        # 广播 var=0 的语义是「沿用现有油量」
        c = controller()
        c.apply({"kind": "fuel", "litres": None})
        self.assertEqual(c.sdk.calls, [(cc.PIT_FUEL, 0)])

    def test_tearoff_and_fast_repair(self):
        c = controller()
        c.apply({"kind": "tearoff"})
        c.apply({"kind": "fast_repair"})
        self.assertEqual(c.sdk.calls, [(cc.PIT_WS, 0), (cc.PIT_FR, 0)])

    def test_clear_variants(self):
        c = controller()
        c.apply({"kind": "clear"})
        c.apply({"kind": "clear_tires"})
        self.assertEqual(c.sdk.calls, [(cc.PIT_CLEAR, 0), (cc.PIT_CLEAR_TIRES, 0)])

    def test_unknown_action_is_rejected(self):
        c = controller()
        r = c.apply({"kind": "engine_swap"})
        self.assertFalse(r["ok"])
        self.assertEqual(c.sdk.calls, [])

    def test_sdk_exception_becomes_error_result(self):
        class Broken(FakeSdk):
            def pit_command(self, mode, var=0):
                raise OSError("boom")
        c = controller(sdk=Broken())
        r = c.apply({"kind": "tyre", "corners": ["LF"]})
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["error"])


class VerifyTests(unittest.TestCase):
    """「发送成功」不等于「生效」。回读没点亮对应位就不能说成功。"""

    def test_verified_when_flags_light_up(self):
        c = controller()
        r = c.verify_applied({"kind": "tyre", "corners": ["LF"]},
                             lambda: 0x01)
        self.assertTrue(r["verified"])

    def test_not_verified_when_flag_missing(self):
        c = controller()
        r = c.verify_applied({"kind": "tyre", "corners": ["LF"]},
                             lambda: 0x00)
        self.assertFalse(r["verified"])

    def test_multi_corner_needs_all_bits(self):
        c = controller()
        action = {"kind": "tyre", "corners": ["LF", "RF"]}
        self.assertFalse(c.verify_applied(action, lambda: 0x01)["verified"])
        self.assertTrue(c.verify_applied(action, lambda: 0x03)["verified"])

    def test_unreadable_flags_is_unknown_not_success(self):
        c = controller()
        r = c.verify_applied({"kind": "fuel", "litres": 10}, lambda: None)
        self.assertIsNone(r["verified"])

    def test_clear_has_no_expected_flags(self):
        c = controller()
        self.assertEqual(c.expected_flags({"kind": "clear"}), 0)
        r = c.verify_applied({"kind": "clear"}, lambda: 0)
        self.assertIsNone(r["verified"])


class CommandMatchTests(unittest.TestCase):
    def test_tyre_all_when_unspecified(self):
        self.assertEqual(match("换胎")["corners"], ["LF", "RF", "LR", "RR"])

    def test_single_corner(self):
        self.assertEqual(match("换左前胎")["corners"], ["LF"])
        self.assertEqual(match("换右后")["corners"], ["RR"])

    def test_axis_phrasing(self):
        self.assertEqual(match("换前胎")["corners"], ["LF", "RF"])
        self.assertEqual(match("换后轮")["corners"], ["LR", "RR"])

    def test_all_corner_phrasing(self):
        for text in ("换四个", "换四条", "四个都换", "换全部"):
            with self.subTest(text=text):
                self.assertEqual(len(match(text)["corners"]), 4)

    def test_cancel_tyre_wins_over_change(self):
        # 「取消换胎」里含「换胎」，顺序错了就会变成换胎
        self.assertEqual(match("取消换胎")["kind"], "clear_tires")
        self.assertEqual(match("不要换胎")["kind"], "clear_tires")

    def test_fuel_quantities(self):
        self.assertEqual(match("加十升油")["litres"], 10.0)
        self.assertEqual(match("加10升")["litres"], 10.0)
        self.assertEqual(match("加一百二十升")["litres"], 120.0)
        self.assertIsNone(match("加油")["litres"])

    def test_tearoff_and_fast_repair(self):
        self.assertEqual(match("撕膜")["kind"], "tearoff")
        self.assertEqual(match("换挡风")["kind"], "tearoff")
        self.assertEqual(match("快修")["kind"], "fast_repair")

    def test_clear(self):
        self.assertEqual(match("清空进站")["kind"], "clear")

    def test_compound_is_unsupported_not_silently_tyre(self):
        # 用户要软胎，我们只勾「换胎」就是做错了还装作做对
        for text in ("换软胎", "设置成软胎", "换硬胎"):
            with self.subTest(text=text):
                r = match(text)
                self.assertEqual(r["kind"], "unsupported")
                self.assertIn("配方", r["reason"])

    def test_car_setup_is_unsupported(self):
        for text in ("调刹车平衡", "改尾翼角度", "调胎压"):
            with self.subTest(text=text):
                self.assertEqual(match(text)["kind"], "unsupported")

    def test_questions_are_not_commands(self):
        for text in ("要不要加油", "该不该换胎", "换胎吗", "油还够吗"):
            with self.subTest(text=text):
                self.assertIsNone(match(text))

    def test_normal_questions_do_not_match(self):
        for text in ("现在第几名", "我为什么慢了", "还有几圈"):
            with self.subTest(text=text):
                self.assertIsNone(match(text))


class AmountParsingTests(unittest.TestCase):
    def test_chinese_numerals(self):
        self.assertEqual(parse_litres("加十升油"), 10.0)
        self.assertEqual(parse_litres("加二十五升"), 25.0)
        self.assertEqual(parse_litres("加两升"), 2.0)

    def test_arabic_numerals(self):
        self.assertEqual(parse_litres("加 12.5 升"), 12.5)

    def test_missing_quantity(self):
        self.assertIsNone(parse_litres("加油"))

    def test_clamp_bounds(self):
        self.assertEqual(clamp_litres(0.5, 1, 120), 1.0)
        self.assertEqual(clamp_litres(500, 1, 120), 120.0)
        self.assertEqual(clamp_litres(30, 1, 120), 30.0)

    def test_clamp_rejects_nan_and_none(self):
        self.assertIsNone(clamp_litres(None, 1, 120))
        self.assertIsNone(clamp_litres(float("nan"), 1, 120))


if __name__ == '__main__':
    unittest.main()


class QueryVsCommandTests(unittest.TestCase):
    """提问不能被当成指令执行。

    真事：「还剩几次快修」曾经命中「快修」→ 引擎会真的执行一次快修。
    用户只是问还剩几次，结果把快修用掉了。这类误伤一条都不能有。
    """

    QUESTIONS = (
        "还剩几次快修",
        "快修还有吗",
        "还剩多少油",
        "还要不要加油",
        "换胎要多久",
        "撕膜还有几张",
        "我该不该换胎",
        "轮胎还剩几条",
        "这次要不要换胎",
    )

    def test_questions_are_not_commands(self):
        for text in self.QUESTIONS:
            with self.subTest(text=text):
                self.assertIsNone(match(text))

    def test_commands_still_match(self):
        for text in ("快修", "换胎", "加十升油", "撕膜", "清空进站", "换左前胎"):
            with self.subTest(text=text):
                self.assertIsNotNone(match(text))
