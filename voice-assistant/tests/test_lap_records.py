import unittest
from src.telemetry import DerivedTracker, Snapshot, FLAG_BITS


def snap(**kw):
    base = dict(connected=True, source="test", session_id=(1, 0, 7),
                session_time=0.0, lap=1, lap_completed=0, fuel_level=30.0,
                on_pit_road=False, in_garage=False,
                session_flags=FLAG_BITS["green"], position=4)
    base.update(kw)
    return Snapshot(**base)


class LapRecordTests(unittest.TestCase):
    def test_new_lap_appends_a_record(self):
        t = DerivedTracker()
        t.update(snap(lap=1, session_time=0.0, fuel_level=30.0))
        t.update(snap(lap=2, session_time=83.5, fuel_level=28.6, lap_completed=1))
        self.assertEqual(len(t.lap_records), 1)
        self.assertEqual(t.lap_records[0]["lap"], 1)

    def test_records_keep_only_last_twenty(self):
        t = DerivedTracker()
        for i in range(25):
            t.update(snap(lap=i + 1, session_time=i * 80.0,
                          fuel_level=30.0 - i * 1.4, lap_completed=i))
        self.assertEqual(len(t.lap_records), 20)

    def test_pit_lap_records_lap_but_no_fuel_use(self):
        t = DerivedTracker()
        t.update(snap(lap=1, fuel_level=30.0))
        t.update(snap(lap=2, fuel_level=28.6, lap_completed=1, on_pit_road=True))
        # 跨线时在维修区 → 圈速不可信，整圈不记
        self.assertEqual(len(t.lap_records), 0)

    def test_refuelling_lap_keeps_record_but_fuel_used_is_none(self):
        # 中途加油：油量回升，_valid_lap 被置假，但圈速/胎温仍然有效
        t = DerivedTracker()
        t.update(snap(lap=1, fuel_level=30.0))
        t.update(snap(lap=1, fuel_level=31.0, lap_completed=0))   # 加油导致 _valid_lap=False
        t.update(snap(lap=2, lap_completed=1, fuel_level=30.5,
                      lap_last=83.1, session_time=83.0))
        self.assertEqual(len(t.lap_records), 1)
        self.assertIsNone(t.lap_records[0]["fuel_used"])           # 油耗不可用
        self.assertEqual(t.lap_records[0]["lap_time"], 83.1)       # 但圈速仍记录

    def test_session_reset_clears_records(self):
        t = DerivedTracker()
        t.update(snap(lap=1, session_id=(1, 0, 7), fuel_level=30.0))
        t.update(snap(lap=2, session_id=(1, 0, 7), fuel_level=28.6, lap_completed=1))
        self.assertEqual(len(t.lap_records), 1)
        t.update(snap(lap=1, session_id=(2, 0, 7), fuel_level=30.0))
        self.assertEqual(len(t.lap_records), 0)

    def test_record_is_written_after_gaps_are_derived(self):
        # 圈末记录必须带上该帧算出的 gap，而不是上一帧的。
        # 「前车」= 赛道位置百分比比我们大的车：car_idx=0 在 0.48，前车 i=1 在 0.50。
        t = DerivedTracker()
        s = snap(lap=1, fuel_level=30.0, car_idx=0,
                 car_lap_dist=[0.48, 0.50], car_est_times=[10.0, 12.0],
                 gap_ahead_s=None)
        t.update(s)
        s2 = snap(lap=2, lap_completed=1, fuel_level=28.6, car_idx=0,
                  car_lap_dist=[0.48, 0.50], car_est_times=[11.0, 13.0],
                  gap_ahead_s=None)
        t.update(s2)
        self.assertEqual(len(t.lap_records), 1)
        self.assertIsNotNone(t.lap_records[0]["gap_ahead_s"])

    def test_skip_lap_number_is_not_recorded(self):
        t = DerivedTracker()
        t.update(snap(lap=1, fuel_level=30.0))
        t.update(snap(lap=4, lap_completed=3, fuel_level=25.0))    # 跳圈
        self.assertEqual(len(t.lap_records), 0)

    def test_valid_lap_property_is_public(self):
        t = DerivedTracker()
        self.assertFalse(t.valid_lap)
        t.update(snap(lap=1, fuel_level=30.0))
        t.update(snap(lap=2, fuel_level=28.6, lap_completed=1))
        self.assertTrue(t.valid_lap)

    def test_record_lap_accepts_trace_for_future_use(self):
        t = DerivedTracker()
        t.record_lap({"lap": 1}, trace=[(0.0, 0.0), (0.5, 40.0)])
        self.assertEqual(len(t.lap_records), 1)


if __name__ == '__main__':
    unittest.main()
