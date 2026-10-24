"""v1.5.4 赛段对比：本地回答（router / telemetry_answers）行为。

依赖 T1 的 SegmentModel（src/segments.py）；本文件用按契约实现的最小替身
`FakeSegments`，T1 落地后同样的用例可以换成真模型做联调。
只验证「本地规则怎么答」：段数随赛道、最多 2 个损失段、无数据/无对比车怎么拒。
"""
import unittest
from dataclasses import dataclass

from src import router
from src.telemetry import Snapshot

# Lead/T3 定稿的赛段 atom：plan 里只允许出现这些 msgId（外加数字/秒）。
SECTOR_ATOMS = {
    "SPCC_FASTESTCARPOS", "SPCC_POINT", "SPCC_SECOND",
    "AST_LAST_LAP_LEAD", "AST_SECTOR_TAIL", "AST_SECTOR_WORD",
    "AST_SECTOR_TIME", "AST_SECTOR_SLOWER_AHEAD", "AST_SECTOR_SLOWER_BEHIND",
    "AST_SECTOR_FASTER_AHEAD", "AST_SECTOR_FASTER_BEHIND",
    "AST_SECTOR_BEST", "AST_SECTOR_NO_DATA", "AST_REFUSE_LATER",
}


@dataclass
class FakeLap:
    """SectorLap 的最小替身（契约字段一致）。"""

    lap: int
    sectors: tuple
    lap_time: float | None = None
    valid: bool = True
    in_pit: bool = False


class FakeSegments:
    """SegmentModel 的最小替身：实现 T2 依赖的全部 API。

    `player_recent/car_recent` 故意按「最近在前」返回，验证适配层会按圈号
    重新排序——T1 的返回顺序契约没写死，不能假设。
    """

    def __init__(self, num_sectors=4, player=None, ahead=None, behind=None,
                 targets=None):
        self.num_sectors = num_sectors
        self.player = list(player or [])
        self.ahead = list(ahead or [])
        self.behind = list(behind or [])
        self._targets = targets
        self._car_idx = {"ahead": 2, "behind": 4}

    # ---- T1 也会调用的写侧 API -------------------------------------
    def update(self, snapshot):
        """替身不参与采样；留着空实现，避免派生追踪线程报错。"""
        return None

    def reset(self):
        return None

    def sectors_for(self, lap):
        """DerivedTracker 结算圈记录时会问某一圈的分段；替身返回「没有」。"""
        return None

    # ---- T1 API ---------------------------------------------------
    def player_recent(self, n=3):
        return list(reversed(self.player[-n:]))

    def car_recent(self, car_idx, n=3):
        for role, laps in (("ahead", self.ahead), ("behind", self.behind)):
            if self._car_idx[role] == car_idx:
                return list(reversed(laps[-n:]))
        return []

    def _medians(self, laps):
        out = []
        for i in range(self.num_sectors):
            values = sorted(lap.sectors[i] for lap in laps
                            if i < len(lap.sectors) and lap.sectors[i] is not None)
            out.append(round(values[len(values) // 2], 2) if values else None)
        return tuple(out)

    def sector_medians(self, car_idx, n=3):
        if car_idx is None:
            return self._medians(self.player[-n:])
        return self._medians(self.car_recent(car_idx, n))

    def compare(self, target_idx, n=3):
        mine = self.sector_medians(None, n)
        other = self.sector_medians(target_idx, n)
        return tuple(None if a is None or b is None else round(b - a, 2)
                     for a, b in zip(mine, other))

    def target_for(self, role):
        if self._targets is not None:
            return self._targets.get(role)
        return self._car_idx.get(role)


def snapshot():
    return Snapshot(connected=True, lap=12, car_idx=7,
                    car_positions=[1, 2, 3, 4], gap_ahead_idx=2,
                    gap_behind_idx=4)


def default_model(**kw):
    """两圈自己的分段 + 前车（每段都快 0.1~0.4s）+ 后车（每段都更慢）。"""
    player = [
        FakeLap(11, (25.2, 24.7, 29.9, 21.9), 101.8),
        FakeLap(12, (25.4, 24.9, 30.0, 22.2), 102.5),
    ]
    ahead = [FakeLap(12, (25.0, 24.7, 30.4, 21.9), 102.0)]
    behind = [FakeLap(11, (26.0, 25.4, 30.9, 22.6), 104.9)]
    args = {"player": player, "ahead": ahead, "behind": behind}
    args.update(kw)
    return FakeSegments(**args)


def route(q, model=None, s=None):
    return router.route("query_sector_loss", s or snapshot(), q,
                        segments=model)


class LocalLossAnswerTests(unittest.TestCase):
    def test_reports_top_two_loss_sectors_against_ahead(self):
        decision = route("哪一段慢", default_model())
        self.assertTrue(decision.ok)
        self.assertIn("AST_SECTOR_SLOWER_AHEAD", decision.plan)
        self.assertNotIn("AST_SECTOR_SLOWER_BEHIND", decision.plan)
        # 最多两个损失段：每段以「第」开头一次，所以「第」出现 2 次。
        self.assertEqual(decision.plan.count("SPCC_FASTESTCARPOS"), 2)
        self.assertIn("第1段", decision.reason)
        self.assertIn("第4段", decision.reason)

    def test_loss_plan_is_exactly_expected_atoms(self):
        decision = route("哪一段慢", default_model())
        # median：25.4/24.9/30.0/22.2；ahead：25.0/24.7/30.4/21.9
        # 损失：段1 0.40、段2 0.20、段4 0.30 → 前二：段1 0.40、段4 0.30
        self.assertEqual(decision.plan, [
            "SPCC_FASTESTCARPOS", "SPCC_N1", "AST_SECTOR_TAIL",
            "AST_SECTOR_SLOWER_AHEAD", "SPCC_N0", "SPCC_POINT", "SPCC_N4",
            "SPCC_SECOND",
            "SPCC_FASTESTCARPOS", "SPCC_N4", "AST_SECTOR_TAIL",
            "AST_SECTOR_SLOWER_AHEAD", "SPCC_N0", "SPCC_POINT", "SPCC_N3",
            "SPCC_SECOND",
        ])

    def test_question_naming_behind_uses_behind_atom(self):
        decision = route("跟后车分段差多少", default_model())
        self.assertTrue(decision.ok)
        self.assertIn("AST_SECTOR_FASTER_BEHIND", decision.plan)
        self.assertNotIn("AST_SECTOR_FASTER_AHEAD", decision.plan)

    def test_default_target_is_ahead_when_question_is_ambiguous(self):
        decision = route("哪里丢时间", default_model())
        self.assertIn("AST_SECTOR_SLOWER_AHEAD", decision.plan)

    def test_named_target_missing_refuses_instead_of_using_best_lap(self):
        model = default_model(targets={"ahead": None, "behind": None})
        decision = route("跟前车分段差多少", model)
        self.assertFalse(decision.ok)
        self.assertEqual(decision.plan, [])
        self.assertIn("识别不到", decision.reason)

    def test_ambiguous_front_and_back_refuses(self):
        decision = route("和前后车分段差距", default_model())
        self.assertFalse(decision.ok)
        self.assertIn("分别", decision.reason)

    def test_no_targets_and_no_own_question_falls_back_to_best_lap(self):
        model = default_model(targets={"ahead": None, "behind": None})
        decision = route("哪一段慢", model)
        self.assertTrue(decision.ok)
        self.assertIn("AST_SECTOR_BEST", decision.plan)
        self.assertIn("比最佳圈慢", decision.reason)

    def test_target_without_comparable_sectors_refuses(self):
        class MediansOnly(FakeSegments):
            def compare(self, target_idx, n=3):
                return tuple([None] * self.num_sectors)

        model = MediansOnly(player=[FakeLap(12, (25.4, 24.9, 30.0, 22.2), 102.5)],
                            ahead=[FakeLap(12, (25.0, 24.7, 30.4, 21.9), 102.0)])
        decision = route("跟前车分段差多少", model)
        self.assertFalse(decision.ok)
        self.assertIn("不能对比", decision.reason)

    def test_flat_deltas_do_not_invent_loss_sectors(self):
        model = FakeSegments(
            player=[FakeLap(12, (25.0, 25.0, 25.0, 25.0), 100.0)],
            ahead=[FakeLap(12, (25.0, 25.0, 25.0, 25.0), 100.0)],
            behind=[])
        decision = route("哪一段慢", model)
        # 「哪段慢」不是「报全部段落」；持平就明确说没有慢段。
        self.assertFalse(decision.ok)
        self.assertNotIn("AST_LAST_LAP_LEAD", decision.plan)
        self.assertNotIn("AST_SECTOR_SLOWER_AHEAD", decision.plan)

    def test_corner_question_refuses_instead_of_listing_sectors(self):
        decision = route("我上一圈哪个弯慢了", default_model())
        self.assertFalse(decision.ok)
        self.assertEqual(decision.plan, [])
        self.assertIn("弯", decision.reason)

    def test_asr_duan_variant_is_loss_not_own_list(self):
        model = FakeSegments(
            player=[FakeLap(12, (25.0, 25.0, 25.0, 25.0), 100.0)],
            ahead=[], behind=[])
        decision = route("我上一圈哪一断慢了", model)
        self.assertFalse(decision.ok)
        self.assertNotIn("AST_LAST_LAP_LEAD", decision.plan)

    def test_loss_phrase_canonicalized_as_own_sectors_still_uses_loss_path(self):
        decision = route("塞段损失多少", default_model())
        self.assertNotEqual(decision.plan[:2], ["AST_LAST_LAP_LEAD", "AST_SECTOR_WORD"])

    def test_one_lap_loss_question_refuses_instead_of_listing_all(self):
        model = FakeSegments(
            player=[FakeLap(12, (25.0, 25.0, 25.0, 25.0), 100.0)],
            ahead=[], behind=[])
        decision = route("哪一段慢", model)
        self.assertFalse(decision.ok)
        self.assertEqual(decision.plan, [])
        self.assertNotIn("AST_LAST_LAP_LEAD", decision.plan)


class LocalOwnSectorAnswerTests(unittest.TestCase):
    def test_full_split_request_uses_drive_focus(self):
        decision = route("上一圈分段", default_model())
        self.assertTrue(decision.ok)
        self.assertEqual(decision.plan, ["AST_REFUSE_LATER"])
        self.assertIn("专心", decision.reason)

    def test_specific_sector_question_reports_only_that_sector(self):
        decision = route("第二段用了多少", default_model())
        self.assertEqual(decision.plan,
                         ["AST_LAST_LAP_LEAD",
                          "SPCC_FASTESTCARPOS", "SPCC_N2", "AST_SECTOR_TAIL",
                          "AST_SECTOR_TIME", "SPCC_N24", "SPCC_POINT",
                          "SPCC_N9", "SPCC_SECOND"])

    def test_specific_sector_question_accepts_arabic_number(self):
        decision = route("第3段跑多久", default_model())
        self.assertEqual(decision.plan,
                         ["AST_LAST_LAP_LEAD",
                          "SPCC_FASTESTCARPOS", "SPCC_N3", "AST_SECTOR_TAIL",
                          "AST_SECTOR_TIME", "SPCC_N30", "SPCC_POINT",
                          "SPCC_N0", "SPCC_SECOND"])

    def test_full_split_refuses_even_with_valid_laps(self):
        player = [
            FakeLap(10, (10.0, 10.0, 10.0, 10.0), 40.0, valid=False),
            FakeLap(11, (25.2, 24.7, 29.9, 21.9), 101.8),
            FakeLap(12, (99.0, 99.0, 99.0, 99.0), 396.0, in_pit=True),
        ]
        decision = route("上一圈分段", FakeSegments(player=player))
        self.assertTrue(decision.ok)
        self.assertEqual(decision.plan, ["AST_REFUSE_LATER"])
        self.assertNotIn("SPCC_N25", decision.plan)


class NoDataTests(unittest.TestCase):
    def test_missing_model_explicitly_refuses(self):
        decision = route("哪一段慢", None)
        self.assertEqual(decision.plan, ["AST_SECTOR_NO_DATA"])
        self.assertIn("分段", decision.reason)

    def test_model_without_player_laps_refuses(self):
        decision = route("哪一段慢", FakeSegments(player=[]))
        self.assertEqual(decision.plan, ["AST_SECTOR_NO_DATA"])

    def test_model_with_one_sector_is_unusable(self):
        model = FakeSegments(num_sectors=1,
                             player=[FakeLap(12, (25.0,), 25.0)])
        decision = route("哪一段慢", model)
        self.assertEqual(decision.plan, ["AST_SECTOR_NO_DATA"])

    def test_old_call_signature_still_works(self):
        # 旧调用方不带 segments：得到明确拒答，不抛异常。
        decision = router.route("query_sector_loss", snapshot(), "哪一段慢")
        self.assertEqual(decision.plan, ["AST_SECTOR_NO_DATA"])


class AnswerShapeTests(unittest.TestCase):
    def test_plan_uses_only_known_atoms(self):
        for q in ("哪一段慢", "跟后车分段差多少", "上一圈分段", "第二段用了多少"):
            with self.subTest(q=q):
                decision = route(q, default_model())
                for msg_id in decision.plan:
                    self.assertIn(msg_id, SECTOR_ATOMS | {
                        f"SPCC_N{n}" for n in range(100)}, msg_id)

    def test_reason_does_not_attribute_or_predict(self):
        decision = route("哪一段慢", default_model())
        for word in ("刹车", "走线", "轮胎", "胎", "预测", "下一圈", "因为"):
            self.assertNotIn(word, decision.reason)

    def test_other_intents_never_use_sector_atoms(self):
        # 意图不是 query_sector_loss 时，赛段分支不能抢答别人的问题。
        for intent, q in (("query_laptime", "哪一段慢"),
                          ("query_fuel", "哪一段慢"),
                          ("query_gap", "跟后车分段差多少")):
            with self.subTest(intent=intent):
                decision = router.route(intent, snapshot(), q,
                                        segments=default_model())
                for msg_id in decision.plan:
                    self.assertNotIn(msg_id, SECTOR_ATOMS - {
                        "SPCC_FASTESTCARPOS", "SPCC_SECOND", "SPCC_POINT"}, msg_id)


class RealSegmentModelTests(unittest.TestCase):
    """联调：直接用 T1 已落地的 src/segments.py 真模型跑本地回答。

    替身测试锁行为，这组测试锁「适配层与 T1 的 API 真的对得上」。
    """

    def _model(self, target=None):
        from src.segments import SegmentModel
        from tests.test_segments import frame

        model = SegmentModel()
        durations = (80.0, 80.0, 80.0)
        total = sum(durations)
        t = 0.0
        while t < total - 1e-9:
            model.update(frame(t, durations, target=target))
            t += 0.25
        model.update(frame(total, durations, target=target))
        return model

    def test_adapter_sees_real_model(self):
        from src.race_context import SectorView

        view = SectorView(self._model())
        self.assertTrue(view.available)
        self.assertEqual(view.num_sectors, 4)
        self.assertEqual(len(view.player_laps(3)), 2)
        self.assertIsInstance(view.player_laps(3)[-1]["lap_time"], float)

    def test_local_answer_reports_real_sector_loss(self):
        # 前车第 3/4 段各快 1s：我应该报这两段（最多两段）
        target = dict(durations=(80.0, 80.0, 80.0),
                      sector_times=(21.0, 21.0, 19.0, 19.0), gap_ahead=1)
        decision = router.route("query_sector_loss", snapshot(), "哪一段慢",
                                segments=self._model(target))
        self.assertTrue(decision.ok)
        self.assertIn("AST_SECTOR_SLOWER_AHEAD", decision.plan)
        self.assertEqual(decision.plan.count("SPCC_FASTESTCARPOS"), 2)
        self.assertIn("第3段", decision.reason)
        self.assertIn("第4段", decision.reason)

    def test_other_class_target_is_refused(self):
        # 组别不同 → T1 的 target_for 返回 None → 本地明确拒答，不硬比
        target = dict(durations=(80.0, 80.0, 80.0), classes=(1, 2),
                      gap_ahead=1)
        decision = router.route("query_sector_loss", snapshot(),
                                "跟前车分段差多少",
                                segments=self._model(target))
        self.assertFalse(decision.ok)
        self.assertIn("识别不到", decision.reason)

    def test_own_sector_times_from_real_model(self):
        decision = router.route("query_sector_loss", snapshot(), "上一圈分段",
                                segments=self._model())
        self.assertTrue(decision.ok)
        self.assertEqual(decision.plan, ["AST_REFUSE_LATER"])


if __name__ == "__main__":
    unittest.main()
