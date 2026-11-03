"""T07/T09 regression: late official lap time backfill; neighbour crossing window."""
from __future__ import annotations

import unittest

from src.segments import SegmentModel
from src.telemetry import DerivedTracker, Snapshot


def _snap(t, lap, lap_last, *, pct=0.5, gap_ahead=None):
    return Snapshot(connected=True, session_time=t, lap=lap,
                    lap_completed=lap - 1, car_idx=0,
                    car_lap_dist=[pct], car_in_pits=[False],
                    on_pit_road=False, lap_last=lap_last,
                    fuel_level=30.0 - t * 0.01, sector_starts=(0.0, 0.5))


class LateLapTimeTests(unittest.TestCase):
    def test_stale_official_time_is_backfilled_when_it_arrives(self):
        tracker = DerivedTracker()
        # 第一圈：80 秒；跨线那一帧 LapLastLapTime 仍是上一圈的 80（旧值），
        # 实际这圈跑了 90 秒，90 秒在下一帧才到。
        for t in (0.0, 40.0, 80.0):
            tracker.update(_snap(t, 1, 80.0))
        tracker.update(_snap(90.0, 2, 80.0))          # 跨线：官方值还是旧的
        records = tracker.lap_records
        self.assertTrue(records, "应当已经记录这一圈")
        self.assertIsNone(records[0]["lap_time"], "旧值不能被当成这圈的成绩")
        tracker.update(_snap(95.0, 2, 90.0))          # 官方圈速晚到
        self.assertEqual(records[0]["lap_time"], 90.0, "晚到的官方圈速要回填")

    def test_never_arriving_time_stays_unknown(self):
        tracker = DerivedTracker()
        for t in (0.0, 40.0, 80.0):
            tracker.update(_snap(t, 1, 80.0))
        tracker.update(_snap(90.0, 2, 80.0))
        # 2.5 秒内一直没等到新的官方圈速 → 保持未知，不填旧值
        for t in (91.0, 92.0, 93.0, 94.0):
            tracker.update(_snap(t, 2, 80.0))
        self.assertIsNone(tracker.lap_records[0]["lap_time"])

    def test_matching_time_is_recorded_immediately(self):
        tracker = DerivedTracker()
        for t in (0.0, 40.0):
            tracker.update(_snap(t, 1, 80.0))
        tracker.update(_snap(80.0, 2, 80.0))          # 官方值正好等于本圈耗时
        self.assertEqual(tracker.lap_records[0]["lap_time"], 80.0)


class CrossingWindowTests(unittest.TestCase):
    def _model(self, ahead_pct, ahead_completed):
        model = SegmentModel()
        for step in range(0, 79):
            t = step * 1.0
            pct = (t % 80.0) / 80.0
            model.update(Snapshot(
                connected=True, session_time=t, lap=1 + int(t // 80.0),
                car_idx=0, car_lap_dist=[pct, ahead_pct],
                car_classes=[1, 1],
                car_completed_laps=[0, ahead_completed],
                gap_ahead_idx=1, sector_starts=(0.0, 0.5)))
        return model

    def test_crossing_neighbour_is_comparable(self):
        # 前车提前约 2 秒越线：玩家 0.975、前车 0.025，且圈数暂差 1
        model = self._model(ahead_pct=0.025, ahead_completed=1)
        self.assertEqual(model.target_for("ahead"), 1,
                         "跨线窗口里的近邻不该被当成套圈")

    def test_lapped_car_still_rejected(self):
        # 真正套圈：位置并排在中段，圈数差 1
        model = self._model(ahead_pct=0.5, ahead_completed=1)
        self.assertIsNone(model.target_for("ahead"))


if __name__ == "__main__":
    unittest.main()
