import unittest
from src import ai_constants as c

class ClampTests(unittest.TestCase):
    def test_values_under_max_pass_through(self):
        self.assertEqual(c.clamp(1.0, 2.0, "llm_timeout"), 1.0)

    def test_values_over_max_are_clamped(self):
        self.assertEqual(c.clamp(10.0, 2.0, "llm_timeout"), 2.0)

    def test_clamp_int_over_max(self):
        self.assertEqual(c.clamp_int(200, 60, "max_answer_chars"), 60)

    def test_hard_constants_match_spec(self):
        # v1.5.1 放宽：旧值 2.0/1.5/3.5 下「全部交给 AI」档几乎必然超时降级。
        self.assertEqual(c.LLM_DEADLINE_HARD_MAX, 3.0)
        self.assertEqual(c.TTS_DEADLINE_HARD_MAX, 2.5)
        self.assertEqual(c.AI_TOTAL_DEADLINE_HARD_MAX, 6.0)
        self.assertEqual(c.MAX_INPUT_TOKENS_HARD, 1500)
        self.assertEqual(c.MAX_OUTPUT_TOKENS_HARD, 300)
        self.assertEqual(c.MAX_ANSWER_CHARS_HARD, 60)
        self.assertTrue(c.SINGLE_INFLIGHT)

    def test_total_deadline_covers_llm_plus_tts(self):
        # 总预算必须容得下两道子预算，否则后改一道常量就会让另一道永远用不上。
        self.assertGreaterEqual(c.AI_TOTAL_DEADLINE_HARD_MAX,
                                c.LLM_DEADLINE_HARD_MAX + c.TTS_DEADLINE_HARD_MAX)

    def test_clamp_logs_a_warning(self):
        with self.assertLogs("src.ai_constants", level="WARNING"):
            c.clamp(10.0, 2.0, "llm_timeout")

    def test_value_equal_to_hard_max_does_not_warn(self):
        # 边界值是合法配置，不该刷告警（钳制只对严格 `>` 生效），否则上界本身
        # 就成了噪声源。assertNoLogs 需要 Python 3.10+，仓库跑 3.12，没问题。
        with self.assertNoLogs("src.ai_constants", level="WARNING"):
            self.assertEqual(c.clamp(2.0, 2.0, "llm_timeout"), 2.0)

    def test_value_under_max_does_not_warn(self):
        with self.assertNoLogs("src.ai_constants", level="WARNING"):
            self.assertEqual(c.clamp(1.0, 2.0, "llm_timeout"), 1.0)

    def test_clamp_int_value_equal_to_hard_max_does_not_warn(self):
        with self.assertNoLogs("src.ai_constants", level="WARNING"):
            self.assertEqual(c.clamp_int(60, 60, "max_answer_chars"), 60)


class NonFiniteTests(unittest.TestCase):
    """非有限值（NaN / +inf / -inf）必须被拦住，不能靠 `>` 比较兜底。

    为什么这三者要区别对待：
    - `nan`：`nan > 2.0` 是 False，只靠比较会让 NaN 原样穿透，最后在
      `urllib.request.urlopen(timeout=nan)` 那里才炸。YAML 支持 `.nan`，
      配置写坏了就应该在读配置时立刻炸出来，而不是静默换成 2.0 —— 静默
      兜底会掩盖上游 bug。→ 抛 ValueError。
    - `+inf`：语义上就是「大到没有意义」，钳到 hard_max 正是钳制的本意，
      没有信息损失，也不需要炸。→ 钳到 hard_max 并 warning。
    - `-inf`：负的无穷延迟/负的 token 上限没有任何合理解释，只能说明配置
      坏了，钳成一个具体的负数同样是掩盖问题。→ 抛 ValueError。
    """

    def test_nan_is_rejected(self):
        with self.assertRaises(ValueError):
            c.clamp(float("nan"), 2.0, "llm_timeout")

    def test_positive_infinity_is_clamped_to_hard_max(self):
        with self.assertLogs("src.ai_constants", level="WARNING"):
            self.assertEqual(c.clamp(float("inf"), 2.0, "llm_timeout"), 2.0)

    def test_negative_infinity_is_rejected(self):
        with self.assertRaises(ValueError):
            c.clamp(float("-inf"), 2.0, "llm_timeout")

    def test_finite_values_are_unaffected_by_the_guard(self):
        # 有效值（含 0 和恰好等于硬上限的边界）行为与修复前完全一致。
        self.assertEqual(c.clamp(0.0, 2.0, "llm_timeout"), 0.0)
        self.assertEqual(c.clamp(1.0, 2.0, "llm_timeout"), 1.0)
        self.assertEqual(c.clamp(2.0, 2.0, "llm_timeout"), 2.0)
        self.assertEqual(c.clamp(2.5, 2.0, "llm_timeout"), 2.0)

    def test_clamp_int_nan_is_rejected(self):
        # isinstance(float('nan'), int) 是 False，类型注解拦不住 NaN，必须显式判。
        with self.assertRaises(ValueError):
            c.clamp_int(float("nan"), 60, "max_answer_chars")

    def test_clamp_int_positive_infinity_is_clamped_to_hard_max(self):
        with self.assertLogs("src.ai_constants", level="WARNING"):
            self.assertEqual(c.clamp_int(float("inf"), 60, "max_answer_chars"), 60)

    def test_clamp_int_negative_infinity_is_rejected(self):
        with self.assertRaises(ValueError):
            c.clamp_int(float("-inf"), 60, "max_answer_chars")

    def test_clamp_int_finite_values_are_unaffected(self):
        self.assertEqual(c.clamp_int(0, 60, "max_answer_chars"), 0)
        self.assertEqual(c.clamp_int(60, 60, "max_answer_chars"), 60)
        self.assertEqual(c.clamp_int(61, 60, "max_answer_chars"), 60)


class NumericEdgeCaseTests(unittest.TestCase):
    """超大 int 与 bool 这两类「合法但不是普通数值」的输入。

    回归背景：`_reject_non_finite` 最初对所有值调用 `math.isfinite`，而
    `math.isfinite(10**400)` 会先把 int 转 float，抛
    `OverflowError: int too large to convert to float` —— 一个超大但完全合法
    的 token 上限从「被钳到 60」变成崩溃。int 是任意精度、天然有限，必须
    短路绕过 float 转换。
    """

    def test_clamp_int_huge_int_is_clamped_not_overflow(self):
        with self.assertLogs("src.ai_constants", level="WARNING"):
            self.assertEqual(c.clamp_int(10**400, 60, "max_answer_chars"), 60)

    def test_clamp_int_huge_int_below_hard_max_passes_through(self):
        # 不被钳制时也必须能原样返回：不能再撞上 float 转换。
        self.assertEqual(c.clamp_int(10**400, 10**500, "max_input_tokens"), 10**400)

    def test_clamp_huge_int_is_clamped_not_overflow(self):
        with self.assertLogs("src.ai_constants", level="WARNING"):
            self.assertEqual(c.clamp(10**400, 2.0, "llm_timeout"), 2.0)

    def test_clamp_bool_returns_float(self):
        # bool 是 int 的子类、不是 float 的子类，原样返回会违反 `-> float`。
        result = c.clamp(True, 2.0, "llm_timeout")
        self.assertEqual(result, 1.0)
        self.assertIs(type(result), float)

    def test_clamp_int_bool_returns_int(self):
        result = c.clamp_int(True, 60, "max_answer_chars")
        self.assertEqual(result, 1)
        self.assertIs(type(result), int)

if __name__=='__main__':unittest.main()
