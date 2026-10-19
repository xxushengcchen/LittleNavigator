import time
import unittest
from src.telemetry import SimTelemetry, build


def _wait_laps(s: SimTelemetry, laps: int = 1, timeout: float = 3.0) -> bool:
    """等模拟圈数推进 laps 圈；超时返回 False。

    原先用固定的 time.sleep(0.06) 等圈推进。跑全量测试时机器繁忙，
    调度延迟会让这几条用例偶发失败（同一提交反复跑会随机红某一条）。
    改成轮询到条件成立，就与负载无关了。
    """
    t0 = time.monotonic()
    start = s.read().lap
    while time.monotonic() - t0 < timeout:
        if s.read().lap - start >= laps:
            return True
        time.sleep(0.005)
    return False


class SimAdvanceTests(unittest.TestCase):
    def test_default_sim_lap_never_advances(self):
        s = SimTelemetry()
        first = s.read().lap
        time.sleep(0.05)
        self.assertEqual(s.read().lap, first)

    def test_advance_mode_increments_lap(self):
        s = SimTelemetry(advance=True, lap_seconds=0.02)
        first = s.read().lap
        self.assertTrue(_wait_laps(s, 1), "模拟圈数在 3 秒内没有推进")
        self.assertGreater(s.read().lap, first)

    def test_advance_also_advances_completed_and_burns_fuel(self):
        s = SimTelemetry(advance=True, lap_seconds=0.02)
        first = s.read()
        self.assertTrue(_wait_laps(s, 1), "模拟圈数在 3 秒内没有推进")
        later = s.read()
        self.assertGreater(later.lap_completed, first.lap_completed)
        self.assertLess(later.fuel_level, first.fuel_level)

    def test_advance_never_drives_fuel_below_reserve(self):
        s = SimTelemetry(advance=True, lap_seconds=0.01)
        time.sleep(0.10)          # 足够跑很多圈
        self.assertGreaterEqual(s.read().fuel_level, 1.0)

    def test_build_passes_advance_flag(self):
        tel, _ = build({"telemetry": {"provider": "sim"}}, force_sim=True,
                       sim_advance=True)
        self.assertTrue(getattr(tel, "advance", False))

    def test_build_defaults_to_no_advance(self):
        tel, _ = build({"telemetry": {"provider": "sim"}}, force_sim=True)
        self.assertFalse(getattr(tel, "advance", True))

    def test_overrides_still_win_when_not_advancing(self):
        s = SimTelemetry(lap=40)
        self.assertEqual(s.read().lap, 40)


if __name__ == '__main__':
    unittest.main()
