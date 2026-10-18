import unittest
from src.answer_checks import (check_length, check_data, check_numbers,
                               run_all)

CTX = {
    "now": {"lap_last": 83.512, "laps_remain": 5, "fuel_level": 18.4,
            "position": 4, "relative_humidity": 0.62},
    "derived": {"laps_of_fuel": 6.2, "delta_to_best": 0.4, "laps_spare": 1.2},
}


class LengthTests(unittest.TestCase):
    def test_short_answer_passes(self):
        self.assertIsNone(check_length("油够跑六圈", 60))

    def test_over_limit_fails(self):
        self.assertIsNotNone(check_length("字" * 61, 60))

    def test_exactly_at_limit_passes(self):
        self.assertIsNone(check_length("字" * 60, 60))

    def test_empty_answer_passes(self):
        self.assertIsNone(check_length("", 60))


class DataTests(unittest.TestCase):
    def test_known_fields_pass(self):
        self.assertIsNone(check_data(["laps_of_fuel"], CTX))

    def test_invented_field_fails(self):
        self.assertIsNotNone(check_data(["fuel_pressure"], CTX))

    def test_empty_list_passes(self):
        self.assertIsNone(check_data([], CTX))

    def test_non_list_fails(self):
        self.assertIsNotNone(check_data("laps_of_fuel", CTX))


class NumberTests(unittest.TestCase):
    def test_exact_number_passes(self):
        self.assertIsNone(check_numbers("油还够跑 6.2 圈", CTX, "油还够吗"))

    def test_invented_number_fails(self):
        self.assertIsNotNone(check_numbers("油还够跑 9.9 圈", CTX, "油还够吗"))

    def test_chinese_numerals_are_rejected(self):
        self.assertIsNotNone(check_numbers("油还够跑六圈", CTX, "油还够吗"))

    def test_laptime_minutes_seconds_is_allowed(self):
        # 84.512 = 1 分 24.5 秒，与 lap_last 83.512 差 1 秒内
        self.assertIsNone(check_numbers("上一圈 1 分 24 秒", CTX, "上圈多少"))

    def test_percentage_scaling_is_allowed(self):
        self.assertIsNone(check_numbers("湿度 62%", CTX, "湿度多少"))

    def test_percentage_without_source_fails(self):
        self.assertIsNotNone(check_numbers("湿度 91%", CTX, "湿度多少"))

    def test_ordinal_matches_position(self):
        self.assertIsNone(check_numbers("现在第 4 名", CTX, "第几名"))

    def test_ordinal_mismatch_fails(self):
        self.assertIsNotNone(check_numbers("现在第 2 名", CTX, "第几名"))

    def test_number_from_question_is_exempt(self):
        self.assertIsNone(check_numbers("确实慢了 0.3 秒", CTX, "为什么慢了0.3秒"))

    def test_answer_without_numbers_passes(self):
        self.assertIsNone(check_numbers("雨要来了，注意轮胎", CTX, "天气"))

    def test_integer_rounding_is_rejected(self):
        # 6.2 → 「6 圈」差 0.2，超过 0.05 容差，必须拒绝
        self.assertIsNotNone(check_numbers("油还够跑 6 圈", CTX, "油还够吗"))

    def test_laps_spare_passes(self):
        self.assertIsNone(check_numbers("还多 1.2 圈", CTX, "油"))

    def test_delta_passes(self):
        self.assertIsNone(check_numbers("比最佳慢 0.4 秒", CTX, "圈速"))


class RunAllTests(unittest.TestCase):
    def test_all_pass_returns_true(self):
        ok, checks = run_all({"answer": "油还够跑 6.2 圈", "data_used": ["laps_of_fuel"]},
                             CTX, "油还够吗", 60)
        self.assertTrue(ok)
        self.assertEqual(checks, {"length": "pass", "data": "pass", "number": "pass"})

    def test_one_fail_returns_false(self):
        ok, checks = run_all({"answer": "油还够跑 9.9 圈", "data_used": ["laps_of_fuel"]},
                             CTX, "油还够吗", 60)
        self.assertFalse(ok)
        self.assertEqual(checks["number"], "fail")
        self.assertEqual(checks["length"], "pass")

    def test_reasons_are_reported(self):
        ok, checks = run_all({"answer": "字" * 100, "data_used": ["bogus"]},
                             CTX, "油", 60)
        self.assertFalse(ok)
        self.assertIn("reasons", checks)

    def test_missing_keys_do_not_raise(self):
        ok, checks = run_all({}, CTX, "油", 60)
        self.assertTrue(ok)      # 空回答没有数字、没有字段，三道闸都过


if __name__ == '__main__':
    unittest.main()


class DottedPathTests(unittest.TestCase):
    """嵌套数据用点路径引用是合法的，但路径本身必须真实存在。"""

    NESTED = {
        "now": {"position": 4, "laps_remain": 5},
        "derived": {"laps_of_fuel": 6.2},
        "opponents": {"leader": {"pos": 1, "best_lap": 82.1},
                      "ahead": {"gap_s": 1.8}},
    }

    def test_valid_dotted_path_passes(self):
        self.assertIsNone(check_data(["opponents.leader.best_lap"], self.NESTED))

    def test_valid_two_level_path_passes(self):
        self.assertIsNone(check_data(["opponents.ahead.gap_s"], self.NESTED))

    def test_missing_intermediate_rejected(self):
        self.assertIsNotNone(check_data(["opponents.behind.gap_s"], self.NESTED))

    def test_missing_leaf_rejected(self):
        self.assertIsNotNone(check_data(["opponents.leader.tyre_temp"], self.NESTED))

    def test_leaf_outside_whitelist_rejected(self):
        # 路径存在但末段字段不在白名单——防的是「顺着结构编一个新字段名」
        nested = {"opponents": {"leader": {"fabricated": 1}}}
        self.assertIsNotNone(check_data(["opponents.leader.fabricated"], nested))

    def test_flat_names_still_work(self):
        self.assertIsNone(check_data(["laps_of_fuel", "position"], self.NESTED))

    def test_abbreviated_path_without_layer_prefix_passes(self):
        # 模型常省略层名：leader.best_lap 等价于 opponents.leader.best_lap
        self.assertIsNone(check_data(["leader.best_lap"], self.NESTED))

    def test_bare_leaf_name_passes(self):
        self.assertIsNone(check_data(["best_lap"], self.NESTED))

    def test_abbreviated_path_to_missing_node_still_rejected(self):
        self.assertIsNotNone(check_data(["behind.gap_s"], self.NESTED))


class ChineseMeasureWordTests(unittest.TestCase):
    """中文数字做量词时不是数值，不能误杀。

    实测：三条真实回答（前车圈速 / 圈速稳定吗 / 有没有进步）里明明只有
    阿拉伯数字，却因为「最近一圈」的「一」被判成中文数字表述。
    """

    CTX = {"now": {"lap_last": 83.512, "lap_best": 83.104},
           "derived": {"delta_to_best": 0.4}}

    def test_zuijin_yiquan_is_measure_word(self):
        self.assertIsNone(check_numbers(
            "稳定，最近一圈83.512，最佳83.104，差0.4。", self.CTX, "圈速稳定吗"))

    def test_zhe_yiquan_is_measure_word(self):
        self.assertIsNone(check_numbers(
            "这一圈83.512，比最佳慢0.4。", self.CTX, ""))

    def test_quantity_claim_still_rejected(self):
        # 「还剩一圈」是在陈述数量，不是量词——必须继续拦
        self.assertIsNotNone(check_numbers("还剩一圈", self.CTX, ""))

    def test_chinese_quantity_still_rejected(self):
        self.assertIsNotNone(check_numbers("油还够跑六圈", self.CTX, ""))
