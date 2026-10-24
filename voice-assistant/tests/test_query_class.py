"""问题分类预检：档位规则的地基。

这里钉死三件事：
  · 判定顺序 SAFETY → OPERATION → ANALYSIS → KNOWN → UNKNOWN 不可换 ——
    换一次就是红线事故（「为什么我被罚了」既含分析词又含安全词，
    顺序错就会送去调模型）；
  · SAFETY 与 OPERATION 不能混为一类 —— 前者「能做但不做」，
    后者「根本做不到」，用户听到的话必须不同；
  · 词表的形状与 brief 列的词不能被后续任务悄悄删掉或写歪。
"""
import unittest

from src.phrase_match import normalize
from src.query_class import (ANALYSIS_WORDS, OPERATION_WORDS, SAFETY_WORDS,
                             QueryKind, classify)


class SafetyPriorityTests(unittest.TestCase):
    def test_penalty_reason_is_safety_not_analysis(self):
        # 含「为什么」，但属红线，必须判 SAFETY
        self.assertIs(classify("为什么我被罚了", "query_flags"), QueryKind.SAFETY)

    def test_out_of_scope_is_safety(self):
        for text in ("现在几点了", "今天几号", "我还有几套备用胎"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.SAFETY)


class OperationTests(unittest.TestCase):
    """要求软件操作车辆 —— 软件是只读的，做不到，归 OPERATION 而非 SAFETY。"""

    def test_car_control_requests_are_operation(self):
        cases = ("帮我把尾翼调高两格", "把 TC 关掉", "设置成软胎",
                 "能不能帮我调一下尾翼", "把方向盘回正", "帮我加油")
        for text in cases:
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.OPERATION)

    def test_driving_input_advice_is_operation(self):
        # 软件没有赛道几何数据，模型也只能编，所以不交给模型
        for text in ("这个弯该几档过", "该走哪条线"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.OPERATION)

    def test_operation_is_not_safety(self):
        # 两者都不调模型，但话术不同：混成一类会让用户听到「没听清」
        self.assertIsNot(classify("设置成软胎", None), QueryKind.SAFETY)


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

    def test_empty_intent_string_counts_as_no_intent(self):
        # 现有调用方传的是 Hit.intent（None 或非空字符串）；空串只可能来自坏调用，
        # 不该凭空造出一个规则层并没有命中的事实型问题。
        self.assertIs(classify("油还够吗", ""), QueryKind.UNKNOWN)


class WordListTests(unittest.TestCase):
    """词表是后续任务往里加词的契约：形状、归一形态、brief 词一个都不能少。"""

    def test_lists_are_non_empty_tuples_of_normalized_words(self):
        for words in (SAFETY_WORDS, OPERATION_WORDS, ANALYSIS_WORDS):
            self.assertIsInstance(words, tuple)
            self.assertTrue(words)          # 先断言非空，下面才能安全地报词
            for word in words:
                with self.subTest(word=word):
                    # 词本身不经过 normalize，所以它必须已是归一形态
                    # （小写、简体、无标点），否则文本永远命中不了它。
                    self.assertTrue(word and word == normalize(word))

    def test_brief_words_are_all_present(self):
        safety = ("处罚", "被罚", "怎么判",
                  "几点", "几号", "星期几", "库存", "备用胎")
        operation = ("替我", "帮我开", "帮我关", "调高", "调低", "调到",
                     "改成", "设置为")
        analysis = ("为什么", "什么原因", "分析", "评价", "怎么看",
                    "哪里慢", "慢在哪", "稳定吗", "有没有进步", "该怎么跑", "策略")
        for word in safety:
            with self.subTest(list="safety", word=word):
                self.assertIn(word, SAFETY_WORDS)
        for word in operation:
            with self.subTest(list="operation", word=word):
                self.assertIn(word, OPERATION_WORDS)
        for word in analysis:
            with self.subTest(list="analysis", word=word):
                self.assertIn(word, ANALYSIS_WORDS)


class VariantWordTests(unittest.TestCase):
    """每个变体靠哪个词命中，逐个钉死：删掉那个词就会立刻红。"""

    def test_each_variant_is_caught_by_a_listed_word(self):
        cases = (
            ("能不能帮我调一下尾翼", "帮我调", OPERATION_WORDS),
            ("这个处罚凭什么", "处罚", SAFETY_WORDS),
            ("帮我把TC关掉", "关掉", OPERATION_WORDS),
            ("设置成软胎", "设置成", OPERATION_WORDS),
        )
        for text, word, words in cases:
            with self.subTest(text=text):
                self.assertIn(word, words)
                self.assertIn(word, normalize(text))


class AmbiguityTests(unittest.TestCase):
    """刻意排除的写法，以及不能被更宽的词误伤的口语。"""

    def test_how_to_drive_is_unknown_not_safety(self):
        # 「怎么开」歧义太大（这个弯怎么开 / 怎么开雨刷），留给 UNKNOWN：
        # 不调模型，也不误杀成拒绝。
        self.assertIs(classify("这条赛道怎么开", None), QueryKind.UNKNOWN)

    def test_bare_help_me_is_not_a_listed_word(self):
        # 「帮我」太泛：「帮我分析一下我的圈速」是分析，不能被误杀
        self.assertNotIn("帮我", OPERATION_WORDS)
        self.assertNotIn("帮我", SAFETY_WORDS)
        self.assertIs(classify("帮我分析一下我的圈速", None), QueryKind.ANALYSIS)

    def test_traditional_chinese_and_punctuation_are_normalized(self):
        self.assertIs(classify("為什麼我被罰了？", None), QueryKind.SAFETY)
        self.assertIs(classify("刚才那个弯为什么慢了0.3秒？", "query_laptime"),
                      QueryKind.ANALYSIS)

    def test_empty_text_is_unknown(self):
        self.assertIs(classify("", None), QueryKind.UNKNOWN)


class WeakOperationWordTests(unittest.TestCase):
    """弱操作词（调整/调一下/打开/关一下）单独不足以判操作。

    中文里它们也做「调取/查看」讲，或出现在分析句里。测试把这条取舍钉死，
    免得下次加词时把最该交给模型的问题误杀。
    """

    def test_analysis_beats_weak_operation_word(self):
        self.assertIs(classify("我调整了刹车之后为什么推头", None), QueryKind.ANALYSIS)

    def test_weak_word_alone_is_operation(self):
        for text in ("把雨刷打开", "TC 关一下", "帮我调一下上一圈的圈速"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.OPERATION)

    def test_strong_operation_words_still_win_over_analysis(self):
        # 真正的操作指令含强词（调高/调到/帮我调），任何情况下都是操作类
        self.assertIs(classify("为什么帮我把它调高", None), QueryKind.OPERATION)

    def test_query_about_fuel_is_not_operation(self):
        self.assertIsNot(classify("能不能帮我看看还有多少油", None),
                         QueryKind.OPERATION)


if __name__ == '__main__':
    unittest.main()
