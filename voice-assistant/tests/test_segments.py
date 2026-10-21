"""v1.5.4 赛段计时核心单测：SegmentModel / SectorLap。

覆盖：边界数量可变、60Hz 插值精度（含跨采样帧与圈末回绕）、Σ 校验、
进站/出界/圈速离群/跳圈过滤、前后车对比过滤、空数据。
"""
import unittest

from src.segments import (LAP_TIME_TOLERANCE_S, OFF_TRACK_LIMIT_S, SectorLap,
                          SegmentModel, normalize_sector_starts)
from src.telemetry import Snapshot

STARTS4 = (0.0, 0.25, 0.5, 0.75)


def lap_pct(t: float, durations: tuple[float, ...]) -> tuple[int, float]:
    """把会话时间映射成 (圈号, 圈内百分比)。时长按每圈给，允许每圈不同。"""
    start = 0.0
    for index, duration in enumerate(durations):
        if t < start + duration:
            return index + 1, (t - start) / duration
        start += duration
    return len(durations) + 1, 0.0          # 刚好在最后一圈末：下一圈起步


def frame(t: float, durations: tuple[float, ...], starts=STARTS4, *,
          lap_last=None, in_pit=False, off_track=False,
          target=None, gap_ahead=None, gap_behind=None,
          car_classes=(1, 1), car_idx=0) -> Snapshot:
    """构造一帧快照。target 为可选的前/后车描述。"""
    lap, pct = lap_pct(t, durations)
    completed = lap - 1
    last = lap_last if lap_last is not None else (
        durations[lap - 2] if lap >= 2 else 0.0)
    payload = dict(
        connected=True, session_time=t, lap=lap, lap_completed=completed,
        car_idx=car_idx, car_lap_dist=[pct], car_completed_laps=[completed],
        car_last_laps=[last], car_in_pits=[in_pit],
        car_surfaces=[0 if off_track else 3], car_classes=list(car_classes),
        on_pit_road=in_pit, sector_starts=starts, lap_last=last,
        gap_ahead_idx=gap_ahead, gap_behind_idx=gap_behind)
    if target is not None:
        target_durations = target.get("durations", durations)
        t_lap, t_pct = lap_pct(t, target_durations)
        t_completed = t_lap - 1 + target.get("completed_delta", 0)
        t_last = (target_durations[t_lap - 2]
                  if t_lap >= 2 else 0.0)
        if target.get("sector_times"):
            t_pct = _pct_from_sector_times(
                t - _lap_start_for(t, target_durations),
                target["sector_times"], starts)
        payload.update(
            car_lap_dist=[pct, t_pct],
            car_in_pits=[in_pit, False],
            car_surfaces=[0 if off_track else 3, 3],
            car_classes=list(target.get("classes", (1, 1))),
            car_completed_laps=[completed, t_completed],
            car_last_laps=[last, t_last],
            gap_ahead_idx=target.get("gap_ahead", 1),
            gap_behind_idx=target.get("gap_behind"))
    return Snapshot(**payload)


def _lap_start_for(t: float, durations: tuple[float, ...]) -> float:
    start = 0.0
    for duration in durations:
        if t < start + duration:
            return start
        start += duration
    return sum(durations)


def _pct_from_sector_times(local: float, sector_times: tuple[float, ...],
                           starts=STARTS4) -> float:
    """按给定分段用时把圈内秒数映射成位置百分比（每段线性）。"""
    edges = list(starts) + [1.0]
    accumulated = 0.0
    for index, duration in enumerate(sector_times):
        if local < accumulated + duration:
            fraction = (local - accumulated) / duration if duration else 0.0
            return edges[index] + fraction * (edges[index + 1] - edges[index])
        accumulated += duration
    return 0.0


def feed(model: SegmentModel, durations=(80.0, 80.0), starts=STARTS4,
         dt: float = 0.25, **frame_kw) -> None:
    """按时间轴喂完整帧：先把采样铺到圈末前，再补一帧圈末后（触发结算）。"""
    total = sum(durations)
    t = 0.0
    while t < total - 1e-9:
        model.update(frame(t, durations, starts, **frame_kw))
        t += dt
    model.update(frame(total, durations, starts, **frame_kw))


class NormalizeTests(unittest.TestCase):
    def test_split_time_info_dict_is_sorted_and_deduped(self):
        raw = {"Sectors": [{"SectorStartPct": 0.5},
                           {"SectorStartPct": 0.0},
                           {"SectorStartPct": 0.5},
                           {"SectorStartPct": 0.25}]}
        self.assertEqual(normalize_sector_starts(raw), (0.0, 0.25, 0.5))

    def test_zero_boundary_is_added_when_missing(self):
        # 模拟数据可能只给内部边界：没有 0.0 就永远结算不出第 0 段
        self.assertEqual(normalize_sector_starts((0.25, 0.5)),
                         (0.0, 0.25, 0.5))

    def test_invalid_values_are_dropped(self):
        raw = [0.0, -0.1, 1.0, float("nan"), float("inf"), "x", None, 0.5]
        self.assertEqual(normalize_sector_starts(raw), (0.0, 0.5))

    def test_empty_or_broken_input_returns_empty(self):
        for raw in (None, [], {}, {"Sectors": []}, "bad", 42):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_sector_starts(raw), ())


class BoundaryCountTests(unittest.TestCase):
    """分段数随赛道变化，绝不写死 3 段。"""

    def _model_with(self, starts, duration=90.0):
        model = SegmentModel()
        feed(model, durations=(duration, duration), starts=starts)
        return model

    def test_three_sectors(self):
        starts = (0.0, 0.3, 0.7)
        model = self._model_with(starts)
        self.assertEqual(model.num_sectors, 3)
        laps = model.player_recent(1)
        self.assertEqual(len(laps), 1)
        self.assertEqual(len(laps[0].sectors), 3)
        self.assertAlmostEqual(sum(laps[0].sectors), 90.0, places=6)

    def test_two_sectors(self):
        model = self._model_with((0.0, 0.5))
        self.assertEqual(model.num_sectors, 2)
        self.assertEqual(len(model.player_recent(1)[0].sectors), 2)

    def test_single_sector(self):
        model = self._model_with((0.0,))
        self.assertEqual(model.num_sectors, 1)
        lap = model.player_recent(1)[0]
        self.assertAlmostEqual(lap.sectors[0], 90.0, places=6)

    def test_five_sectors(self):
        starts = (0.0, 0.2, 0.4, 0.6, 0.8)
        model = self._model_with(starts)
        self.assertEqual(model.num_sectors, 5)
        self.assertEqual(len(model.player_recent(1)[0].sectors), 5)

    def test_boundary_change_resets_tracking(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0), starts=STARTS4)
        self.assertEqual(model.num_sectors, 4)
        # 换赛道：边界从 4 段变 2 段，旧的段号全部失效，不能混用
        new_session = Snapshot(connected=True, session_time=200.0, lap=1,
                               car_idx=0, car_lap_dist=[0.1],
                               sector_starts=(0.0, 0.5),
                               session_id=("new-track", 1, 0))
        model.update(new_session)
        self.assertEqual(model.num_sectors, 2)
        self.assertEqual(model.player_recent(3), [])


class InterpolationTests(unittest.TestCase):
    """60Hz 采样下用线性插值还原跨界时刻。"""

    def test_boundary_and_lap_end_can_share_one_step(self):
        # 压缩圈（1s/圈）让「跨最后一段边界」与「跨终点线」落在同一采样间隔里：
        # 事件必须按位移排序先结算段、再结算圈，不能越界也不能丢末段。
        model = SegmentModel()
        durations = (1.0, 1.0)
        starts = (0.0, 0.25, 0.5, 0.9)
        t = 0.0
        while t < 2.0 - 1e-9:
            model.update(frame(t, durations, starts))
            t = round(t + 0.17, 6)
        model.update(frame(2.0, durations, starts))
        laps = model.player_recent(3)
        self.assertEqual([lap.lap for lap in laps], [2])
        self.assertEqual(len(laps[0].sectors), 4)
        self.assertAlmostEqual(sum(laps[0].sectors), 1.0, places=6)

    def test_two_sector_track_wraps_with_full_lap_time(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0), starts=(0.0, 0.5))
        laps = model.player_recent(1)
        self.assertEqual(len(laps), 1)
        self.assertEqual(len(laps[0].sectors), 2)
        self.assertAlmostEqual(sum(laps[0].sectors), 80.0, places=6)

    def test_short_final_segment_is_counted(self):
        # 边界贴近终点线（0.999）：末段很短，但必须算进去，否则 Σ 差一整段
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0), starts=(0.0, 0.999))
        lap = model.player_recent(1)[0]
        self.assertEqual(len(lap.sectors), 2)
        self.assertAlmostEqual(lap.sectors[0], 79.92, places=6)
        self.assertAlmostEqual(lap.sectors[1], 0.08, places=6)

    def test_duplicate_tick_does_not_interpolate(self):
        # 同一 SessionTime 的重复读取（tick 相位相同）不能当位移处理
        model = SegmentModel()
        base = dict(connected=True, lap=1, car_idx=0, car_completed_laps=[0],
                    car_last_laps=[80.0], car_in_pits=[False],
                    car_surfaces=[3], car_classes=[1],
                    sector_starts=STARTS4, lap_last=80.0)
        model.update(Snapshot(session_time=10.0, car_lap_dist=[0.24], **base))
        model.update(Snapshot(session_time=10.0, car_lap_dist=[0.30], **base))
        state = model._cars[0]
        self.assertEqual(state.last_pct, 0.24)      # 重复 tick 不采纳
        self.assertIsNone(state.sector_start_t)

    def test_small_backward_noise_is_tolerated(self):
        # 刹车/浮点噪声造成的极小负位移不能被当成跳变，否则真实比赛里
        # 每个抖动都会把整圈标成 torn。
        model = SegmentModel()
        durations = (80.0, 80.0)
        frames = []
        t = 0.0
        while t < 160.0 - 1e-9:
            frames.append(frame(t, durations))
            t += 0.25
        for snap in frames:                     # 40.25 这一帧位置微退 2e-5
            if abs(snap.session_time - 40.25) < 1e-9:
                snap.car_lap_dist = [0.5 - 0.00002]
        frames.append(frame(160.0, durations))
        for snap in frames:
            model.update(snap)
        laps = model.player_recent(3)
        self.assertIn(2, [lap.lap for lap in laps])

    def test_backward_teleport_is_discarded(self):
        model = SegmentModel()
        base = dict(connected=True, lap=1, car_idx=0, car_completed_laps=[0],
                    car_last_laps=[80.0], car_in_pits=[False],
                    car_surfaces=[3], car_classes=[1],
                    sector_starts=STARTS4, lap_last=80.0)
        model.update(Snapshot(session_time=10.0, car_lap_dist=[0.60], **base))
        model.update(Snapshot(session_time=11.0, car_lap_dist=[0.40], **base))
        state = model._cars[0]
        self.assertTrue(state.torn)
        self.assertIsNone(state.sector_start_t)

    def test_pct_one_then_zero_does_not_settle_twice(self):
        model = SegmentModel()
        starts = STARTS4
        for t, pct, lap in ((0.0, 0.0, 1), (79.0, 0.99, 1),
                            (80.0, 1.0, 2), (80.25, 0.01, 2)):
            model.update(Snapshot(
                connected=True, session_time=t, lap=lap, lap_completed=lap - 1,
                car_idx=0, car_lap_dist=[pct], car_completed_laps=[lap - 1],
                car_last_laps=[80.0], car_in_pits=[False], car_surfaces=[3],
                car_classes=[1], sector_starts=starts, lap_last=80.0))
        self.assertEqual(len(model._cars[0].laps), 1)   # 只结算一次
        self.assertAlmostEqual(model._cars[0].last_pct, 0.01, places=6)

    def test_crossing_between_samples_uses_linear_interpolation(self):
        model = SegmentModel()
        starts = (0.0, 0.25, 0.5, 0.75)
        # 人造两帧：t=10 pct=.24 → t=11 pct=.26，.25 在中间恰好 t=10.5 跨过
        # （位移速率 0.02 pct/s，属于正常 80s 圈量级，不会被跳变防护误杀）
        model.update(Snapshot(connected=True, session_time=10.0, lap=1,
                              car_idx=0, car_lap_dist=[0.24],
                              car_completed_laps=[0], car_last_laps=[80.0],
                              car_in_pits=[False], car_surfaces=[3],
                              car_classes=[1], sector_starts=starts,
                              lap_last=80.0))
        model.update(Snapshot(connected=True, session_time=11.0, lap=1,
                              car_idx=0, car_lap_dist=[0.26],
                              car_completed_laps=[0], car_last_laps=[80.0],
                              car_in_pits=[False], car_surfaces=[3],
                              car_classes=[1], sector_starts=starts,
                              lap_last=80.0))
        state = model._cars[0]
        self.assertAlmostEqual(state.sector_start_t, 10.5, places=9)

    def test_constant_speed_lap_recovers_exact_sector_times(self):
        # 匀速 90s 一圈、60Hz 采样（dt≈1/60）：跨段时间应是精确的 27/36/27
        model = SegmentModel()
        feed(model, durations=(90.0, 90.0), starts=(0.0, 0.3, 0.7),
             dt=1 / 60)
        lap = model.player_recent(1)[0]
        self.assertAlmostEqual(lap.sectors[0], 27.0, places=6)
        self.assertAlmostEqual(lap.sectors[1], 36.0, places=6)
        self.assertAlmostEqual(lap.sectors[2], 27.0, places=6)
        self.assertAlmostEqual(sum(lap.sectors), 90.0, places=6)

    def test_lap_time_matches_reported_official_time(self):
        model = SegmentModel()
        feed(model)
        lap = model.player_recent(1)[0]
        self.assertAlmostEqual(lap.lap_time, 80.0, places=6)
        self.assertAlmostEqual(sum(lap.sectors), lap.lap_time, places=6)

    def test_lap_end_wrap_settles_previous_lap(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0, 80.0), dt=0.5)
        laps = model.player_recent(3)
        self.assertEqual([lap.lap for lap in laps], [3, 2])
        for lap in laps:
            self.assertAlmostEqual(lap.lap_time, 80.0, places=6)

    def test_jump_larger_than_half_lap_is_discarded(self):
        # 传送/重连：位移 > 50% 的一次采样不能拿来插值，否则会造出巨长的假分段
        model = SegmentModel()
        base = dict(connected=True, lap=1, car_idx=0, car_completed_laps=[0],
                    car_last_laps=[80.0], car_in_pits=[False],
                    car_surfaces=[3], car_classes=[1],
                    sector_starts=STARTS4, lap_last=80.0)
        model.update(Snapshot(session_time=10.0, car_lap_dist=[0.1], **base))
        model.update(Snapshot(session_time=11.0, car_lap_dist=[0.9], **base))
        state = model._cars[0]
        self.assertIsNone(state.sector_start_t)     # 没有跨界被插值
        self.assertEqual(state.sectors, [None] * 4)

    def test_teleport_by_rate_below_half_lap_is_discarded(self):
        # 拖回维修区：位移不到半圈（.1→.3），但 1s 内跑了 20% 赛道，
        # 速率远超正常行驶；不拦住就会把一个假跨界写进分段。
        model = SegmentModel()
        base = dict(connected=True, lap=1, car_idx=0, car_completed_laps=[0],
                    car_last_laps=[80.0], car_in_pits=[False],
                    car_surfaces=[3], car_classes=[1],
                    sector_starts=STARTS4, lap_last=80.0)
        model.update(Snapshot(session_time=10.0, car_lap_dist=[0.10], **base))
        model.update(Snapshot(session_time=11.0, car_lap_dist=[0.30], **base))
        state = model._cars[0]
        self.assertTrue(state.torn)
        self.assertIsNone(state.sector_start_t)     # 跨 .25 的那次插值被丢弃
        self.assertEqual(state.sectors, [None] * 4)

    def test_normal_speed_is_not_treated_as_teleport(self):
        # 正常 80s 圈的速率约 0.0125 pct/s，远低于阈值，绝不能被误伤
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0))
        self.assertTrue(model.player_recent(1))


class LapValidationTests(unittest.TestCase):
    def test_clean_lap_is_valid_and_complete(self):
        model = SegmentModel()
        feed(model)
        lap = model.player_recent(1)[0]
        self.assertTrue(lap.valid)
        self.assertFalse(lap.in_pit)
        self.assertEqual(lap.sectors, (20.0, 20.0, 20.0, 20.0))

    def test_first_lap_started_mid_lap_is_not_used(self):
        # 程序中途启动时第一圈没有圈首：宁可不报，也不报半圈
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0))
        self.assertNotIn(1, [lap.lap for lap in model.player_recent(5)])

    def test_sigma_mismatch_invalidates_lap(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0),
             lap_last=81.0)                 # 官方圈速与 Σ 差 1s
        self.assertEqual(model.player_recent(3), [])

    def test_sigma_within_tolerance_is_valid(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0), lap_last=80.0 + 0.09)
        self.assertEqual(len(model.player_recent(1)), 1)

    def test_sigma_beyond_tolerance_is_invalid(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0),
             lap_last=80.0 + LAP_TIME_TOLERANCE_S + 0.01)
        self.assertEqual(model.player_recent(3), [])

    def test_pit_lap_is_invalid(self):
        model = SegmentModel()
        total = 240.0
        t = 0.0
        while t < total - 1e-9:
            in_pit = 85.0 <= t < 100.0     # 第二圈中途进维修区
            model.update(frame(t, (80.0, 80.0, 80.0), in_pit=in_pit))
            t += 0.25
        model.update(frame(total, (80.0, 80.0, 80.0), in_pit=False))
        self.assertEqual([lap.lap for lap in model.player_recent(5)], [3])

    def test_off_track_over_limit_invalidates_lap(self):
        model = SegmentModel()
        total = 240.0
        t = 0.0
        while t < total - 1e-9:
            off = 85.0 <= t < 85.0 + OFF_TRACK_LIMIT_S + 1.0   # 第二圈出界 4s
            model.update(frame(t, (80.0, 80.0, 80.0), off_track=off))
            t += 0.25
        model.update(frame(total, (80.0, 80.0, 80.0)))
        self.assertEqual([lap.lap for lap in model.player_recent(5)], [3])

    def test_short_off_track_keeps_lap_valid(self):
        model = SegmentModel()
        total = 240.0
        t = 0.0
        while t < total - 1e-9:
            off = 85.0 <= t < 86.0          # 1s 出界，允许
            model.update(frame(t, (80.0, 80.0, 80.0), off_track=off))
            t += 0.25
        model.update(frame(total, (80.0, 80.0, 80.0)))
        self.assertEqual([lap.lap for lap in model.player_recent(5)], [3, 2])

    def test_lap_time_outlier_is_invalid(self):
        # 第 3 圈 90s，比近 3 圈中位数 80s 慢 12.5% → 事故/黄旗圈，不参与对比
        model = SegmentModel()
        durations = (80.0, 80.0, 90.0, 80.0)
        total = sum(durations)
        t = 0.0
        while t < total - 1e-9:
            model.update(frame(t, durations))
            t += 0.25
        model.update(frame(total, durations))
        self.assertEqual([lap.lap for lap in model.player_recent(5)], [4, 2])

    def test_reported_lap_time_is_preferred_over_measured(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0))
        lap = model.player_recent(1)[0]
        self.assertEqual(lap.lap_time, 80.0)


class EmptyDataTests(unittest.TestCase):
    def test_fresh_model_returns_empty_results(self):
        model = SegmentModel()
        self.assertEqual(model.num_sectors, 0)
        self.assertEqual(model.player_recent(3), [])
        self.assertEqual(model.car_recent(1, 3), [])
        self.assertEqual(model.sector_medians(None, 3), ())
        self.assertEqual(model.compare(1, 3), ())
        self.assertIsNone(model.target_for("ahead"))
        self.assertIsNone(model.sectors_for(1))

    def test_snapshot_without_sector_starts_is_ignored(self):
        model = SegmentModel()
        for _ in range(10):
            model.update(Snapshot(connected=True, session_time=1.0, lap=1,
                                  car_idx=0, car_lap_dist=[0.1]))
        self.assertEqual(model.num_sectors, 0)
        self.assertEqual(model.player_recent(3), [])

    def test_disconnected_snapshot_keeps_finished_history(self):
        model = SegmentModel()
        feed(model)
        before = len(model.player_recent(3))
        model.update(Snapshot(connected=False))
        self.assertEqual(len(model.player_recent(3)), before)

    def test_reset_clears_everything(self):
        model = SegmentModel()
        feed(model)
        self.assertTrue(model.player_recent(3))
        model.reset()
        self.assertEqual(model.num_sectors, 0)
        self.assertEqual(model.player_recent(3), [])
        self.assertIsNone(model.target_for("ahead"))


class ComparisonTests(unittest.TestCase):
    """前后车对比：同组别、非套圈、只报观察到的段用时差。"""

    def _model(self, target):
        model = SegmentModel()
        durations = (80.0, 80.0, 80.0)
        total = sum(durations)
        t = 0.0
        while t < total - 1e-9:
            model.update(frame(t, durations, target=target))
            t += 0.25
        model.update(frame(total, durations, target=target))
        return model

    def test_player_recent_is_newest_first(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0, 80.0))
        laps = model.player_recent(3)
        self.assertEqual([lap.lap for lap in laps], [3, 2])

    def test_recent_limit_is_respected(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0, 80.0, 80.0))
        self.assertEqual(len(model.player_recent(1)), 1)
        self.assertEqual(len(model.player_recent(2)), 2)

    def test_sector_medians_uses_median_of_recent_laps(self):
        # 圈速 80/82/78 → 每段用时 20/20.5/19.5，近 3 圈中位数回到 20
        model = SegmentModel()
        durations = (80.0, 80.0, 82.0, 78.0)
        total = sum(durations)
        t = 0.0
        while t < total - 1e-9:
            model.update(frame(t, durations))
            t += 0.25
        model.update(frame(total, durations))
        medians = model.sector_medians(None, 3)
        for value in medians:
            self.assertAlmostEqual(value, 20.0, places=6)
        # 只看最近一圈就是 19.5
        self.assertAlmostEqual(model.sector_medians(None, 1)[0], 19.5, places=6)

    def test_compare_reports_target_minus_mine_per_sector(self):
        # 前车第 1/2 段各慢 1s、第 3/4 段各快 1s（总圈速相同）
        target = dict(durations=(80.0, 80.0, 80.0),
                      sector_times=(21.0, 21.0, 19.0, 19.0),
                      gap_ahead=1)
        model = self._model(target)
        deltas = model.compare(1, 3)
        self.assertEqual([round(delta, 6) for delta in deltas],
                         [1.0, 1.0, -1.0, -1.0])
        # 车手自己的中位数仍是整圈 20s
        for value in model.sector_medians(None, 3):
            self.assertAlmostEqual(value, 20.0, places=6)
        # 目标车自己的分段也能取到
        target_lap = model.car_recent(1, 1)[0]
        self.assertAlmostEqual(sum(target_lap.sectors), 80.0, places=6)

    def test_compare_without_target_returns_none_tuple(self):
        model = SegmentModel()
        feed(model)
        self.assertEqual(model.compare(None, 3), (None, None, None, None))

    def test_compare_without_target_laps_returns_none_tuple(self):
        # 目标车还没跑出有效圈：逐段给 None，绝不拿 0 当「打平」
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0))
        self.assertEqual(model.compare(9, 3), (None, None, None, None))

    def _filter_model(self, target):
        model = self._model(target)
        return model

    def test_target_for_returns_same_class_same_lap_car(self):
        model = self._filter_model(dict(durations=(80.0, 80.0, 80.0)))
        self.assertEqual(model.target_for("ahead"), 1)

    def test_target_for_rejects_other_class(self):
        model = self._filter_model(dict(durations=(80.0, 80.0, 80.0),
                                        classes=(1, 2)))
        self.assertIsNone(model.target_for("ahead"))

    def test_target_for_rejects_lapped_car(self):
        model = self._filter_model(dict(durations=(80.0, 80.0, 80.0),
                                        completed_delta=1))
        self.assertIsNone(model.target_for("ahead"))

    def test_target_for_rejects_unknown_class(self):
        model = self._filter_model(dict(durations=(80.0, 80.0, 80.0),
                                        classes=(1, -1)))
        self.assertIsNone(model.target_for("ahead"))

    def test_target_for_without_gap_index_is_none(self):
        model = SegmentModel()
        feed(model)
        self.assertIsNone(model.target_for("ahead"))
        self.assertIsNone(model.target_for("behind"))

    def test_target_for_unknown_role_is_none(self):
        model = self._filter_model(dict(durations=(80.0, 80.0, 80.0)))
        self.assertIsNone(model.target_for("sideways"))

    def test_target_for_requires_sector_data(self):
        model = SegmentModel()
        model.update(Snapshot(connected=True, session_time=1.0, lap=1,
                              car_idx=0, car_lap_dist=[0.1],
                              gap_ahead_idx=1, car_classes=[1, 1],
                              car_completed_laps=[0, 0]))
        self.assertIsNone(model.target_for("ahead"))


    def test_lap_number_jump_discards_partial_lap(self):
        # 跳圈/重连：圈号一次 > +1，说明中间丢了数据，当前半圈不可信
        model = SegmentModel()
        for t in (0.0, 20.0, 40.0):
            model.update(frame(t, (80.0, 80.0)))
        model.update(Snapshot(
            connected=True, session_time=40.5, lap=6, lap_completed=5,
            car_idx=0, car_lap_dist=[0.00625], car_completed_laps=[5],
            car_last_laps=[80.0], car_in_pits=[False], car_surfaces=[3],
            car_classes=[1], sector_starts=STARTS4, lap_last=80.0))
        state = model._cars[0]
        self.assertEqual(state.lap, 6)
        self.assertIsNone(state.lap_start_t)     # 跳圈后的半圈没有起点
        self.assertEqual(state.sectors, [None] * 4)
        self.assertEqual(model.player_recent(3), [])

    def test_sector_lap_shape(self):
        model = SegmentModel()
        feed(model)
        lap = model.player_recent(1)[0]
        self.assertIsInstance(lap, SectorLap)
        self.assertIsInstance(lap.sectors, tuple)
        self.assertIsInstance(lap.lap, int)
        self.assertIsInstance(lap.lap_time, float)
        self.assertIsInstance(lap.valid, bool)
        self.assertIsInstance(lap.in_pit, bool)


class SessionBestTests(unittest.TestCase):
    """本场最佳分段（借鉴 irdashies session best；只取有效圈）。"""

    def test_best_sector_is_min_of_valid_laps(self):
        model = SegmentModel()
        durations = (80.0, 82.0, 78.0)     # 每段 20 / 20.5 / 19.5
        total = sum(durations)
        t = 0.0
        while t < total - 1e-9:
            model.update(frame(t, durations))
            t += 0.25
        model.update(frame(total, durations))
        best = model.session_best(None)
        self.assertEqual(len(best), 4)
        for value in best:
            self.assertAlmostEqual(value, 19.5, places=6)

    def test_best_ignores_invalid_fast_lap(self):
        # 事故/出界圈常常「更快」：不能让它成为标杆
        model = SegmentModel()
        durations = (80.0, 80.0, 70.0)
        total = sum(durations)
        t = 0.0
        while t < total - 1e-9:
            model.update(frame(t, durations))
            t += 0.25
        model.update(frame(total, durations))
        # 70s 圈偏离中位数 80s 超过 8% → 无效，最佳仍是 20s
        best = model.session_best(None)
        for value in best:
            self.assertAlmostEqual(value, 20.0, places=6)

    def test_best_is_empty_before_any_valid_lap(self):
        model = SegmentModel()
        self.assertEqual(model.session_best(None), ())
        model.update(frame(0.0, (80.0, 80.0)))
        self.assertEqual(model.session_best(None), (None, None, None, None))

    def test_reset_clears_session_best(self):
        model = SegmentModel()
        feed(model)
        self.assertTrue(any(value is not None
                            for value in model.session_best(None)))
        model.reset()
        self.assertEqual(model.session_best(None), ())


class SectorLapShapeTests(unittest.TestCase):
    def test_sector_lap_is_immutable(self):
        lap = SectorLap(lap=2, sectors=(20.0, 20.0), lap_time=40.0,
                        valid=True, in_pit=False)
        with self.assertRaises(Exception):
            lap.lap = 3                     # frozen dataclass：读方拿到的就是快照

    def test_sectors_for_returns_none_for_invalid_lap(self):
        model = SegmentModel()
        feed(model, durations=(80.0, 80.0), lap_last=100.0)
        self.assertIsNone(model.sectors_for(2))


if __name__ == "__main__":
    unittest.main()
