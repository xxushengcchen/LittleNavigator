import pathlib
import tempfile
import unittest

from src.ai_learn import (LearnError, LearnStore, fill_template,
                          validate_template)

GOOD = {
    "params": ["laps_of_fuel", "laps_remain"],
    "rules": [
        {"when": "laps_of_fuel >= laps_remain * 1.15", "say": "够 {laps_of_fuel} 圈"},
        {"when": "true", "say": "得进站"},
    ],
}


class ValidateTests(unittest.TestCase):
    def test_good_template_passes(self):
        self.assertIsNone(validate_template(GOOD))

    def test_unknown_param_rejected(self):
        bad = {"params": ["fuel_pressure"], "rules": [{"when": "true", "say": "x"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_missing_fallback_rejected(self):
        bad = {"params": ["laps_of_fuel"],
               "rules": [{"when": "laps_of_fuel > 1", "say": "x"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_function_call_in_when_rejected(self):
        bad = {"params": ["laps_of_fuel"],
               "rules": [{"when": "__import__('os')", "say": "x"},
                         {"when": "true", "say": "y"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_attribute_access_rejected(self):
        bad = {"params": ["laps_of_fuel"],
               "rules": [{"when": "laps_of_fuel.real > 1", "say": "x"},
                         {"when": "true", "say": "y"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_placeholder_outside_params_rejected(self):
        bad = {"params": ["laps_remain"],
               "rules": [{"when": "true", "say": "用 {laps_of_fuel} 圈"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_field_outside_params_rejected(self):
        bad = {"params": ["laps_remain"],
               "rules": [{"when": "laps_of_fuel > 1", "say": "x"},
                         {"when": "true", "say": "y"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_empty_rules_rejected(self):
        self.assertIsNotNone(validate_template({"params": ["lap"], "rules": []}))

    def test_non_dict_rejected(self):
        self.assertIsNotNone(validate_template("nope"))


class EvalSafetyTests(unittest.TestCase):
    """求值器不能执行任意代码——学习库里的表达式来自模型。"""

    def test_semicolon_injection_rejected(self):
        bad = {"params": ["lap"],
               "rules": [{"when": "true; import os", "say": "x"},
                         {"when": "true", "say": "y"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_eval_is_not_used(self):
        import src.ai_learn as mod
        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("eval(", source)
        self.assertNotIn("exec(", source)


class FillTests(unittest.TestCase):
    def test_fill_picks_first_matching_rule(self):
        ctx = {"derived": {"laps_of_fuel": 6.2}, "now": {"laps_remain": 5}}
        self.assertEqual(fill_template(GOOD, ctx), "够 6.2 圈")

    def test_fill_falls_back(self):
        ctx = {"derived": {"laps_of_fuel": 2.0}, "now": {"laps_remain": 5}}
        self.assertEqual(fill_template(GOOD, ctx), "得进站")

    def test_missing_placeholder_returns_none(self):
        # 命中第一条规则（laps_of_fuel >= laps_remain*1.15 需要两个字段），
        # 但上下文里没有 laps_remain → 无法算 when，落到兜底分支「得进站」。
        # 真正要防的是：命中某分支、该分支 say 里的占位符缺值。
        tpl = {"params": ["laps_of_fuel", "laps_remain"],
               "rules": [
                   {"when": "laps_of_fuel >= 1", "say": "够 {laps_of_fuel} 圈"},
                   {"when": "true", "say": "不确定"},
               ]}
        # 只给 laps_of_fuel，say 里的占位符有值 → 正常填（整数不带小数点）
        self.assertEqual(fill_template(tpl, {"derived": {"laps_of_fuel": 6.0}}),
                         "够 6 圈")
        # 上下文里完全没有该字段 → when 无法求值，走兜底
        self.assertEqual(fill_template(tpl, {"derived": {}}), "不确定")

    def test_integer_formatting_has_no_decimal(self):
        tpl = {"params": ["laps_remain"],
               "rules": [{"when": "true", "say": "{laps_remain} 圈"}]}
        self.assertEqual(fill_template(tpl, {"now": {"laps_remain": 5}}), "5 圈")

    def test_invalid_template_returns_none(self):
        self.assertIsNone(fill_template({"params": [], "rules": []}, {}))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LearnStore(pathlib.Path(self.tmp.name))

    def test_add_and_match_template(self):
        self.store.add_template("我该不该进站", GOOD)
        hit = self.store.match("我该不该进站啊")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], "template")

    def test_unrelated_question_does_not_match(self):
        self.store.add_template("我该不该进站", GOOD)
        self.assertIsNone(self.store.match("今天天气怎么样"))

    def test_disabled_after_two_errors(self):
        entry = self.store.add_template("我该不该进站", GOOD)
        self.store.mark(entry["id"], ok=False)
        self.store.mark(entry["id"], ok=False)
        self.assertIsNone(self.store.match("我该不该进站"))

    def test_success_clears_error_count(self):
        entry = self.store.add_template("我该不该进站", GOOD)
        self.store.mark(entry["id"], ok=False)
        self.store.mark(entry["id"], ok=True)
        self.store.mark(entry["id"], ok=False)
        self.assertIsNotNone(self.store.match("我该不该进站"))

    def test_clear_empties_store(self):
        self.store.add_template("我该不该进站", GOOD)
        self.store.clear()
        self.assertEqual(self.store.stats()["templates"], [])

    def test_rejects_invalid_template_on_add(self):
        with self.assertRaises(LearnError):
            self.store.add_template("q", {"params": [], "rules": []})

    def test_persistence_across_instances(self):
        self.store.add_template("我该不该进站", GOOD)
        again = LearnStore(pathlib.Path(self.tmp.name))
        self.assertIsNotNone(again.match("我该不该进站"))

    def test_knowledge_roundtrip(self):
        self.store.add_knowledge("蓝旗什么意思", "后方有快车要放行")
        self.assertEqual(len(self.store.stats()["knowledge"]), 1)
        self.assertIsNotNone(self.store.match("蓝旗什么意思"))

    def test_template_wins_over_knowledge(self):
        self.store.add_knowledge("我该不该进站", "固定答案")
        self.store.add_template("我该不该进站", GOOD)
        kind, _ = self.store.match("我该不该进站")
        self.assertEqual(kind, "template")

    def test_broken_jsonl_line_is_skipped(self):
        path = pathlib.Path(self.tmp.name) / "templates.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        self.assertEqual(self.store.stats()["templates"], [])


class AddFromReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LearnStore(pathlib.Path(self.tmp.name))

    def test_dynamic_record_with_template_becomes_template(self):
        rec = {"question": "我该不该进站", "answer": "够 6.2 圈",
               "template": GOOD, "dynamic": True}
        self.assertEqual(self.store.add_from_review(rec), "template")
        self.assertEqual(len(self.store.stats()["templates"]), 1)

    def test_dynamic_record_without_template_is_rejected(self):
        # 动态型没有模板 → 绝不能存成品答案（会播报过期数值）
        rec = {"question": "我该不该进站", "answer": "够 6.2 圈",
               "template": None, "dynamic": True}
        self.assertEqual(self.store.add_from_review(rec), "rejected")
        self.assertEqual(self.store.stats()["templates"], [])
        self.assertEqual(self.store.stats()["knowledge"], [])

    def test_static_record_becomes_knowledge(self):
        rec = {"question": "蓝旗什么意思", "answer": "后方有快车要放行",
               "template": None, "dynamic": False, "clip_path": "clips/a.wav"}
        self.assertEqual(self.store.add_from_review(rec), "knowledge")
        self.assertEqual(len(self.store.stats()["knowledge"]), 1)

    def test_invalid_template_is_rejected(self):
        bad = {"params": ["not_a_field"], "rules": [{"when": "true", "say": "x"}]}
        rec = {"question": "q", "answer": "a", "template": bad, "dynamic": True}
        self.assertEqual(self.store.add_from_review(rec), "rejected")

    def test_empty_question_rejected(self):
        rec = {"question": "  ", "answer": "a", "template": None, "dynamic": False}
        self.assertEqual(self.store.add_from_review(rec), "rejected")


if __name__ == '__main__':
    unittest.main()
