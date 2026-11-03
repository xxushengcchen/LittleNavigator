"""T03/T08 regression: zero-based ClassPosition and no cross-session reuse."""
from __future__ import annotations

import unittest

from src.telemetry import IrsdkTelemetry, Snapshot, _NOT_SET


class FakeIr:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def __getitem__(self, key):
        if key in self.data:
            return self.data[key]
        raise KeyError(key)


def make_irsdk(data):
    tel = IrsdkTelemetry.__new__(IrsdkTelemetry)
    tel._ir = None
    tel._err = None
    tel._sector_key = _NOT_SET
    tel._sector_starts = ()
    return tel, FakeIr(data)


def snapshot(positions=(0, 0, 0)):
    return Snapshot(connected=True, car_idx=0,
                    car_lap_dist=[0.1, 0.2, 0.3],
                    car_positions=list(positions),
                    car_class_positions=[None] * len(positions),
                    car_last_laps=[None] * len(positions),
                    car_best_laps=[None] * len(positions),
                    car_completed_laps=[None] * len(positions),
                    field_size=1)


class ClassPositionTests(unittest.TestCase):
    def test_zero_based_class_position_becomes_one_based(self):
        tel, fake = make_irsdk({
            "SessionNum": 0,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [
                    {"CarIdx": 2, "Position": 1, "ClassPosition": 0},
                    {"CarIdx": 1, "Position": 2, "ClassPosition": 1},
                ],
            }]},
        })
        s = snapshot([1, 0, 0])
        tel._apply_session_results(s, fake)
        self.assertEqual(s.car_class_positions[2], 1, "组别第一不能被丢掉")
        self.assertEqual(s.car_class_positions[1], 2)

    def test_negative_class_position_is_not_written(self):
        tel, fake = make_irsdk({
            "SessionNum": 0,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [
                    {"CarIdx": 2, "Position": 1, "ClassPosition": -1},
                ],
            }]},
        })
        s = snapshot([1, 0, 0])
        tel._apply_session_results(s, fake)
        self.assertIsNone(s.car_class_positions[2])


class CrossSessionTests(unittest.TestCase):
    def test_results_from_other_session_are_not_used(self):
        tel, fake = make_irsdk({
            "SessionNum": 1,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [
                    {"CarIdx": 2, "Position": 1, "FastestTime": 80.0,
                     "LastTime": 81.0, "LapsComplete": 12},
                ],
            }]},
        })
        s = snapshot([0, 0, 0])
        tel._apply_session_results(s, fake)
        self.assertEqual(s.car_positions, [0, 0, 0], "不能借上一场名次")
        self.assertIsNone(s.car_best_laps[2], "不能借上一场最快圈")
        self.assertIsNone(s.car_completed_laps[2])

    def test_track_rubber_unknown_when_session_mismatch(self):
        tel, fake = make_irsdk({
            "SessionNum": 1,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "SessionTrackRubberState": "high_usage",
            }]},
        })
        row = next((r for r in fake["SessionInfo"]["Sessions"]
                    if r.get("SessionNum") == fake["SessionNum"]), None)
        self.assertIsNone(row, "精确匹配不到就等于未知，不回退到最后一场")

    def test_same_session_still_fills(self):
        tel, fake = make_irsdk({
            "SessionNum": 0,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [
                    {"CarIdx": 2, "Position": 1, "ClassPosition": 0,
                     "FastestTime": 80.0, "LapsComplete": 5},
                ],
            }]},
        })
        s = snapshot([0, 0, 0])
        tel._apply_session_results(s, fake)
        self.assertEqual(s.car_positions[2], 1)
        self.assertEqual(s.car_best_laps[2], 80.0)
        self.assertEqual(s.car_completed_laps[2], 5)


if __name__ == "__main__":
    unittest.main()
