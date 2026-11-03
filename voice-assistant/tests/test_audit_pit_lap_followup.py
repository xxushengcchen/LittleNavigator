"""T05 regression: mid-lap pit road marks the whole lap as a pit lap."""
from __future__ import annotations

import unittest

from src.telemetry import DerivedTracker, Snapshot


def frame(t, lap, pit=False, stall=False):
    return Snapshot(connected=True, session_time=t, lap=lap,
                    lap_completed=lap - 1, car_idx=0,
                    car_lap_dist=[0.5], car_in_pits=[pit],
                    on_pit_road=pit, in_pit_stall=stall,
                    lap_last=80.0, fuel_level=30.0 - t * 0.01,
                    sector_starts=(0.0, 0.25, 0.5, 0.75))


def feed(tracker, frames):
    for snap in frames:
        tracker.update(snap)


class MidLapPitTests(unittest.TestCase):
    def _tracker(self):
        tracker = DerivedTracker()
        return tracker

    def test_mid_lap_pit_stop_marks_lap_as_pit(self):
        tracker = self._tracker()
        # 第 1 圈干净开始，中途进站（85-100 秒），跨线时已出站
        frames = [frame(0.0, 1), frame(40.0, 1), frame(80.0, 1),
                  frame(85.0, 1, pit=True), frame(92.0, 1, pit=True, stall=True),
                  frame(100.0, 1), frame(160.0, 2)]
        feed(tracker, frames)
        laps = tracker.lap_records
        self.assertTrue(laps, "应记录到一圈")
        self.assertTrue(laps[0]["in_pit"], "中途进站必须把这圈标成进站圈")

    def test_clean_lap_stays_clean(self):
        tracker = self._tracker()
        frames = [frame(0.0, 1), frame(40.0, 1), frame(80.0, 1), frame(160.0, 2)]
        feed(tracker, frames)
        laps = tracker.lap_records
        self.assertTrue(laps)
        self.assertFalse(laps[0]["in_pit"], "干净圈不能被误标进站")

    def test_pit_stall_without_pit_road_still_marks(self):
        tracker = self._tracker()
        frames = [frame(0.0, 1), frame(80.0, 1), frame(85.0, 1, stall=True),
                  frame(160.0, 2)]
        feed(tracker, frames)
        laps = tracker.lap_records
        self.assertTrue(laps)
        self.assertTrue(laps[0]["in_pit"], "停在维修位同样算进站圈")


if __name__ == "__main__":
    unittest.main()
