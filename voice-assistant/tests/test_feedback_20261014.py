"""2026-10-14 实测反馈的回归用例。

来源：31 张「意图调试台」截图（实测反馈/）。每条只允许两种合法结果：
命中期望的 msgId，或明确拒绝；**不允许答非所问**。
"""
import unittest

from src import router, telemetry
from src.phrase_match import PhraseMatcher


class FeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = PhraseMatcher()

    def ask(self, q, s=None):
        """按调试台的路径走：短语库优先，未命中再走 L1。"""
        from src import intents
        hit = self.matcher.match(q)
        intent, layer = hit.intent, "phrase"
        if intent is None:
            m = intents.match(q) or None
            forced = intents.disambiguate(q)
            if forced:
                m = intents.Match(intent=forced, confidence=0.9, why="消歧规则")
            if m:
                intent, layer = m.intent, "l1_keyword"
        if intent is None:
            return None, layer
        return router.route(intent, s or telemetry.SimTelemetry().read(), q), layer

    # ---- 一、必须拒绝（原来会自信地答错） ----
    # 甲、永久拒绝：遥测里根本没有这项，或属于范围外/规则解释，永远不该答。
    MUST_REFUSE = [
        "现在几点了",            # 原来播"车内设置尚未提供"
        "现在是什么时间",          # 原来会播绿旗
        "现在是几月几号",
        "我还有几套备用轮胎",       # 原来要求指明左右轮
        "我黑旗的原因是什么",       # 原来播当前旗号
        "我黑旗的处罚是什么",
        "前车最近一次进站是第几圈",   # 原来播"油够跑到结束"
        "我上次进站是第几圈",
        "前车进站了吗",
        "我的引擎模式是几档",       # 原来播变速箱挡位
        "还有多久会下雨",          # 原来播比赛剩余时间；SDK 没有未来天气
        "还有几圈下雨",
    ]

    # 乙、过渡性拒绝：数据能算，但派生逻辑还没写；写好即应改回可回答。
    PENDING_MATERIAL = [
        "我这圈极速慢了多少",       # 需要采样线程自记每圈最高速，尚未实现
        "我的最高速度是多少",
    ]

    def test_must_refuse(self):
        for q in self.MUST_REFUSE:
            with self.subTest(q=q):
                r, layer = self.ask(q)
                self.assertTrue(r is None or not r.ok, f"{q} 应拒绝，却给出 {getattr(r, 'plan', None)}")

    def test_pending_material_refuses_for_now(self):
        """这些是"还没录素材"，不是"不该答"：今天必须拒绝，将来必须能答。"""
        for q in self.PENDING_MATERIAL:
            with self.subTest(q=q):
                r, layer = self.ask(q)
                self.assertTrue(r is None or not r.ok, f"{q} 现在应先拒绝，却给出 {getattr(r, 'plan', None)}")

    # ---- 二、必须答对（含原来能否回答的对照） ----
    ANSWERS = {
        "我的刹车平衡是多少": "AST2_BRAKE_BIAS_LEAD",
        "后车在追进我吗": "SPCC_CARBEHINDGAIN",
        "我应该这圈进站吗": "SPCC_FUELFINRACE",
        "现在赛道积水怎么样": "SPCC_SESSIONDRY",     # TrackWetness=1 → 干
        "我的tc是什么挡位": "AST2_TC_LEAD",          # 不能被挡位抢走
        "我的abs是什么挡位": "AST2_ABS_LEAD",
        "我的水温正常吗": "AST2_WATER_TEMP_LEAD",     # 先给数值，判定类待素材
        "前车是第几名": "AST2_AHEAD_POSITION_LEAD",   # v1.2 新话术
        "后车是第几名": "AST2_BEHIND_POSITION_LEAD",
        "我离第三名还有多远": "AST2_GAP_TO_POSITION_LEAD",
        "我离组别第一多远": "AST2_GAP_TO_CLASS_LEADER_LEAD",
        "我现在的实际油耗和平均油耗差多少": "AST2_FUEL_VS_AVG_MORE_LEAD",
        "现在赛道使用率是多少": "AST2_RUBBER_MODERATE",
    }

    def test_answers(self):
        for q, first in self.ANSWERS.items():
            with self.subTest(q=q):
                r, layer = self.ask(q)
                self.assertIsNotNone(r, f"{q} 未进入路由")
                self.assertTrue(r.ok, f"{q} 应能回答：{r.reason}")
                self.assertEqual(r.plan[0], first, f"{q} → {r.plan}")


class TelemetryFieldTests(unittest.TestCase):
    def test_furled_and_dq_bits_decoded(self):
        self.assertIn("furled", telemetry.decode_flags(0x00080000))
        self.assertIn("dq_scoring_invalid", telemetry.decode_flags(0x00200000))
        # 实测：只有 servicible + start_hidden 时不算旗号
        self.assertEqual([f for f in telemetry.decode_flags(0x10040000)
                          if f not in ("furled", "dq_scoring_invalid")], [])

    def test_setup_value_cross_car_naming(self):
        sim = telemetry.SimTelemetry().read()
        self.assertEqual(sim.setup_value("ArbSetting", "AntiRollBar"), "1")
        self.assertEqual(sim.setup_value("WingAngle"), "12")
        self.assertEqual(sim.setup_value("AbsSetting"), "3 (ABS)")

    def test_flatten_setup_nested_dialects(self):
        mx5 = {"UpdateCount": 2, "Suspension": {"Front": {"AntiRollBar": "Med"}}}
        flat = telemetry.flatten_setup({k: v for k, v in mx5.items() if k != "UpdateCount"})
        self.assertEqual(flat, {"Suspension.Front.AntiRollBar": "Med"})

    def test_gap_refuses_when_player_not_on_track(self):
        s = telemetry.Snapshot(connected=True, on_track_player=False, car_idx=7,
                               car_lap_dist=[-1.0] * 64, car_est_times=[5.0] * 64,
                               session_time=100.0, lap_best=90.0)
        telemetry.DerivedTracker().update(s)
        self.assertIsNone(s.gap_ahead_s, "不在赛道上时不能算出前后车差距")


if __name__ == "__main__":
    unittest.main()
