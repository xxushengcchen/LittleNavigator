"""v1.5.5：低置信度意图由 AI 只做选取，本地规则再回答遥测。"""
import io
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.engine import Assistant
from src.phrase_match import Hit
from src.query_class import QueryKind
from src.router import Decision
from tests.test_engine_ai import fake_pack, make_cfg
from app.server import Service


class FakeAi:
    """AiPipeline 的替身：只提供仲裁所需属性/方法。"""
    available = True
    mode = "whitelist"
    whitelist = []
    client = None

    def __init__(self, result=None):
        self.result = result or {"intent": "query_laptime", "confidence": 0.9}
        self.calls = []

    def judge_intent(self, text, candidates, timeout_s=1.0):
        self.calls.append((text, candidates, timeout_s))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_engine(root, result=None):
    with patch("src.engine.VoicePack.load_many", return_value=fake_pack()):
        assistant = Assistant(make_cfg(root, ai_enabled=True),
                              no_play=True, out=io.StringIO())
    assistant.ai = FakeAi(result)
    assistant.matcher = MagicMock()
    assistant.matcher.match.return_value = Hit(
        None, 0.936, "", 0.023, "「query_laptime」与「query_sector_loss」只差 0.02，分不清",
        [("query_laptime", 0.936, "上圈圈速多少"),
         ("query_sector_loss", 0.913, "分段圈速多少"),
         ("query_opponent", 0.913, "前车圈速多少")])
    return assistant


class DisambiguationRequestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def test_ambiguous_phrase_returns_request_without_playing(self):
        a = make_engine(self.root)
        rec = a.ask("圈速多少")
        self.assertFalse(rec.get("ok"))
        req = rec.get("ai_disambiguate")
        self.assertIsInstance(req, dict)
        self.assertEqual(len(req["candidates"]), 3)
        self.assertEqual(req["candidates"][0]["intent"], "query_laptime")
        self.assertIn("圈速", req["candidates"][0]["label"])
        # 只是请求仲裁，没有在本进程里发网络请求
        a.ai.judge_intent = MagicMock()
        self.assertFalse(a.ai.judge_intent.called)

    def test_forced_intent_skips_arbitration_and_routes_locally(self):
        a = make_engine(self.root)
        a.play_decision = MagicMock(return_value=True)
        rec = a.ask("圈速多少", forced_intent="query_laptime")
        self.assertEqual(rec.get("intent"), "query_laptime")
        a.play_decision.assert_called_once()

    def test_force_refuse_does_not_play_or_route(self):
        a = make_engine(self.root)
        a.play_decision = MagicMock(return_value=True)
        rec = a.ask("圈速多少", force_refuse=True)
        self.assertFalse(rec.get("ok"))
        self.assertIn("没能判断", rec.get("reason"))
        a.play_decision.assert_not_called()

    def test_ai_unavailable_does_not_request(self):
        a = make_engine(self.root)
        a.ai.available = False
        rec = a.ask("圈速多少")
        self.assertNotIn("ai_disambiguate", rec)
        self.assertFalse(rec.get("ok"))

    def test_safety_kind_does_not_request(self):
        a = make_engine(self.root)
        a.matcher.match.return_value = Hit(
            None, 0.9, "", 0.02, "分不清",
            [("query_laptime", 0.9, "圈速"), ("query_sector_loss", 0.88, "分段")])
        with patch("src.engine.classify", return_value=QueryKind.SAFETY):
            rec = a.ask("圈速多少")
        self.assertNotIn("ai_disambiguate", rec)


class JudgeIntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def candidates(self):
        return [{"intent": "query_laptime", "label": "圈速", "examples": ["圈速"]},
                {"intent": "query_sector_loss", "label": "赛段", "examples": ["哪一段"]}]

    def test_accepts_valid_candidate(self):
        a = make_engine(self.root)
        self.assertEqual(a.judge_intent("圈速多少", self.candidates()),
                         "query_laptime")
        self.assertTrue(a.ai.calls)

    def test_rejects_confidence_below_threshold(self):
        a = make_engine(self.root, {"intent": "query_laptime", "confidence": 0.2})
        self.assertIsNone(a.judge_intent("圈速多少", self.candidates()))

    def test_rejects_intent_outside_candidates(self):
        a = make_engine(self.root, {"intent": "query_engine", "confidence": 0.99})
        self.assertIsNone(a.judge_intent("圈速多少", self.candidates()))

    def test_judge_exception_returns_none(self):
        a = make_engine(self.root, RuntimeError("timeout"))
        self.assertIsNone(a.judge_intent("圈速多少", self.candidates()))


class ServiceDisambiguationTests(unittest.TestCase):
    def test_service_calls_ai_then_reasks_with_forced_intent(self):
        svc = Service.__new__(Service)
        svc.last_ask = None
        svc.asst = MagicMock()
        svc.asst.judge_intent.return_value = "query_laptime"
        request = {"candidates": [{"intent": "query_laptime"}]}
        rec = {"ok": False, "ai_disambiguate": request}
        svc.asst.ask.return_value = {"ok": True, "intent": "query_laptime"}
        out = svc._maybe_disambiguate(rec, "圈速多少", "voice", {"x": 1})
        self.assertEqual(out["intent"], "query_laptime")
        kwargs = svc.asst.ask.call_args.kwargs
        self.assertEqual(kwargs["forced_intent"], "query_laptime")
        self.assertTrue(svc.asst.judge_intent.called)

    def test_service_falls_back_to_refusal_when_ai_cannot_judge(self):
        svc = Service.__new__(Service)
        svc.last_ask = None
        svc.asst = MagicMock()
        svc.asst.judge_intent.return_value = None
        rec = {"ok": False, "ai_disambiguate": {"candidates": []}}
        svc.asst.ask.return_value = {"ok": False, "reason": "没能判断"}
        out = svc._maybe_disambiguate(rec, "圈速多少", "voice", None)
        self.assertFalse(out["ok"])
        kwargs = svc.asst.ask.call_args.kwargs
        self.assertTrue(kwargs["force_refuse"])
        self.assertFalse(kwargs["allow_disambiguate"])


if __name__ == "__main__":
    unittest.main()
