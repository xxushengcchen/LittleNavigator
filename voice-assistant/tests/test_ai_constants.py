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
        self.assertEqual(c.LLM_DEADLINE_HARD_MAX, 2.0)
        self.assertEqual(c.TTS_DEADLINE_HARD_MAX, 1.5)
        self.assertEqual(c.AI_TOTAL_DEADLINE_HARD_MAX, 3.5)
        self.assertEqual(c.MAX_ANSWER_CHARS_HARD, 60)
        self.assertTrue(c.SINGLE_INFLIGHT)

    def test_clamp_logs_a_warning(self):
        with self.assertLogs("src.ai_constants", level="WARNING"):
            c.clamp(10.0, 2.0, "llm_timeout")

if __name__=='__main__':unittest.main()
