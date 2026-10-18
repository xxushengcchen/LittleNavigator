import unittest
from src.query_class import QueryKind, classify


class SafetyPriorityTests(unittest.TestCase):
    def test_penalty_reason_is_safety_not_analysis(self):
        # 含「为什么」，但属安全类，必须判 SAFETY
        self.assertIs(classify("为什么我被罚了", "query_flags"), QueryKind.SAFETY)

    def test_car_control_is_safety(self):
        self.assertIs(classify("帮我把尾翼调高两格", None), QueryKind.SAFETY)

    def test_out_of_scope_is_safety(self):
        self.assertIs(classify("现在几点了", None), QueryKind.SAFETY)


class AnalysisTests(unittest.TestCase):
    def test_why_slow_is_analysis_even_without_intent(self):
        self.assertIs(classify("刚才那个弯为什么慢了0.3秒", None), QueryKind.ANALYSIS)

    def test_analysis_wins_over_known_intent(self):
        self.assertIs(classify("我为什么进弯推头", "query_laptime"), QueryKind.ANALYSIS)


class KnownAndUnknownTests(unittest.TestCase):
    def test_known_intent_without_analysis_words(self):
        self.assertIs(classify("油还够吗", "query_fuel"), QueryKind.KNOWN)

    def test_no_intent_no_analysis_words(self):
        self.assertIs(classify("今天天气不错", None), QueryKind.UNKNOWN)

    def test_intent_present_but_garbage_text(self):
        # 即使 intent 命中，含分析词仍走 ANALYSIS（已在上面覆盖）；
        # 这里验证纯 KNOWN 分支
        self.assertIs(classify("现在第几名", "query_position"), QueryKind.KNOWN)


class SafetyVariantTests(unittest.TestCase):
    def test_soft_phrasing_of_car_control_is_still_safety(self):
        for text in ("能不能帮我调一下尾翼", "这个处罚凭什么", "帮我把TC关掉"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.SAFETY)


class BoundaryTests(unittest.TestCase):
    def test_ambiguous_zenmekai_is_not_safety(self):
        # 「怎么开」歧义太大：可能指赛道怎么跑，也可能指操作。
        # 归 UNKNOWN —— 不转模型，也不误杀成安全拒绝。
        self.assertIs(classify("这条赛道怎么开", None), QueryKind.UNKNOWN)

    def test_empty_text_is_unknown(self):
        self.assertIs(classify("", None), QueryKind.UNKNOWN)

    def test_traditional_chinese_is_normalized(self):
        # normalize 做繁转简，繁体问法应与简体判成同一类
        self.assertIs(classify("為什麼我被罰了", None), QueryKind.SAFETY)

    def test_analysis_with_no_intent_still_analysis(self):
        # 意图为 None 也要能识别分析型——这是修掉旗舰用例不可达的关键
        self.assertIs(classify("我慢在哪", None), QueryKind.ANALYSIS)


if __name__ == '__main__':
    unittest.main()
