"""v1.5.4 遥测层单测：Snapshot 新字段、SplitTimeInfo 边界读取、模拟 4 段。

重点是两条边界：
  · `SplitTimeInfo` 读失败只能让分段功能失效，不能影响主遥测；
  · 模拟模式要能真的产出分段（--sim-advance），否则手工验证无从谈起。
"""
import time
import unittest

from src.segments import SegmentModel
from src.telemetry import (DerivedTracker, IrsdkTelemetry, SimTelemetry,
                           Snapshot, _NOT_SET)


class FakeIr:
    """最小 pyirsdk 替身：像 irsdk 一样按下标取 session YAML / 变量。"""

    def __init__(self, data=None):
        self.data = dict(data or {})
        self.reads = []

    def __getitem__(self, key):
        self.reads.append(key)
        if key in self.data:
            return self.data[key]
        raise KeyError(key)


def make_irsdk(data=None) -> tuple[IrsdkTelemetry, FakeIr]:
    """绕过真实共享内存，只测解析/缓存逻辑。"""
    tel = IrsdkTelemetry.__new__(IrsdkTelemetry)
    tel._ir = None
    tel._err = None
    tel._sector_key = _NOT_SET
    tel._sector_starts = ()
    fake = FakeIr(data)
    return tel, fake


def moving_frames(starts=(0.0, 0.25, 0.5, 0.75), duration=80.0, dt=0.25,
                  laps=3, pit_window=(), off_window=()):
    """生成一段 60Hz 风格的连续快照（单圈时长固定）。"""
    total = duration * laps
    t = 0.0
    frames = []
    while t < total - 1e-9:
        lap = 1 + int(t / duration)
        pct = (t % duration) / duration
        in_pit = any(a <= t < b for a, b in pit_window)
        off = any(a <= t < b for a, b in off_window)
        frames.append(Snapshot(
            connected=True, source="test", session_time=t, lap=lap,
            lap_completed=lap - 1, car_idx=0, car_lap_dist=[pct],
            car_completed_laps=[lap - 1], car_last_laps=[duration],
            car_in_pits=[in_pit], car_surfaces=[0 if off else 3],
            car_classes=[1], on_pit_road=in_pit, sector_starts=starts,
            lap_last=duration, fuel_level=30.0))
        t += dt
    # 圈末后补一帧，触发最后一圈结算
    frames.append(Snapshot(
        connected=True, source="test", session_time=total, lap=laps + 1,
        lap_completed=laps, car_idx=0, car_lap_dist=[0.0],
        car_completed_laps=[laps], car_last_laps=[duration],
        car_in_pits=[False], car_surfaces=[3], car_classes=[1],
        on_pit_road=False, sector_starts=starts, lap_last=duration,
        fuel_level=30.0))
    return frames


class SnapshotFieldTests(unittest.TestCase):
    def test_new_fields_default_to_unavailable(self):
        snap = Snapshot()
        self.assertEqual(snap.sector_starts, ())
        self.assertIsNone(snap.track_num_turns)
        self.assertIsNone(snap.car_classes)

    def test_snapshot_still_constructible_positionally(self):
        # 新字段都带默认值，既有位置参数调用方不受影响
        snap = Snapshot(True, "test", (1, 0, 0), 120.0)
        self.assertTrue(snap.connected)
        self.assertEqual(snap.sector_starts, ())


class SimTelemetryTests(unittest.TestCase):
    def test_sim_provides_four_fixed_sectors(self):
        snap = SimTelemetry().read()
        self.assertEqual(snap.sector_starts, (0.0, 0.25, 0.5, 0.75))
        self.assertEqual(len(snap.sector_starts), 4)

    def test_sim_provides_track_meta_for_comparisons(self):
        snap = SimTelemetry().read()
        self.assertIsInstance(snap.track_num_turns, int)
        self.assertGreater(snap.track_num_turns, 0)
        self.assertIsInstance(snap.car_classes, list)
        self.assertEqual(len(snap.car_classes), len(snap.car_lap_dist))

    def test_static_sim_keeps_legacy_values(self):
        # 不推进时行为必须与既有断言完全一致
        snap = SimTelemetry().read()
        self.assertIsNone(snap.session_time)
        self.assertEqual(snap.lap, 12)
        self.assertEqual(snap.lap_last, 83.512)
        self.assertEqual(snap.car_lap_dist[7], 0.22)

    def test_advance_moves_time_and_positions(self):
        sim = SimTelemetry(advance=True, lap_seconds=0.5)
        first = sim.read()
        time.sleep(0.08)
        second = sim.read()
        self.assertIsNotNone(first.session_time)
        self.assertGreater(second.session_time, first.session_time)
        self.assertNotEqual(second.car_lap_dist, first.car_lap_dist)
        # 模拟圈速和位置必须自洽，否则 Σ 校验永远不过
        self.assertEqual(second.lap_last, sim.lap_seconds)

    def test_advance_keeps_neighbors_in_same_lap_range(self):
        # 前后车（模拟数据里是 idx 2/4）与玩家的完成圈数最多差 1：
        # 差 0 是同圈缠斗，差 1 只会出现在刚跨线的瞬间——都不能是套圈。
        sim = SimTelemetry(advance=True, lap_seconds=0.3)
        time.sleep(0.7)
        snap = sim.read()
        for idx in (2, 4):
            self.assertLessEqual(
                abs(snap.car_completed_laps[idx] - snap.lap_completed), 1)


class IrsdkSectorStartsTests(unittest.TestCase):
    """SplitTimeInfo 解析与缓存；读失败必须静默降级。"""

    def test_parses_sector_start_pct(self):
        tel, fake = make_irsdk({"SplitTimeInfo": {"Sectors": [
            {"SectorNum": 0, "SectorStartPct": 0.0},
            {"SectorNum": 1, "SectorStartPct": 0.4},
            {"SectorNum": 2, "SectorStartPct": 0.7},
        ]}})
        starts = tel._session_sector_starts(fake, ("sid", 0, 7))
        self.assertEqual(starts, (0.0, 0.4, 0.7))

    def test_caches_per_session(self):
        tel, fake = make_irsdk({"SplitTimeInfo": {"Sectors": [
            {"SectorStartPct": 0.0}, {"SectorStartPct": 0.5}]}})
        key = ("sid", 0, 7)
        first = tel._session_sector_starts(fake, key)
        self.assertEqual(first, (0.0, 0.5))
        fake.reads.clear()
        second = tel._session_sector_starts(fake, key)
        self.assertEqual(second, first)
        self.assertNotIn("SplitTimeInfo", fake.reads)   # 命中缓存，不重复读

    def test_missing_key_degrades_to_empty(self):
        tel, fake = make_irsdk({})
        self.assertEqual(tel._session_sector_starts(fake, ("sid", 0, 7)), ())

    def test_malformed_value_degrades_to_empty_without_raising(self):
        for bad in ({"Sectors": "nope"}, {"Sectors": [None, "x", 1.5]}, []):
            with self.subTest(bad=bad):
                tel, fake = make_irsdk({"SplitTimeInfo": bad})
                self.assertEqual(tel._session_sector_starts(fake, ("sid", 0, 7)),
                                 ())

    def test_new_session_does_not_reuse_old_track_bounds(self):
        tel, fake = make_irsdk({"SplitTimeInfo": {"Sectors": [
            {"SectorStartPct": 0.0}, {"SectorStartPct": 0.5}]}})
        tel._session_sector_starts(fake, ("old", 0, 7))
        fake.data = {}                       # 换赛道后 SplitTimeInfo 暂时读不到
        self.assertEqual(tel._session_sector_starts(fake, ("new", 0, 7)), ())
        # 同一 session 内偶发读失败仍可回退已缓存值
        self.assertEqual(tel._session_sector_starts(fake, ("old", 0, 7)),
                         (0.0, 0.5))

    def test_empty_result_is_not_cached(self):
        tel, fake = make_irsdk({})
        tel._session_sector_starts(fake, ("sid", 0, 7))
        fake.data = {"SplitTimeInfo": {"Sectors": [
            {"SectorStartPct": 0.0}, {"SectorStartPct": 0.25}]}}
        self.assertEqual(tel._session_sector_starts(fake, ("sid", 0, 7)),
                         (0.0, 0.25))


class IrsdkReadDegradesTests(unittest.TestCase):
    """主遥测不能因为分段信息读不到而失败。"""

    def test_read_frozen_survives_missing_split_time_info(self):
        tel, fake = make_irsdk({
            "WeekendInfo": {"TrackNumTurns": 12},
            "CarIdxClass": [0, 1, 1],
        })
        snap = tel._read_frozen(fake)
        self.assertTrue(snap.connected)
        self.assertEqual(snap.sector_starts, ())       # 静默降级
        self.assertEqual(snap.car_classes, [0, 1, 1])  # 主遥测字段照常
        self.assertEqual(snap.track_num_turns, 12)
        self.assertIsNone(tel._err)

    def test_read_frozen_parses_split_time_info(self):
        tel, fake = make_irsdk({
            "SplitTimeInfo": {"Sectors": [{"SectorStartPct": 0.0},
                                          {"SectorStartPct": 0.5}]},
        })
        snap = tel._read_frozen(fake)
        self.assertEqual(snap.sector_starts, (0.0, 0.5))

    def test_read_frozen_tolerates_broken_split_time_info(self):
        tel, fake = make_irsdk({"SplitTimeInfo": "totally-broken"})
        snap = tel._read_frozen(fake)
        self.assertTrue(snap.connected)
        self.assertEqual(snap.sector_starts, ())


class DerivedTrackerSegmentsTests(unittest.TestCase):
    """圈记录挂分段；分段模型跟着 DerivedTracker 生命周期。"""

    def _tracker(self):
        return DerivedTracker()

    def test_lap_records_carry_sectors(self):
        tracker = self._tracker()
        for snap in moving_frames(laps=3):
            tracker.update(snap)
        records = {rec["lap"]: rec for rec in tracker.lap_records}
        self.assertIn(2, records)
        self.assertEqual(len(records[2]["sectors"]), 4)
        self.assertAlmostEqual(sum(records[2]["sectors"]), 80.0, places=3)
        # 中途开局的第 1 圈没有完整分段：明确给空 list，而不是半圈数据
        self.assertEqual(records[1]["sectors"], [])

    def test_pit_lap_has_no_sectors_but_record_kept(self):
        tracker = self._tracker()
        for snap in moving_frames(laps=3, pit_window=((85.0, 100.0),)):
            tracker.update(snap)
        records = {rec["lap"]: rec for rec in tracker.lap_records}
        self.assertIn(2, records)                 # 圈记录本身仍保留
        self.assertEqual(records[2]["sectors"], [])   # 但分段不可用

    def test_reset_clears_segment_model(self):
        tracker = self._tracker()
        for snap in moving_frames(laps=2):
            tracker.update(snap)
        self.assertTrue(tracker.segments.player_recent(3))
        tracker.reset()
        self.assertEqual(tracker.segments.player_recent(3), [])
        self.assertEqual(tracker.segments.num_sectors, 0)
        self.assertEqual(tracker.lap_records, [])

    def test_disconnect_resets_segment_model(self):
        tracker = self._tracker()
        for snap in moving_frames(laps=2):
            tracker.update(snap)
        tracker.update(Snapshot(connected=False))
        self.assertEqual(tracker.segments.player_recent(3), [])

    def test_sim_advance_produces_valid_sectors_end_to_end(self):
        """--sim-advance 端到端：模拟遥测真能跑出分段圈。

        lap_seconds=0.5、2ms 轮询：一圈约 250 个采样点，远多于每圈跨 3 次
        边界所需。轮询到出现有效圈为止，避免依赖固定 sleep 的时序。
        """
        sim = SimTelemetry(advance=True, lap_seconds=0.5)
        tracker = DerivedTracker()
        deadline = time.monotonic() + 12.0
        found = None
        while time.monotonic() < deadline:
            tracker.update(sim.read())
            for record in tracker.lap_records:
                if record.get("sectors"):
                    found = record
                    break
            if found is not None:
                break
            time.sleep(0.002)
        self.assertIsNotNone(found, "模拟模式 12 秒内没有产出有效分段圈")
        self.assertEqual(tracker.segments.num_sectors, 4)
        self.assertAlmostEqual(sum(found["sectors"]), found["lap_time"],
                               places=2)


class SessionResultsFallbackTests(unittest.TestCase):
    """v1.5.5 实测：CarIdxPosition 只给玩家 1，其余为 0。

    结果页有完整名次/圈速时必须补缺，否则「第一名最快圈」会选中玩家，
    「前车/后车」会找不到目标；正常会话则不能覆盖 SDK 的有效值。
    """

    def _snap(self, positions):
        return Snapshot(connected=True, car_idx=0,
                        car_lap_dist=[0.1, 0.2, 0.3],
                        car_positions=positions,
                        car_last_laps=[None, None, None],
                        car_best_laps=[None, None, None],
                        car_completed_laps=[None, None, None],
                        field_size=1)

    def test_fills_positions_and_laps_from_results(self):
        tel, fake = make_irsdk({
            "SessionNum": 0,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [
                    {"CarIdx": 2, "Position": 1, "ClassPosition": 1,
                     "FastestTime": 80.0, "LastTime": 81.0, "LapsComplete": 5},
                    {"CarIdx": 1, "Position": 2, "ClassPosition": 2,
                     "FastestTime": 82.0, "LastTime": 83.0, "LapsComplete": 5},
                ],
            }]},
        })
        s = self._snap([1, 0, 0])
        tel._apply_session_results(s, fake)
        self.assertEqual(s.field_size, 2)
        self.assertEqual(s.car_positions[2], 1)
        self.assertEqual(s.car_positions[1], 2)
        self.assertEqual(s.car_best_laps[2], 80.0)
        self.assertEqual(s.car_last_laps[1], 83.0)
        self.assertEqual(s.car_completed_laps[2], 5)

    def test_does_not_override_valid_sdk_positions(self):
        tel, fake = make_irsdk({
            "SessionNum": 0,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [{"CarIdx": 2, "Position": 9}],
            }]},
        })
        s = self._snap([2, 1, 3])
        tel._apply_session_results(s, fake)
        self.assertEqual(s.car_positions, [2, 1, 3])


if __name__ == "__main__":
    unittest.main()
