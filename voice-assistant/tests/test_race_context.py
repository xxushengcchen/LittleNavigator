import unittest
from src.race_context import build, estimate_tokens, trim, ALLOWED_FIELDS
from src.query_class import QueryKind
from src.telemetry import Snapshot


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


if __name__ == '__main__':
    unittest.main()
