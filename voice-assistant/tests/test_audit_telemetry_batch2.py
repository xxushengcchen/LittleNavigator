"""T02/T04 regression: class leader stays in class; partial results never shrink field size."""
from __future__ import annotations

import unittest

from src import telemetry
from src.telemetry import IrsdkTelemetry, Snapshot, _NOT_SET
from src.segment_rules import is_full_split
from src.telemetry_answers import _class_leader_index, answer


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


class ClassLeaderTests(unittest.TestCase):
    def test_class_leader_is_same_class(self):
        # 审计 T02 原始数据：class=[10,20,20]、class_position=[1,1,2]，玩家 idx2。
        # 旧代码取第一个组别第一（idx0，别的组），正确应是同组 idx1。
        snap = Snapshot(connected=True, car_idx=2,
                        car_classes=[10, 20, 20],
                        car_class_positions=[1, 1, 2])
        self.assertEqual(_class_leader_index(snap), 1)

    def test_no_class_data_keeps_previous_behaviour(self):
        snap = Snapshot(connected=True, car_idx=2,
                        car_class_positions=[1, 2, 3])
        self.assertEqual(_class_leader_index(snap), 0)

    def test_pace_car_and_self_excluded(self):
        snap = Snapshot(connected=True, car_idx=0,
                        car_classes=[20, 20, 20],
                        car_class_positions=[1, 1, 2],
                        pace_car_indices=[1])
        self.assertEqual(_class_leader_index(snap), None)

    def test_unavailable_when_no_class_positions(self):
        snap = Snapshot(connected=True, car_idx=0)
        self.assertIsNone(_class_leader_index(snap))


class FieldSizeTests(unittest.TestCase):
    def test_partial_results_do_not_shrink_field_size(self):
        tel, fake = make_irsdk({
            "SessionNum": 0,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [{"CarIdx": 2, "Position": 1}],
            }]},
        })
        snap = Snapshot(connected=True, car_idx=0,
                        car_lap_dist=[0.1, 0.2, 0.3],
                        car_positions=[1, 2, 3],
                        car_class_positions=[None] * 3,
                        car_last_laps=[None] * 3,
                        car_best_laps=[None] * 3,
                        car_completed_laps=[None] * 3,
                        field_size=3)
        tel._apply_session_results(snap, fake)
        self.assertEqual(snap.field_size, 3, "局部结果列表不能把参赛车数缩小")

    def test_complete_results_can_grow_field_size(self):
        tel, fake = make_irsdk({
            "SessionNum": 0,
            "SessionInfo": {"Sessions": [{
                "SessionNum": 0,
                "ResultsPositions": [
                    {"CarIdx": 1, "Position": 1},
                    {"CarIdx": 2, "Position": 2},
                    {"CarIdx": 3, "Position": 3},
                ],
            }]},
        })
        snap = Snapshot(connected=True, car_idx=0,
                        car_lap_dist=[0.1, 0.2, 0.3, 0.4],
                        car_positions=[0, 0, 0, 0],
                        car_class_positions=[None] * 4,
                        car_last_laps=[None] * 4,
                        car_best_laps=[None] * 4,
                        car_completed_laps=[None] * 4,
                        field_size=1)
        tel._apply_session_results(snap, fake)
        self.assertEqual(snap.field_size, 3)


if __name__ == "__main__":
    unittest.main()


class FullSplitBoundaryTests(unittest.TestCase):
    """T10：只有整份清单请求才硬拒答；指定单段/比较问题放行。"""

    def test_whole_list_still_refused(self):
        for q in ('全部分段', '所有赛段用时', '每一段用了多少', '各段成绩',
                  '完整分段', '分段列表', '我上一圈分段用时', '这圈分段成绩'):
            with self.subTest(q=q):
                self.assertTrue(is_full_split(q), q)

    def test_named_segment_or_comparison_allowed(self):
        for q in ('第二段分段用时是多少', '第3段丢了多少时间',
                  '前车上一圈分段差多少', '我跟前车分段差多少'):
            with self.subTest(q=q):
                self.assertFalse(is_full_split(q), q)


if __name__ == "__main__":
    unittest.main()
