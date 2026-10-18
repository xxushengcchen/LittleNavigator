import time
import unittest
from src.telemetry import SimTelemetry, build


class SimAdvanceTests(unittest.TestCase):
    def test_default_sim_lap_never_advances(self):
        s = SimTelemetry()
        first = s.read().lap
        time.sleep(0.05)
        self.assertEqual(s.read().lap, first)

    def test_advance_mode_increments_lap(self):
        s = SimTelemetry(advance=True, lap_seconds=0.02)
        first = s.read().lap
        time.sleep(0.06)
        self.assertGreater(s.read().lap, first)

    def test_advance_also_advances_completed_and_burns_fuel(self):
        s = SimTelemetry(advance=True, lap_seconds=0.02)
        first = s.read()
        time.sleep(0.06)
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
