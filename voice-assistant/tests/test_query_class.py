"""问题分类预检：档位规则的地基。

这里钉死两件事：
  · 判定顺序 SAFETY → ANALYSIS → KNOWN → UNKNOWN 不可换 —— 换一次就是红线事故
    （「为什么我被罚了」既含分析词又含安全词，顺序错就会送去调模型）；
  · 词表的形状与 brief 列的词不能被后续任务悄悄删掉或写歪。
"""
import unittest

from src.phrase_match import normalize
from src.query_class import ANALYSIS_WORDS, SAFETY_WORDS, QueryKind, classify


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

    def test_empty_intent_string_counts_as_no_intent(self):
        # 现有调用方传的是 Hit.intent（None 或非空字符串）；空串只可能来自坏调用，
        # 不该凭空造出一个规则层并没有命中的事实型问题。
        self.assertIs(classify("油还够吗", ""), QueryKind.UNKNOWN)


class SafetyVariantTests(unittest.TestCase):
    def test_soft_phrasing_of_car_control_is_still_safety(self):
        for text in ("能不能帮我调一下尾翼", "这个处罚凭什么", "帮我把TC关掉"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.SAFETY)


class WordListTests(unittest.TestCase):
    """词表是后续任务往里加词的契约：形状、归一形态、brief 词一个都不能少。"""

    def test_lists_are_non_empty_tuples_of_normalized_words(self):
        for words in (SAFETY_WORDS, ANALYSIS_WORDS):
            self.assertIsInstance(words, tuple)
            self.assertTrue(words)          # 先断言非空，下面才能安全地报词
            for word in words:
                with self.subTest(word=word):
                    # 词本身不经过 normalize，所以它必须已是归一形态
                    # （小写、简体、无标点），否则文本永远命中不了它。
                    self.assertTrue(word and word == normalize(word))

    def test_brief_words_are_all_present(self):
        safety = ("处罚", "被罚", "怎么判", "替我", "帮我开", "帮我关",
                  "调高", "调低", "调到", "改成", "设置为",
                  "几点", "几号", "星期几", "库存", "备用胎")
        analysis = ("为什么", "什么原因", "分析", "评价", "怎么看",
                    "哪里慢", "慢在哪", "稳定吗", "有没有进步", "该怎么跑", "策略")
        for word in safety:
            with self.subTest(word=word):
                self.assertIn(word, SAFETY_WORDS)
        for word in analysis:
            with self.subTest(word=word):
                self.assertIn(word, ANALYSIS_WORDS)


class SafetyVariantWordTests(unittest.TestCase):
    """三个安全变体各靠哪个词命中，逐个钉死：删掉那个词就会立刻红。"""

    def test_each_variant_is_caught_by_a_listed_word(self):
        cases = (
            ("能不能帮我调一下尾翼", "帮我调"),
            ("这个处罚凭什么", "处罚"),
            ("帮我把TC关掉", "关掉"),
        )
        for text, word in cases:
            with self.subTest(text=text):
                self.assertIn(word, SAFETY_WORDS)
                self.assertIn(word, normalize(text))


class AmbiguityTests(unittest.TestCase):
    """brief 明确排除的写法，以及不能被更宽的安全词误伤的口语。

    「怎么开」刻意不进 SAFETY：它既可能是「这个弯怎么开」（分析），也可能是
    「怎么开雨刷」（操作）。歧义大的词放进 SAFETY 会误杀分析类问题，所以留给
    UNKNOWN —— 不调模型，也不误杀成安全拒绝。
    """

    def test_how_to_drive_is_unknown_not_safety(self):
        self.assertIs(classify("这条赛道怎么开", None), QueryKind.UNKNOWN)

    def test_bare_help_me_is_not_a_safety_word(self):
        # 「帮我开 / 帮我关 / 帮我调」是代劳操作；裸「帮我」会把
        # 「帮我分析一下我的圈速」误杀成安全拒绝，所以不能收。
        self.assertNotIn("帮我", SAFETY_WORDS)
        self.assertIs(classify("帮我分析一下我的圈速", None), QueryKind.ANALYSIS)

    def test_traditional_chinese_and_punctuation_are_normalized(self):
        # 复用 phrase_match.normalize：繁→简、去标点。
        self.assertIs(classify("為什麼我被罰了？", None), QueryKind.SAFETY)
        self.assertIs(classify("刚才那个弯为什么慢了0.3秒？", "query_laptime"),
                      QueryKind.ANALYSIS)

    def test_empty_text_is_unknown(self):
        self.assertIs(classify("", None), QueryKind.UNKNOWN)


if __name__=='__main__':unittest.main()
