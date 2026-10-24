"""问法边界回归：短问法不能被含英文前缀的长问法抢走。

背景（2026-10-20）：「现在几档」曾被判成 query_car_settings。
根因有两条，缺一不可：
  1) 词库里没有「现在几档 / 现在几挡」原句，只有「abs现在几档」这类带前缀的；
  2) pypinyin 的 errors="ignore" 把「abs」整个丢掉，于是「现在几档」与
     「abs现在几档」得到完全相同的拼音串，短问法白拿 1.00。

这两条都已修：词库补了原句，_syllables() 保留拉丁字母。
本文件锁住边界，防止以后又退回去。
"""
import unittest

from src import router, telemetry
from src.phrase_match import PhraseMatcher


class GearBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = PhraseMatcher()

    def test_short_gear_question_goes_to_engine(self):
        for q in ("现在几档", "现在几挡", "我现在是几档", "现在挂几挡", "几挡了", "当前挡位"):
            with self.subTest(q=q):
                self.assertEqual(self.matcher.match(q).intent, "query_engine")

    def test_assist_settings_still_go_to_car_settings(self):
        for q in ("abs几档", "tc几档", "防抱死几档", "循迹控制几档", "ABS现在几档"):
            with self.subTest(q=q):
                self.assertEqual(self.matcher.match(q).intent, "query_car_settings")

    def test_gear_answers_from_snapshot(self):
        snap = telemetry.Snapshot(connected=True, gear=4)
        self.assertEqual(router.route("query_engine", snap, "现在几档").plan[:2],
                         ["AST2_GEAR_LEAD", "SPCC_N4"])

    def test_abs_still_answers_from_settings(self):
        snap = telemetry.Snapshot(connected=True, abs_level=5)
        decision = router.route("query_car_settings", snap, "abs几档")
        self.assertTrue(decision.ok)
        self.assertEqual(decision.plan[0], "AST2_ABS_LEAD")


class PinyinChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = PhraseMatcher()

    def test_latin_prefix_changes_pinyin_string(self):
        q = self.matcher.match("现在几档")
        other = self.matcher.match("abs现在几档")
        # 两条问法可以落到不同意图，但绝不能因为拼音被清空而拿到同一个 1.00
        self.assertNotEqual((q.intent, q.score), (other.intent, other.score))

    def test_homophone_typo_still_matches(self):
        hit = self.matcher.match("油还購吗")
        self.assertEqual(hit.intent, "query_fuel")

    def test_off_topic_is_still_refused(self):
        for q in ("现在几点了", "今天星期几", "我还有几套备用轮胎"):
            with self.subTest(q=q):
                self.assertIsNone(self.matcher.match(q).intent)

    def test_common_questions_unchanged(self):
        cases = {
            "油还够吗": "query_fuel",
            "还剩多少圈": "query_laps",
            "胎压多少": "query_tyre",
            "前车多远": "query_gap",
            "我上一圈多少": "query_laptime",
            "现在第几名": "query_position",
            "现在什么旗": "query_flags",
            "事故点多少": "query_damage",
            "车速多少": "query_speed",
            "水温多少": "query_engine",
        }
        for q, intent in cases.items():
            with self.subTest(q=q):
                self.assertEqual(self.matcher.match(q).intent, intent)


if __name__ == "__main__":
    unittest.main()
