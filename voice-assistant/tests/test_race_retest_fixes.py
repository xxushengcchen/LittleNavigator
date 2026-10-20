"""第二场真机回放：无标点疑问不改车、查询语义与播报前数据时效。"""
import io
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src import command_match, router
from src.ai_pipeline import AiPipeline, AiResult
from src.car_control import CarController
from src.engine import Assistant
from src.phrase_match import PhraseMatcher
from src.query_class import QueryKind, classify
from src.telemetry import Snapshot
from tests.test_engine_car import make_cfg, fake_pack
from tests.test_live_race_fixes import GameSdk


class SpokenCommandBoundaryTests(unittest.TestCase):
    def test_unpunctuated_questions_and_descriptions_never_become_commands(self):
        for q in ('解释现在是否需要快修哪些数据能够支持这个判断',
                  '我是否需要快修', '快修有没有开启', '现在快修是什么状态',
                  '帮我分析是否需要快修', '快修已经勾上了', '上次进站用了快修',
                  '我能不能取消快修', '解释取消快修的作用',
                  '是否要换左侧轮胎', '分析加10升油的必要性', '撕膜有没有必要',
                  '快修有必要么', '如果换左侧轮胎', '我在考虑加10升油'):
            with self.subTest(q=q):
                self.assertIsNone(command_match.match(q))

    def test_explicit_commands_without_punctuation_still_work(self):
        cases = [('帮我取消换胎', 'clear_tires'),
                 ('我马上要进站帮我换左侧轮胎', 'tyre'),
                 ('我要进站了帮我加10升油', 'fuel'),
                 ('我要进站了帮我取消快修', 'clear_fr'),
                 ('我要进站了帮我勾上快修', 'fast_repair'),
                 ('快修', 'fast_repair'), ('进站快修', 'fast_repair'),
                 ('帮我撕膜', 'tearoff'), ('我马上要进站了帮我把快修勾上', 'fast_repair')]
        for q, kind in cases:
            with self.subTest(q=q):
                self.assertEqual(command_match.match(q)['kind'], kind)

    def test_written_question_mark_is_only_additional_protection(self):
        self.assertIsNone(command_match.match('快修？'))
        self.assertIsNot(classify('快修？', 'query_pit'), QueryKind.OPERATION)

    def test_real_engine_does_not_broadcast_for_unpunctuated_fast_repair_question(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_cfg(pathlib.Path(td), True)
            sdk = GameSdk(connected=True, flags=0x35)  # 快修原本关闭
            with patch('src.engine.VoicePack.load_many', return_value=fake_pack()):
                a = Assistant(cfg, no_play=True, out=io.StringIO())
            self.addCleanup(a.close)
            a.car = CarController(cfg, sdk=sdk)
            a.snapshot = lambda: Snapshot(connected=True, pit_sv_flags=sdk.flags)
            result = a.ask('解释现在是否需要快修哪些数据能够支持这个判断', source='voice')
            self.assertNotEqual(result.get('kind'), 'operation')
            self.assertEqual(sdk.calls, [])
            self.assertEqual(sdk.flags, 0x35)


class QuerySemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = PhraseMatcher()

    def test_lap_gap_does_not_return_seconds(self):
        s = Snapshot(connected=True, gap_ahead_s=.524, gap_ahead_trend=.01)
        for q in ('前车和我差几圈', '我和后车差多少圈', '我落后前车几圈'):
            with self.subTest(q=q):
                result = router.route('query_gap', s, q)
                self.assertFalse(result.ok)
                self.assertIn('圈差', result.reason)
        self.assertEqual(self.matcher.match('前车和我差几圈').intent, 'query_gap')

    def test_numeric_nearness_question_is_not_trend(self):
        s = Snapshot(connected=True, gap_behind_s=.642, gap_behind_trend=.015,
                     gap_ahead_s=1.2, gap_ahead_trend=-.02, gap_estimated=True)
        for q in ('后车离我多近', '前车有多近'):
            with self.subTest(q=q):
                self.assertEqual(self.matcher.match(q).intent, 'query_gap')
                result = router.route('query_gap', s, q)
                self.assertTrue(result.ok)
                self.assertIn('SPCC_GAPIS_PT2', result.plan)
                self.assertNotIn('AST2_GAP_STABLE', result.plan)

    def test_repair_time_question_works_in_strict_voice_mode(self):
        self.assertEqual(self.matcher.match('维修还剩多久').intent, 'query_pit')
        inactive = router.route('query_pit', Snapshot(connected=True, pitstop_active=False), '维修还剩多久')
        self.assertFalse(inactive.ok)
        self.assertIn('维修尚未进行', inactive.reason)
        active = router.route('query_pit', Snapshot(connected=True, pitstop_active=True,
                              repair_required_s=30, repair_optional_s=90), '维修还剩多久')
        self.assertTrue(active.ok)
        self.assertIn('SPCC_MINUTE', active.plan)

    def test_remaining_race_time_still_maps_to_laps(self):
        self.assertEqual(self.matcher.match('还剩多久').intent, 'query_laps')

    def test_natural_analysis_forms_are_analysis_without_question_marks(self):
        for q in ('根据我最近几圈的数据我的驾驶稳定性怎么样',
                  '结合实际数据总结当前最需要注意的三件事简短回答',
                  '刚才进站后我的节奏和油量有什么变化', '我是否需要快修'):
            with self.subTest(q=q):
                self.assertIs(classify(q, 'query_fuel'), QueryKind.ANALYSIS)

    def test_analysis_is_not_short_circuited_by_a_fuel_rule_answer(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_cfg(pathlib.Path(td), False)
            cfg['ai'] = {'enabled': True, 'mode': 'whitelist'}
            with patch('src.engine.VoicePack.load_many', return_value=fake_pack()):
                a = Assistant(cfg, no_play=True, out=io.StringIO())
            self.addCleanup(a.close)
            a.snapshot = lambda: Snapshot(connected=True, fuel_level=42.1)
            with patch.object(a, '_play_plan') as play:
                result = a.ask('刚才进站后我的节奏和油量有什么变化')
            self.assertEqual(result['kind'], 'analysis')
            self.assertTrue(result['ai_eligible'])
            play.assert_not_called()
            self.assertFalse(a._ai_should_engage(QueryKind.KNOWN, '前车和我差几圈', 'query_gap', False))
            self.assertFalse(a._ai_should_engage(QueryKind.ANALYSIS, '分析我与前车圈差', None, False))
            self.assertTrue(a._ai_should_engage(QueryKind.ANALYSIS, '分析差距是不是上一圈造成的', 'query_gap', False))


class FreshnessTests(unittest.TestCase):
    def pipe(self, current):
        player = MagicMock(enabled=True)
        p = AiPipeline({'ai': {'mode': 'full', 'base_url': 'http://test',
                              'api_key': 'test', 'model': 'test'}},
                       out=io.StringIO(), player=player, snapshot_provider=lambda: current)
        p.tts = MagicMock()
        p.tts.synth.side_effect = lambda text: text.encode('utf-8')
        p.client = MagicMock(last_latency_ms=10)
        p.client.ask.return_value = {'answer': '当前油量41.7升', 'data_used': ['fuel_level']}
        return p

    def test_refuel_during_model_call_prevents_old_answer_and_template_playback(self):
        before = Snapshot(connected=True, fuel_level=41.7)
        p = self.pipe(Snapshot(connected=True, fuel_level=51.7))
        r = p.handle('分析油量', before, QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        self.assertEqual(r.checks['freshness'], 'expired')
        self.assertIsNone(r.template)
        p.player.play_bytes.assert_called_once_with('数据已经变化，请重新提问。'.encode('utf-8'))

    def test_refuel_during_tts_is_checked_again_before_play(self):
        current = Snapshot(connected=True, fuel_level=41.7)
        p = self.pipe(current)
        def synth(text):
            current.fuel_level = 51.7
            return text.encode('utf-8')
        p.tts.synth.side_effect = synth
        r = p.handle('分析油量', Snapshot(connected=True, fuel_level=41.7), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        p.player.play_bytes.assert_called_once_with('数据已经变化，请重新提问。'.encode('utf-8'))

    def test_pit_and_session_changes_and_disconnect_reject_old_answer(self):
        before = Snapshot(connected=True, fuel_level=41.7, session_id=(1, 0), on_pit_road=False)
        for current in (Snapshot(connected=False),
                        Snapshot(connected=True, session_id=(2, 0)),
                        Snapshot(connected=True, session_id=(1, 0), on_pit_road=True)):
            with self.subTest(current=current):
                self.assertFalse(self.pipe(current).handle('分析油量', before, QueryKind.ANALYSIS, []).ok)

    def test_ordinary_fuel_consumption_does_not_block_answer(self):
        p = self.pipe(Snapshot(connected=True, fuel_level=41.6))
        r = p.handle('分析油量', Snapshot(connected=True, fuel_level=41.7), QueryKind.ANALYSIS, [])
        self.assertTrue(r.ok)
        self.assertEqual(r.checks['tts'], 'ok')
        p.player.play_bytes.assert_called_once_with('当前油量41.7升'.encode('utf-8'))

    def test_expired_time_budget_without_real_sleep(self):
        from src import ai_constants as const
        clock = [0.0]
        p = self.pipe(Snapshot(connected=True, fuel_level=41.7))
        reply = p.client.ask.return_value
        def slow(*_):
            # 越过总预算即可，用常量而不是写死秒数：v1.5.1 把 3.5s 放宽到 6.0s，
            # 写死 4.0 的旧断言会变成「没超时」，契约再调整时也会假红。
            clock[0] = const.AI_TOTAL_DEADLINE_HARD_MAX + 0.5
            return reply
        p.client.ask.side_effect = slow
        with patch('src.ai_pipeline.time.monotonic', side_effect=lambda: clock[0]):
            r = p.handle('分析油量', Snapshot(connected=True, fuel_level=41.7), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        self.assertIn('预算', r.reason)

    def test_already_old_snapshot_is_not_treated_as_fresh(self):
        import time
        p = self.pipe(Snapshot(connected=True, fuel_level=41.7))
        old = Snapshot(connected=True, fuel_level=41.7, t=time.time()-30)
        r = p.handle('分析油量', old, QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        self.assertEqual(r.checks['freshness'], 'expired')

    def test_learned_answers_use_the_same_freshness_check(self):
        p = self.pipe(Snapshot(connected=True, fuel_level=51.7))
        with patch.object(p, '_from_learn', return_value=AiResult(ok=True, text='油量41.7升', source='learn')):
            r = p.handle('分析油量', Snapshot(connected=True, fuel_level=41.7), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        p.client.ask.assert_not_called()

    def test_rejected_number_keeps_actual_model_answer_and_validation_context(self):
        p = self.pipe(Snapshot(connected=True, fuel_level=41.7))
        p.client.ask.return_value = {'answer': '油量99999升', 'data_used': ['fuel_level']}
        r = p.handle('分析油量', Snapshot(connected=True, fuel_level=41.7), QueryKind.ANALYSIS, [])
        self.assertFalse(r.ok)
        self.assertEqual(r.evidence['answer'], '油量99999升')
        self.assertEqual(r.evidence['context']['now']['fuel_level'], 41.7)
        p.player.play_bytes.assert_not_called()


if __name__ == '__main__':
    unittest.main()
