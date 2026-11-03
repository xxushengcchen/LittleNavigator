"""燃油口径：每小时燃油是质量流量，不能当升/小时播报（审计 T01）。

同时守住「每圈消耗升数 / 剩余升数 / 续航」这些已经可靠的回答。
"""
from __future__ import annotations

import unittest

from src import telemetry
from src.telemetry_answers import answer


def _snap(**kwargs):
    base = dict(connected=True, fuel_level=18.4, fuel_pct=0.42,
                fuel_use_per_hour=60.0, fuel_per_lap=2.0,
                lap_last=83.0, laps_of_fuel=9.2, laps_remain=8)
    base.update(kwargs)
    return telemetry.Snapshot(**base)


class FuelUnitTests(unittest.TestCase):
    def test_mass_flow_is_not_spoken_as_litres_per_hour(self):
        for q in ("现在实际油耗多少", "即时油耗多少", "实时油耗是多少"):
            decision = answer("query_fuel", _snap(), q)
            plan = list(getattr(decision, "plan", []) or [])
            self.assertNotIn("AST2_UNIT_LITRE_PER_HOUR", plan, q)
            self.assertNotIn("AST2_FUEL_USE_HOUR_LEAD", plan, q)
            # 也不能出现「升/圈」口径的差值拼装
            self.assertNotIn("AST2_FUEL_VS_AVG_MORE_LEAD", plan, q)
            self.assertNotIn("AST2_FUEL_VS_AVG_LESS_LEAD", plan, q)

    def test_per_lap_fuel_still_works(self):
        decision = answer("query_fuel", _snap(), "每圈油耗多少")
        self.assertTrue(decision.ok, decision)
        # 数值口径：plan 里应出现数字与「升」单位，而不是质量流量。
        self.assertIn("AST2_UNIT_LITRE", decision.plan)
        self.assertNotIn("AST2_UNIT_LITRE_PER_HOUR", decision.plan)

    def test_remaining_and_time_still_work(self):
        self.assertTrue(answer("query_fuel", _snap(), "还有多少油").ok)
        self.assertTrue(answer("query_fuel", _snap(), "油够跑几圈").ok)
        self.assertTrue(answer("query_fuel", _snap(), "油还够跑多久").ok)


if __name__ == "__main__":
    unittest.main()
