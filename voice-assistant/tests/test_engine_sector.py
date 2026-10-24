"""v1.5.4：engine 把 DerivedTracker.segments 接进本地路由与 AI 上下文。

三条链要各自走通：
1. 本地问答：ask()/explain() 把分段模型传给 router.route；
2. AI 上下文：snapshot() 把模型挂到快照上，race_context.build 自己取；
3. all 档语义不变：规则赛段答案仍只当 AI 失败兜底，不先播一遍。
"""
import io
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.engine import Assistant
from src.query_class import QueryKind
from tests.test_engine_ai import fake_pack, make_cfg
from tests.test_router_sector import FakeSegments, default_model


def make_engine(root, ai_enabled=False, **ai):
    """构造一个不播真声的引擎；短语库直接指到 query_sector_loss。"""
    with patch("src.engine.VoicePack.load_many", return_value=fake_pack()):
        assistant = Assistant(make_cfg(root, ai_enabled, **ai),
                              no_play=True, out=io.StringIO())
    assistant.matcher = MagicMock()
    assistant.matcher.match.return_value = MagicMock(
        intent="query_sector_loss", score=0.9, phrase="哪一段慢",
        margin=0.2, reason="命中短语", ranked=[])
    return assistant


def make_playable(assistant, root):
    assistant.pack.has.return_value = True
    assistant.pack.path_of.return_value = root / "x.wav"
    assistant._pick_existing = MagicMock(
        return_value=MagicMock(text="第2段比前车慢0.3秒"))
    assistant.player.play_many = MagicMock(return_value=0.0)
    assistant.player.play = MagicMock(return_value=0.0)
    return assistant


class SegmentWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def test_snapshot_carries_segments_model(self):
        a = make_engine(self.root)
        model = default_model()
        a.derive.segments = model
        snap = a.snapshot()
        self.assertIs(getattr(snap, "segments", None), model)

    def test_snapshot_without_model_is_safe(self):
        # 分段模型不可用时（例如未来某版本没接）快照上不该有半成品属性。
        a = make_engine(self.root)
        with patch.object(a, "segments_model", return_value=None):
            snap = a.snapshot()
        self.assertIsNone(getattr(snap, "segments", None))

    def test_ask_passes_segments_to_router(self):
        from src import router
        a = make_engine(self.root)
        model = default_model()
        a.derive.segments = model
        with patch("src.engine.router.route",
                   return_value=router.Decision(["AST_SECTOR_NO_DATA"], "x")) as route:
            a.ask("哪一段慢", source="keyboard")
        self.assertIs(route.call_args.kwargs.get("segments"), model)
        self.assertEqual(route.call_args.args[0], "query_sector_loss")

    def test_explain_passes_segments_to_router(self):
        from src import router
        a = make_engine(self.root)
        model = default_model()
        a.derive.segments = model
        with patch("src.engine.router.route",
                   return_value=router.Decision(["AST_SECTOR_NO_DATA"], "x")) as route:
            a.explain("哪一段慢")
        self.assertIs(route.call_args.kwargs.get("segments"), model)

    def test_ai_context_receives_segments_layer(self):
        """AI 上下文靠 snapshot 上的引用拿到分段模型，不改流水线签名。"""
        from src import race_context
        a = make_engine(self.root)
        a.derive.segments = default_model()
        snap = a.snapshot()
        ctx = race_context.build(snap, [], QueryKind.ANALYSIS, "哪一段慢")
        self.assertIn("segments", ctx)
        self.assertEqual(ctx["segments"]["count"], 4)


class LocalSectorPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def test_sector_answer_is_played(self):
        a = make_playable(make_engine(self.root), self.root)
        a.derive.segments = default_model()
        rec = a.ask("哪一段慢", source="keyboard")
        self.assertTrue(rec["ok"])
        self.assertIn("AST_SECTOR_SLOWER_AHEAD", rec["plan"])
        a.player.play_many.assert_called_once()
        self.assertTrue(a.live["answer"])
        self.assertEqual(rec["intent"], "query_sector_loss")

    def test_missing_model_produces_no_data_answer(self):
        a = make_playable(make_engine(self.root), self.root)
        # 通过方法替身模拟「模型不可用」：不直接改 derive.segments，
        # 否则采样线程仍会按 T1 的真实路径调用它。
        with patch.object(a, "segments_model", return_value=None):
            rec = a.ask("哪一段慢", source="keyboard")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["plan"], ["AST_SECTOR_NO_DATA"])
        a.player.play_many.assert_called_once()

    def test_ai_all_mode_holds_sector_answer_for_model(self):
        # 分段问题已统一走 AI 分析路径：all 档不再先播本地赛段答案。
        a = make_playable(make_engine(self.root, ai_enabled=True, mode="all"),
                          self.root)
        a.derive.segments = default_model()
        rec = a.ask("哪一段慢", source="keyboard")
        self.assertTrue(rec["ai_eligible"])
        self.assertNotIn("fallback_plan", rec)
        a.player.play_many.assert_not_called()
        self.assertEqual(a.live["state"], "thinking")

    def test_non_all_mode_routes_sector_question_to_ai(self):
        a = make_playable(make_engine(self.root, ai_enabled=True, mode="full"),
                          self.root)
        a.derive.segments = default_model()
        rec = a.ask("哪一段慢", source="keyboard")
        self.assertTrue(rec["ai_eligible"])
        self.assertFalse(rec["ok"])
        a.player.play_many.assert_not_called()

    def test_full_split_request_is_hard_refused_before_ai(self):
        a = make_playable(make_engine(self.root, ai_enabled=True, mode="all"),
                          self.root)
        a.derive.segments = default_model()
        rec = a.ask("上一圈分段", source="keyboard")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["plan"], ["AST_REFUSE_LATER"])
        self.assertNotIn("ai_eligible", rec)
        a.player.play.assert_called_once()


if __name__ == "__main__":
    unittest.main()
