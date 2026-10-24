import io
import pathlib
import tempfile
import unittest
import unittest.mock

from src.ai_learn import LearnStore

GOOD_TEMPLATE = {
    "params": ["laps_of_fuel", "laps_remain"],
    "rules": [
        {"when": "laps_of_fuel >= laps_remain * 1.15", "say": "够 {laps_of_fuel} 圈"},
        {"when": "true", "say": "得进站"},
    ],
}


class AddFromReviewTests(unittest.TestCase):
    """复盘入库的分类规则：动态型没有合法模板就必须拒绝。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LearnStore(pathlib.Path(self.tmp.name))

    def test_dynamic_record_with_template_becomes_template(self):
        rec = {"question": "我该不该进站", "answer": "够 6.2 圈",
               "template": GOOD_TEMPLATE, "dynamic": True}
        self.assertEqual(self.store.add_from_review(rec), "template")
        self.assertEqual(len(self.store.stats()["templates"]), 1)

    def test_dynamic_record_without_template_is_rejected(self):
        # 这是本功能的底线：没有模板的动态回答只在那刻成立，
        # 存下来下次就是播报过期数值。
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

    def test_static_record_without_answer_rejected(self):
        rec = {"question": "q", "answer": "", "template": None, "dynamic": False}
        self.assertEqual(self.store.add_from_review(rec), "rejected")


class PipelineDynamicFlagTests(unittest.TestCase):
    """模型返回 dynamic/template 时必须透传到 AiResult，否则复盘无从分类。"""

    def _pipeline(self):
        from src.ai_pipeline import AiPipeline
        return AiPipeline({"ai": {"enabled": True, "mode": "full",
                                  "base_url": "http://x", "api_key": "k",
                                  "model": "m", "timeout_s": 2.0,
                                  "max_tokens": 300, "max_answer_chars": 60,
                                  "whitelist_intents": [],
                                  "tts": {"adapter": "off"}}},
                          out=io.StringIO())

    def test_dynamic_template_is_carried(self):
        from src.telemetry import Snapshot
        from src.query_class import QueryKind
        snap = Snapshot(connected=True, laps_of_fuel=6.2, laps_remain=5)
        reply = {"answer": "够 6.2 圈", "data_used": ["laps_of_fuel"],
                 "dynamic": True, "template": GOOD_TEMPLATE}
        with unittest.mock.patch.object(self._pipeline().client, "ask",
                                        return_value=reply):
            p = self._pipeline()
            with unittest.mock.patch.object(p.client, "ask", return_value=reply):
                r = p.handle("我该不该进站", snap, QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertTrue(r.dynamic)
        self.assertEqual(r.template, GOOD_TEMPLATE)

    def test_dynamic_without_template_downgrades_to_static(self):
        from src.telemetry import Snapshot
        from src.query_class import QueryKind
        snap = Snapshot(connected=True)
        reply = {"answer": "读不到", "data_used": [], "dynamic": True,
                 "template": None}
        p = self._pipeline()
        with unittest.mock.patch.object(p.client, "ask", return_value=reply):
            r = p.handle("x", snap, QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertFalse(r.dynamic)      # 声明了但没模板 → 不当动态型

    def test_static_stays_static(self):
        from src.telemetry import Snapshot
        from src.query_class import QueryKind
        snap = Snapshot(connected=True)
        reply = {"answer": "后方有快车", "data_used": [], "dynamic": False}
        p = self._pipeline()
        with unittest.mock.patch.object(p.client, "ask", return_value=reply):
            r = p.handle("x", snap, QueryKind.ANALYSIS, [])
        self.assertFalse(r.dynamic)
        self.assertIsNone(r.template)


class PromptContractTests(unittest.TestCase):
    def test_prompt_asks_for_template_structure(self):
        from src.llm_engine import LlmClient
        c = LlmClient("http://x", "k", "m")
        system = c._payload({"now": {}}, "q")["messages"][0]["content"]
        for need in ("template", "dynamic", "params", "rules", "when", "兜底"):
            self.assertIn(need, system, f"提示词缺少 {need}")


if __name__ == '__main__':
    unittest.main()
