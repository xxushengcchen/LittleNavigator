"""2026-10-19 真机：独立SDK连接、精确服务位、取消确认与文字AI链路。"""
import io
import pathlib
import tempfile
import threading
import types
import unittest
from unittest.mock import MagicMock, patch

from app.server import Service
from src import car_control as cc
from src.car_control import CarController
from src.engine import Assistant
from tests.test_engine_car import make_cfg, fake_pack


class GameSdk:
    def __init__(self, connected=False, flags=0x7F):
        self.is_initialized = connected
        self.is_connected = connected
        self.flags = flags
        self.fuel = 100
        self.calls = []
        self.starts = 0
        self.stops = 0
        self.game_running = True

    def startup(self):
        self.starts += 1
        self.is_initialized = self.game_running
        self.is_connected = self.game_running
        return self.is_initialized

    def shutdown(self):
        self.stops += 1
        self.is_initialized = self.is_connected = False

    def pit_command(self, mode, var=0):
        self.calls.append((mode, var))
        if mode == cc.PIT_CLEAR:
            self.flags = 0
        elif mode == cc.PIT_CLEAR_TIRES:
            self.flags &= ~0x0F
        elif mode in (cc.PIT_LF, cc.PIT_RF, cc.PIT_LR, cc.PIT_RR):
            self.flags |= 1 << (mode - cc.PIT_LF)
        elif mode == cc.PIT_FUEL:
            self.flags |= cc.FLAG_FUEL
            if var:
                self.fuel = var
        elif mode == cc.PIT_FR:
            self.flags |= cc.FLAG_FR
        elif mode == cc.PIT_CLEAR_FR:
            self.flags &= ~cc.FLAG_FR
        elif mode == cc.PIT_CLEAR_FUEL:
            self.flags &= ~cc.FLAG_FUEL
        elif mode == cc.PIT_CLEAR_WS:
            self.flags &= ~cc.FLAG_WS


class ConnectionTests(unittest.TestCase):
    def test_new_controller_connects_its_own_sdk_without_sending(self):
        sdk = GameSdk()
        with patch.dict('sys.modules', {'irsdk': types.SimpleNamespace(IRSDK=lambda: sdk)}):
            c = CarController({'car_control': {'enabled': True}})
        self.assertTrue(c.available)
        self.assertEqual(sdk.starts, 1)
        self.assertEqual(sdk.calls, [])
        self.assertTrue(c.available)
        self.assertEqual(sdk.starts, 1)
        c.close()
        c.close()
        self.assertEqual(sdk.stops, 1)
        self.assertFalse(c.available)

    def test_disconnect_reconnects_without_replaying_commands(self):
        sdk = GameSdk(connected=True)
        c = CarController({'car_control': {'enabled': True}}, sdk=sdk)
        sdk.is_connected = False
        self.assertTrue(c.available)
        self.assertEqual(sdk.starts, 1)
        self.assertEqual(sdk.calls, [])

    def test_absent_game_never_receives_broadcast(self):
        sdk = GameSdk()
        sdk.game_running = False
        c = CarController({'car_control': {'enabled': True}}, sdk=sdk)
        r = c.apply({'kind': 'fast_repair'})
        self.assertFalse(r['ok'])
        self.assertEqual(sdk.calls, [])
        self.assertFalse(c.available)
        self.assertEqual(sdk.starts, 1)  # disconnected status polling is throttled


class ExactServiceTests(unittest.TestCase):
    def setUp(self):
        self.sdk = GameSdk(connected=True)
        self.c = CarController({'car_control': {'enabled': True}}, sdk=self.sdk)

    def test_left_only_replaces_default_four_tires_preserving_other_services(self):
        r = self.c.apply({'kind': 'tyre', 'corners': ['LF', 'LR']})
        self.assertTrue(r['ok'])
        self.assertEqual(self.sdk.flags, 0x75)
        self.assertEqual(self.sdk.calls, [(7, 0), (3, 0), (5, 0)])

    def test_extra_right_tire_is_not_a_successful_left_only_verification(self):
        with patch('src.car_control._VERIFY_TIMEOUT_S', 0):
            r = self.c.verify_applied({'kind': 'tyre', 'corners': ['LF', 'LR']}, lambda: 0x7F)
        self.assertFalse(r['verified'])

    def test_cancel_actions_require_bits_to_clear_and_preserve_unrelated_bits(self):
        for kind, flags in [('clear', 0), ('clear_tires', 0x70),
                            ('clear_fr', 0x3F), ('clear_fuel', 0x6F), ('clear_ws', 0x5F)]:
            with self.subTest(kind=kind):
                r = self.c.verify_applied({'kind': kind}, lambda: flags)
                self.assertTrue(r['verified'])
                with patch('src.car_control._VERIFY_TIMEOUT_S', 0):
                    self.assertFalse(self.c.verify_applied({'kind': kind}, lambda: 0x7F)['verified'])

    def test_missing_readback_cannot_confirm_success(self):
        r = self.c.verify_applied({'kind': 'clear_fr'}, lambda: None)
        self.assertIsNot(r['verified'], True)

    def test_existing_fuel_flag_does_not_confirm_wrong_target_litres(self):
        with patch('src.car_control._VERIFY_TIMEOUT_S', 0):
            r = self.c.verify_applied({'kind': 'fuel', 'litres': 10},
                                     lambda: 0x7F, read_fuel=lambda: 100)
        self.assertFalse(r['verified'])
        self.assertTrue(self.c.verify_applied({'kind': 'fuel', 'litres': 10},
                                             lambda: 0x7F, read_fuel=lambda: 10)['verified'])

    def test_fractional_fuel_never_silently_rounds_or_sends(self):
        for litres in (1.5, 0.5, -1, 0, float('nan'), float('inf')):
            with self.subTest(litres=litres):
                self.assertFalse(self.c.apply({'kind': 'fuel', 'litres': litres})['ok'])
        self.assertEqual(self.sdk.calls, [])


class RaceSequenceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = make_cfg(pathlib.Path(tmp.name).resolve(), True)
        with patch('src.engine.VoicePack.load_many', return_value=fake_pack(
                {'AST_CC_TYRE', 'AST_CC_FUEL', 'AST_CC_CLEAR', 'AST_CC_FR', 'AST_CC_FAIL',
                 'AST_AI_SEE_SCREEN'})):
            self.a = Assistant(cfg, no_play=True, out=io.StringIO())
        self.addCleanup(self.a.close)
        self.sdk = GameSdk(connected=True)
        self.a.car.sdk = self.sdk
        self.a.matcher = MagicMock()
        self.a.matcher.match.return_value = MagicMock(intent=None, reason='未命中', ranked=[])
        self.a.snapshot = lambda: types.SimpleNamespace(pit_sv_flags=self.sdk.flags,
                                                       pit_sv_fuel=self.sdk.fuel)

    def test_exact_five_utterances_change_default_services_and_confirm_in_order(self):
        cases = [('帮我取消换胎', 0x70, 'AST_CC_CLEAR'),
                 ('我马上要进站，帮我换左侧轮胎', 0x75, 'AST_CC_TYRE'),
                 ('我要进站了，帮我加10升油', 0x75, 'AST_CC_FUEL'),
                 ('我要进站了，帮我取消快修', 0x35, 'AST_CC_CLEAR'),
                 ('我要进站了，帮我勾上快修', 0x75, 'AST_CC_FR')]
        with patch.object(self.a, '_say') as say:
            for q, flags, msg in cases:
                with self.subTest(q=q):
                    r = self.a.ask(q)
                    self.assertTrue(r['ok'])
                    self.assertEqual(self.sdk.flags, flags)
                    self.assertTrue(r['cc_verified']['verified'])
                    self.assertEqual(say.call_args.args[0], msg)
        self.assertEqual(self.sdk.fuel, 10)

    def test_unknown_verification_does_not_play_success(self):
        self.a.snapshot = lambda: types.SimpleNamespace(pit_sv_flags=None)
        with patch.object(self.a, '_say') as say:
            r = self.a.ask('帮我取消换胎')
        self.assertFalse(r['ok'])
        self.assertEqual(say.call_args.args[0], 'AST_CC_FAIL')

    def test_spoken_ai_answer_does_not_append_see_screen_prompt(self):
        from src.ai_pipeline import AiResult
        from src.query_class import QueryKind
        self.a.ai = MagicMock()
        self.a.ai.handle.return_value = AiResult(ok=True, text='当前没有足够的圈记录。',
                                                 checks={'tts': 'ok'})
        with patch.object(self.a, '_say') as say:
            result = self.a.ask_ai('我为什么在变慢', QueryKind.ANALYSIS, None, False)
        self.assertTrue(result['ok'])
        say.assert_not_called()


class TextAiRouteTests(unittest.TestCase):
    def service(self, eligible=True):
        svc = Service.__new__(Service)
        svc.lock = threading.RLock()
        svc.log = MagicMock()
        svc.last_ask = None
        svc.asst = MagicMock()
        svc.asst.explain.return_value = {'text': '我为什么在变慢', 'intent': None}
        svc.asst.ask.return_value = {'q': '我为什么在变慢', 'kind': 'analysis', 'ai_eligible': eligible}
        svc.asst.ask_ai.return_value = {'q': '我为什么在变慢', 'kind': 'analysis', 'ok': True, 'ai_text': '当前没有足够的圈记录。'}
        return svc

    def test_audition_returns_final_ai_answer_after_complete_call(self):
        svc = self.service()
        r = svc.ask('我为什么在变慢', play=True)
        svc.asst.ask_ai.assert_called_once()
        self.assertEqual(r['playback']['ai_text'], '当前没有足够的圈记录。')
        self.assertEqual(svc.last_ask, r['playback'])

    def test_rule_answer_does_not_call_model(self):
        svc = self.service(eligible=False)
        svc.ask('油还够吗', play=True)
        svc.asst.ask_ai.assert_not_called()

    def test_preview_does_not_call_model_or_execute_commands(self):
        svc = self.service()
        svc.asst.peek_car_command.return_value = None
        svc.ask('我为什么在变慢', play=False)
        svc.asst.ask.assert_not_called()
        svc.asst.ask_ai.assert_not_called()

    def test_failed_ai_handoff_logs_without_secondary_exception(self):
        svc = self.service()
        svc.asst.ask_ai.side_effect = RuntimeError('offline')
        svc.ask('我为什么在变慢', play=True)
        svc.log.write.assert_called_once()


class SpokenAnswerTests(unittest.TestCase):
    def test_learned_answer_still_uses_configured_tts_and_player(self):
        from src.ai_pipeline import AiPipeline, AiResult
        from src.query_class import QueryKind
        player = MagicMock(enabled=True)
        pipe = AiPipeline({'ai': {'mode': 'full'}}, out=io.StringIO(), player=player)
        pipe.tts = MagicMock()
        pipe.tts.synth.return_value = b'wav'
        learned = AiResult(ok=True, text='这是已复盘的知识。', source='learn')
        with patch.object(pipe, '_from_learn', return_value=learned):
            result = pipe.handle('帮我分析', None, QueryKind.ANALYSIS, [])
        player.play_bytes.assert_called_once_with(b'wav')
        self.assertEqual(result.checks['tts'], 'ok')

    def test_disabled_player_is_not_reported_as_successful_tts_playback(self):
        from src.ai_pipeline import AiPipeline, AiResult
        pipe = AiPipeline({'ai': {}}, out=io.StringIO(), player=MagicMock(enabled=False))
        pipe.tts = MagicMock()
        result = AiResult(ok=True, text='文字仍可用。')
        pipe._maybe_speak(result.text, result)
        pipe.tts.synth.assert_not_called()
        self.assertEqual(result.checks['tts'], 'disabled')


class TimeQuestionTests(unittest.TestCase):
    def test_remaining_time_question_is_not_confused_with_repair_time(self):
        from src.phrase_match import PhraseMatcher
        matcher = PhraseMatcher()
        for q in ('还剩多久', '还剩多少时间', '剩余时间多少', '还有多少时间'):
            with self.subTest(q=q):
                self.assertEqual(matcher.match(q).intent, 'query_laps')
        self.assertEqual(matcher.match('必修还剩多久').intent, 'query_pit')


if __name__ == '__main__':
    unittest.main()
