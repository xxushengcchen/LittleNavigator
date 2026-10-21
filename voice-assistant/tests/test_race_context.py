import unittest
from src.race_context import build, estimate_tokens, trim, ALLOWED_FIELDS
from src.query_class import QueryKind
from src.telemetry import Snapshot
from tests.test_router_sector import FakeSegments, default_model


def snap(**kw):
    base = dict(connected=True, source="test", lap=12, lap_completed=11,
                lap_last=83.512, lap_best=83.104, laps_remain=5,
                fuel_level=18.4, fuel_per_lap=1.42, laps_of_fuel=13.0,
                position=4, field_size=18,
                gap_ahead_s=1.8, gap_behind_s=1.2,
                air_temp=28.0, track_temp=41.0, track_wetness=1)
    base.update(kw)
    return Snapshot(**base)


class BuildTests(unittest.TestCase):
    def test_minimal_question_gets_now_and_derived_only(self):
        ctx = build(snap(), [], QueryKind.KNOWN, "油还够吗")
        self.assertIn("now", ctx)
        self.assertIn("derived", ctx)
        self.assertNotIn("recent_laps", ctx)

    def test_analysis_includes_recent_laps(self):
        laps = [{"lap": i, "lap_time": 83.0 + i * 0.1} for i in range(1, 8)]
        ctx = build(snap(), laps, QueryKind.ANALYSIS, "为什么慢")
        self.assertIn("recent_laps", ctx)
        self.assertLessEqual(len(ctx["recent_laps"]), 5)

    def test_derived_values_are_prerounded(self):
        ctx = build(snap(), [{"lap": 11, "lap_time": 83.2}], QueryKind.KNOWN, "油")
        self.assertEqual(round(ctx["derived"]["delta_to_best"], 1), 0.4)

    def test_opponents_layer_is_not_included_by_default(self):
        ctx = build(snap(), [], QueryKind.KNOWN, "油还够吗")
        self.assertNotIn("opponents", ctx)

    def test_opponents_layer_included_when_asked_about_gap(self):
        ctx = build(snap(car_positions=[1, 4, 5], gap_ahead_idx=1,
                         car_last_laps=[82.1, 83.0, 84.0],
                         car_best_laps=[81.9, 82.8, 83.9],
                         car_completed_laps=[13, 12, 12]),
                    [], QueryKind.ANALYSIS, "前车多远")
        self.assertIn("opponents", ctx)
        self.assertIn("ahead", ctx["opponents"])

    def test_laps_spare_is_computed(self):
        ctx = build(snap(fuel_level=18.4, laps_of_fuel=13.0, laps_remain=5),
                    [], QueryKind.KNOWN, "油")
        self.assertEqual(ctx["derived"]["laps_spare"], 8.0)

    def test_allowed_fields_covers_derived_keys(self):
        ctx = build(snap(), [{"lap": 11, "lap_time": 83.2}], QueryKind.ANALYSIS, "为什么慢")
        for key in ctx["derived"]:
            self.assertIn(key, ALLOWED_FIELDS, f"derived.{key} 不在白名单")


class SpokenFieldTests(unittest.TestCase):
    """v1.5.2：时长字段成对给出「口语化」版本，模型照抄即可，不必自己换算。

    数值字段保留不变（数字闸仍按原字段核对）；`_spoken` 只是播报用的字符串。
    """

    def test_lap_times_have_spoken_pairs(self):
        ctx = build(snap(), [], QueryKind.KNOWN, "上一圈多少")
        self.assertEqual(ctx["now"]["lap_last"], 83.512)
        self.assertEqual(ctx["now"]["lap_last_spoken"], "1分23.5秒")
        self.assertEqual(ctx["now"]["lap_best_spoken"], "1分23.1秒")

    def test_time_remain_spoken_uses_whole_seconds(self):
        ctx = build(snap(time_remain_s=750.4), [], QueryKind.KNOWN, "还剩多久")
        self.assertEqual(ctx["now"]["time_remain_s"], 750.0)
        self.assertEqual(ctx["now"]["time_remain_spoken"], "12分30秒")

    def test_avg3_and_avg5_have_spoken_pairs(self):
        laps = [{"lap": i, "lap_time": 83.0 + i * 0.1} for i in range(1, 6)]
        # 最近 3 圈：83.3 / 83.4 / 83.5 → 均值 83.4
        ctx = build(snap(), laps, QueryKind.ANALYSIS, "圈速稳定吗")
        self.assertEqual(ctx["derived"]["avg3"], 83.4)
        self.assertEqual(ctx["derived"]["avg3_spoken"], "1分23.4秒")
        self.assertEqual(ctx["derived"]["avg5"], 83.3)
        self.assertEqual(ctx["derived"]["avg5_spoken"], "1分23.3秒")

    def test_spoken_fields_omitted_when_value_missing(self):
        ctx = build(snap(lap_last=None, lap_best=None, time_remain_s=None),
                    [], QueryKind.KNOWN, "油")
        for key in ("lap_last_spoken", "lap_best_spoken", "time_remain_spoken"):
            self.assertNotIn(key, ctx["now"], f"{key} 不该在缺值出现")
        self.assertNotIn("avg3_spoken", ctx["derived"])

    def test_spoken_fields_are_whitelisted(self):
        ctx = build(snap(time_remain_s=750), [], QueryKind.KNOWN, "还剩多久")
        self.assertIn("lap_last_spoken", ctx["now"])
        for key in ("lap_last_spoken", "lap_best_spoken", "time_remain_spoken",
                    "avg3_spoken", "avg5_spoken"):
            self.assertIn(key, ALLOWED_FIELDS, f"{key} 不在白名单，模型引用会被拒")

    def test_spoken_value_passes_number_gate(self):
        from src.answer_checks import check_numbers

        ctx = build(snap(), [], QueryKind.KNOWN, "上一圈多少")
        spoken = ctx["now"]["lap_last_spoken"]
        self.assertIsNone(check_numbers(f"上一圈 {spoken}",
                                        ctx, "上一圈多少"))
        # 同一路径下编造的数字仍然被拦
        self.assertIsNotNone(check_numbers("上一圈 1分30.0秒",
                                           ctx, "上一圈多少"))


class TrimTests(unittest.TestCase):
    def test_trim_drops_far_laps_first(self):
        laps = [{"lap": i, "lap_time": 83.0} for i in range(1, 20)]
        ctx = build(snap(), laps, QueryKind.ANALYSIS, "为什么慢")
        tight = trim(ctx, 100)
        self.assertLessEqual(len(tight.get("recent_laps", [])),
                             len(ctx["recent_laps"]))

    def test_estimate_is_cheap_and_monotonic(self):
        small = {"a": "x"}
        big = {"a": "x" * 400}
        self.assertLess(estimate_tokens(small), estimate_tokens(big))

    def test_trim_under_budget_returns_equivalent(self):
        ctx = build(snap(), [], QueryKind.KNOWN, "油")
        self.assertEqual(trim(ctx, 100000), ctx)

    def test_trim_eventually_drops_opponents(self):
        ctx = build(snap(car_positions=[1, 4, 5], gap_ahead_idx=1,
                         car_last_laps=[82.1, 83.0, 84.0]),
                    [], QueryKind.ANALYSIS, "前车多远")
        self.assertIn("opponents", ctx)
        tight = trim(ctx, 1)
        self.assertNotIn("opponents", tight)


class MissingDataTests(unittest.TestCase):
    def test_disconnected_snapshot_does_not_raise(self):
        ctx = build(Snapshot(connected=False), [], QueryKind.ANALYSIS, "为什么慢")
        self.assertFalse(ctx["now"]["connected"])

    def test_none_fields_are_omitted_not_nulled(self):
        ctx = build(snap(fuel_level=None, lap_last=None), [], QueryKind.ANALYSIS, "油")
        self.assertNotIn("fuel_level", ctx["now"])
        self.assertNotIn("lap_last", ctx["now"])

    def test_nan_is_treated_as_missing(self):
        ctx = build(snap(fuel_level=float("nan")), [], QueryKind.KNOWN, "油")
        self.assertNotIn("fuel_level", ctx["now"])

    def test_empty_laps_list_is_safe(self):
        ctx = build(snap(), [], QueryKind.ANALYSIS, "为什么慢")
        self.assertNotIn("recent_laps", ctx)


class SegmentLayerTests(unittest.TestCase):
    """v1.5.4：segments 层只在问到分段时附加，数值全部预计算好。"""

    def _build(self, question, model=None, s=None):
        return build(s or snap(), [], QueryKind.ANALYSIS, question,
                     segments=model)

    def test_not_included_by_default(self):
        ctx = self._build("油还够吗", default_model())
        self.assertNotIn("segments", ctx)

    def test_included_for_sector_questions(self):
        for question in ("哪一段慢", "哪里丢时间", "上一圈分段",
                         "跟后车分段差多少", "各段用时多少"):
            with self.subTest(q=question):
                ctx = self._build(question, default_model())
                self.assertIn("segments", ctx)

    def test_layer_shape(self):
        ctx = self._build("哪一段慢", default_model())
        seg = ctx["segments"]
        self.assertEqual(seg["count"], 4)
        self.assertLessEqual(len(seg["player"]), 3)
        self.assertEqual(seg["target"]["role"], "ahead")
        self.assertEqual(seg["target"]["car_idx"], 2)
        self.assertEqual(len(seg["target"]["deltas"]), 4)
        self.assertEqual(len(seg["target"]["deltas_spoken"]), 4)
        self.assertLessEqual(len(seg["best_loss"]), 2)

    def test_player_entries_carry_lap_and_spoken_time(self):
        ctx = self._build("哪一段慢", default_model())
        latest = ctx["segments"]["player"][-1]
        self.assertEqual(latest["lap"], 12)
        self.assertEqual(latest["lap_time"], 102.5)
        self.assertEqual(latest["lap_time_spoken"], "1分42.5秒")
        self.assertEqual(latest["sectors"], [25.4, 24.9, 30.0, 22.2])

    def test_deltas_are_signed_and_spoken(self):
        ctx = self._build("哪一段慢", default_model())
        target = ctx["segments"]["target"]
        self.assertEqual(target["deltas"], [-0.4, -0.2, 0.4, -0.3])
        self.assertEqual(target["deltas_spoken"],
                         ["慢0.40秒", "慢0.20秒", "快0.40秒", "慢0.30秒"])

    def test_best_loss_is_sorted_desc_and_capped(self):
        ctx = self._build("哪一段慢", default_model())
        best = ctx["segments"]["best_loss"]
        self.assertEqual([row["sector"] for row in best], [1, 4])
        self.assertEqual([row["delta_s"] for row in best], [0.4, 0.3])
        self.assertEqual([row["delta_spoken"] for row in best],
                         ["0.40秒", "0.30秒"])

    def test_behind_question_selects_behind_target(self):
        ctx = self._build("跟后车分段差多少", default_model())
        self.assertEqual(ctx["segments"]["target"]["role"], "behind")
        self.assertEqual(ctx["segments"]["target"]["car_idx"], 4)

    def test_no_model_means_no_layer(self):
        ctx = self._build("哪一段慢", None)
        self.assertNotIn("segments", ctx)

    def test_model_without_player_laps_means_no_layer(self):
        ctx = self._build("哪一段慢", FakeSegments(player=[]))
        self.assertNotIn("segments", ctx)

    def test_snapshot_attribute_is_used(self):
        """引擎把 DerivedTracker.segments 挂在快照上；AI 上下文不需改签名。"""
        s = snap()
        s.segments = default_model()
        ctx = build(s, [], QueryKind.ANALYSIS, "哪一段慢")
        self.assertIn("segments", ctx)

    def test_allowed_fields_covers_layer_keys(self):
        ctx = self._build("哪一段慢", default_model())
        seg = ctx["segments"]

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from walk(value)
            elif isinstance(node, (list, tuple)):
                for value in node:
                    yield from walk(value)

        for key in walk(seg):
            self.assertIn(key, ALLOWED_FIELDS, f"segments.{key} 不在白名单")

    def test_trim_drops_segments_under_tight_budget(self):
        ctx = self._build("哪一段慢", default_model())
        tight = trim(ctx, 1)
        self.assertNotIn("segments", tight)

    def test_number_gate_accepts_model_answer(self):
        from src.answer_checks import check_numbers

        ctx = self._build("哪一段慢", default_model())
        self.assertIsNone(check_numbers("第 1 段比前车慢 0.40 秒", ctx, "哪一段慢"))
        self.assertIsNotNone(check_numbers("第 1 段比前车慢 0.99 秒", ctx, "哪一段慢"))

    def test_data_gate_accepts_segments_paths(self):
        from src.answer_checks import check_data

        ctx = self._build("哪一段慢", default_model())
        for name in ("segments", "segments.target", "segments.target.deltas",
                     "segments.player", "segments.best_loss"):
            with self.subTest(name=name):
                self.assertIsNone(check_data([name], ctx), name)
        self.assertIsNotNone(check_data(["segments.target.tyre"], ctx))


class RealSegmentModelContextTests(unittest.TestCase):
    """联调：race_context 对 T1 真模型的取数路径。"""

    def test_build_with_real_model(self):
        from src.segments import SegmentModel
        from tests.test_segments import feed

        model = SegmentModel()
        feed(model, durations=(80.0, 80.0, 80.0))
        ctx = build(snap(), [], QueryKind.ANALYSIS, "哪一段慢",
                    segments=model)
        self.assertIn("segments", ctx)
        seg = ctx["segments"]
        self.assertEqual(seg["count"], 4)
        self.assertGreaterEqual(len(seg["player"]), 1)
        latest = seg["player"][-1]
        self.assertIn("lap", latest)
        self.assertIn("lap_time", latest)
        self.assertIn("lap_time_spoken", latest)
        self.assertEqual(len(latest["sectors"]), 4)
        # 真模型没有目标车：不硬塞 target
        self.assertNotIn("target", seg)


if __name__ == '__main__':
    unittest.main()
