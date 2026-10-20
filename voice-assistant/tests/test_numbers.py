"""数值口语化：seconds_to_spoken 的格式与边界。

背景（v1.5.2 问题 2）：模型会把 lap_last=83.512 直接念成「八十三点五秒」，
车手期望的是「一分二十三点五秒」。T2 在 AI 回答后处理里调用本函数，
race_context 的 `_spoken` 字段也复用它，所以格式必须稳定。
"""
import unittest

from src.numbers import seconds_to_spoken


class SecondsToSpokenTests(unittest.TestCase):
    def test_under_a_minute_keeps_seconds(self):
        self.assertEqual(seconds_to_spoken(45.2), "45.2秒")
        self.assertEqual(seconds_to_spoken(59.9), "59.9秒")
        self.assertEqual(seconds_to_spoken(0), "0.0秒")
        self.assertEqual(seconds_to_spoken(1), "1.0秒")

    def test_over_a_minute_uses_minutes_and_seconds(self):
        self.assertEqual(seconds_to_spoken(83.512), "1分23.5秒")
        self.assertEqual(seconds_to_spoken(125.3), "2分5.3秒")
        self.assertEqual(seconds_to_spoken(750), "12分30.0秒")
        self.assertEqual(seconds_to_spoken(60), "1分0.0秒")

    def test_seconds_rounding_carries_into_minute(self):
        # 59.96 不是「0分60.0秒」，必须进位成 1 分
        self.assertEqual(seconds_to_spoken(59.96), "1分0.0秒")
        self.assertEqual(seconds_to_spoken(119.97), "2分0.0秒")

    def test_digits_zero_omits_fraction(self):
        self.assertEqual(seconds_to_spoken(83.512, 0), "1分24秒")
        self.assertEqual(seconds_to_spoken(45.2, 0), "45秒")
        self.assertEqual(seconds_to_spoken(750, 0), "12分30秒")
        self.assertEqual(seconds_to_spoken(59.6, 0), "1分0秒")

    def test_digits_two_keeps_two_decimals(self):
        self.assertEqual(seconds_to_spoken(83.512, 2), "1分23.51秒")
        self.assertEqual(seconds_to_spoken(45.2, 2), "45.20秒")

    def test_integer_input_is_accepted(self):
        self.assertEqual(seconds_to_spoken(83), "1分23.0秒")

    def test_invalid_value_raises(self):
        for bad in (-0.1, -60, "83.5", None, [], float("nan"),
                    float("inf"), float("-inf"), True):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    seconds_to_spoken(bad)

    def test_invalid_digits_raises(self):
        for bad in (-1, 4, 1.5, True, "1", None):
            with self.subTest(digits=bad):
                with self.assertRaises(ValueError):
                    seconds_to_spoken(83.5, bad)


if __name__ == "__main__":
    unittest.main()
