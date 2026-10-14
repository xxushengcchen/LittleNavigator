"""离线回归：以可复现输入检查真实比赛中不能猜测的边界。"""
import contextlib
import io
import json
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import yaml

from src import config, numbers, router, telemetry
from src.engine import Assistant
from src.listener import PttListener, capture_key, input_format
from src.player import Player, _read_wav
from src.voicepack import VoicePack, Variant, VariantPicker
from src.phrase_match import PhraseMatcher
from app.server import Service, Handler, Server


class RouteTests(unittest.TestCase):
    def s(self, **kw):
        return telemetry.Snapshot(connected=True, **kw)

    def test_disconnect_refuses_all(self):
        for intent in router._ROUTES:
            with self.subTest(intent=intent):
                self.assertFalse(router.route(intent, telemetry.Snapshot()).ok)

    def test_incident_count_is_not_damage(self):
        for count in (0, 1, 5, 25, 100):
            self.assertFalse(router.route('query_damage', self.s(incident_count=count)).ok)

    def test_repair_uses_repair_flag(self):
        self.assertEqual(router.route('query_damage', self.s(session_flags=0x100000)).plan, ['SPCC_REPAIR'])

    def test_tyre_data_not_invented(self):
        self.assertFalse(router.route('query_tyre', self.s(incident_count=9), '轮胎怎么样').ok)

    def test_pit_without_data_refuses(self):
        self.assertFalse(router.route('query_pit', self.s()).ok)

    def test_pit_already_inside_not_enter_instruction(self):
        self.assertEqual(router.route('query_pit', self.s(on_pit_road=True)).plan,['AST2_IN_PIT'])

    def test_fuel_without_finish_distance_refuses(self):
        plan=router.route('query_fuel', self.s(fuel_level=40, laps_of_fuel=30)).plan
        self.assertEqual(plan[0],'AST2_FUEL_LAPS_LEAD')
        self.assertFalse(any(mid.startswith('SPCC_FUEL_RACE') for mid in plan))

    def test_fuel_numeric_not_replaced_with_enough(self):
        plan=router.route('query_fuel', self.s(fuel_level=40, laps_of_fuel=30, laps_remain=10), '油还剩多少').plan
        self.assertEqual(plan[0],'AST2_FUEL_LEAD');self.assertIn('SPCC_N40',plan)

    def test_fuel_valid_enough(self):
        self.assertEqual(router.route('query_fuel', self.s(fuel_level=40, laps_of_fuel=30, laps_remain=10)).plan, ['SPCC_FUEL_RACE1'])

    def test_front_gap_not_rear(self):
        s = self.s(gap_ahead_s=4.8, gap_behind_s=1.2)
        plan = router.route('query_gap', s, '前车多远').plan
        self.assertEqual(plan[0], 'AST_GAP_AHEAD_LEAD')
        self.assertIn('SPCC_N4', plan)

    def test_rear_gap_without_trend_still_answers_number(self):
        self.assertEqual(router.route('query_gap', self.s(gap_behind_s=1.2), '后车多远').plan[0], 'AST_GAP_BEHIND_LEAD')

    def test_no_front_data_cannot_substitute_rear(self):
        self.assertFalse(router.route('query_gap', self.s(gap_behind_s=1.2), '前车多远').ok)

    def test_stable_gap_not_pulling_away(self):
        self.assertEqual(router.route('query_gap', self.s(gap_behind_s=1.2, gap_behind_trend=0), '后车在追吗').plan,['AST2_GAP_STABLE'])

    def test_unknown_wetness_not_dry(self):
        self.assertFalse(router.route('query_weather', self.s(track_wetness=0)).ok)

    def test_mostly_dry_is_dry(self):
        self.assertEqual(router.route('query_weather', self.s(track_wetness=2)).plan, ['SPCC_SESSIONDRY'])

    def test_weather_temperature_not_dry_answer(self):
        plan=router.route('query_weather', self.s(track_wetness=1, air_temp=28), '气温多少').plan
        self.assertEqual(plan[0],'AST2_AIR_TEMP_LEAD');self.assertIn('SPCC_N28',plan)

    def test_weather_forecast_not_present_answer(self):
        self.assertFalse(router.route('query_weather', self.s(track_wetness=1), '什么时候会下雨').ok)

    def test_yellow_waving_overrides_green(self):
        self.assertEqual(router.route('query_flags', self.s(session_flags=0x104)).plan, ['SPCC_YELOUT'])

    def test_dq_overrides_green(self):
        self.assertEqual(router.route('query_flags', self.s(session_flags=0x20004)).plan, ['SPCC_DQ'])

    def test_zero_laptime_refuses(self):
        self.assertFalse(router.route('query_laptime', self.s(lap_last=0), '上圈多少').ok)

    def test_best_lap_not_last_lap(self):
        plan=router.route('query_laptime', self.s(lap_last=90, lap_best=80), '最快圈多少').plan
        self.assertEqual(plan[0],'AST2_BEST_LAP_LEAD');self.assertIn('SPCC_N20',plan);self.assertNotIn('SPCC_N30',plan)

    def test_laptime_does_not_invent_reason(self):
        self.assertFalse(router.route('query_laptime', self.s(lap_last=90,lap_best=80), '什么原因慢了').ok)

    def test_other_car_laptime_not_own_lap(self):
        self.assertFalse(router.route('query_laptime', self.s(lap_last=90), '前车圈速多少').ok)

    def test_unlimited_laps_refuses(self):
        self.assertFalse(router.route('query_laps', self.s(laps_remain=32767)).ok)

    def test_position_zero_refuses(self):
        self.assertFalse(router.route('query_position', self.s(position=0)).ok)

    def test_nonfinite_laptime_refuses(self):
        self.assertFalse(router.route('query_laptime', self.s(lap_last=float('nan')), '上圈多少').ok)


class TelemetryTests(unittest.TestCase):
    def test_missing_sdk_does_not_fallback_to_sim(self):
        with patch.dict('sys.modules', {'irsdk': None}):
            provider, warning = telemetry.build(config.DEFAULTS)
        self.assertIsInstance(provider, telemetry.IrsdkTelemetry)
        self.assertFalse(provider.read().connected)
        self.assertTrue(warning)

    def test_fuel_requires_full_observed_lap(self):
        t = telemetry.DerivedTracker()
        def feed(lap, fuel):
            return t.update(telemetry.Snapshot(connected=True, lap=lap, fuel_level=fuel))
        feed(1, 50)
        self.assertIsNone(feed(2, 49).fuel_per_lap)  # 第一圈起点不完整。
        self.assertEqual(feed(3, 47).fuel_per_lap, 2)

    def test_refuel_during_lap_excluded(self):
        t = telemetry.DerivedTracker()
        for lap, fuel in [(1,50),(2,48),(2,45),(2,47),(3,46)]:
            s = t.update(telemetry.Snapshot(connected=True, lap=lap, fuel_level=fuel))
        self.assertIsNone(s.fuel_per_lap)

    def test_new_session_clears_fuel_history(self):
        t = telemetry.DerivedTracker()
        for lap, fuel in [(1,50),(2,48),(3,46)]:
            t.update(telemetry.Snapshot(connected=True, session_id=(1,), lap=lap, fuel_level=fuel))
        self.assertTrue(t.fuel_per_lap_samples)
        s = t.update(telemetry.Snapshot(connected=True, session_id=(2,), lap=1, fuel_level=50))
        self.assertIsNone(s.fuel_per_lap)

    def test_skipped_laps_not_single_lap_consumption(self):
        t = telemetry.DerivedTracker()
        for lap, fuel in [(1,50),(2,48),(4,44)]:
            s = t.update(telemetry.Snapshot(connected=True, lap=lap, fuel_level=fuel))
        self.assertIsNone(s.fuel_per_lap)

    def test_missing_sdk_fields_and_unfreeze(self):
        class FakeSdk:
            is_initialized = is_connected = True
            freeze = unfreeze = 0
            def __getitem__(self, name):
                if name == 'FuelLevel': return 30
                if name == 'SessionLapsRemainEx': return 32767
                if name == 'LapLastLapTime': return -1
                raise KeyError(name)
            def freeze_var_buffer_latest(self): self.freeze += 1
            def unfreeze_var_buffer_latest(self): self.unfreeze += 1
        p = telemetry.IrsdkTelemetry()
        p._ir = FakeSdk()
        s = p.read()
        self.assertTrue(s.connected)
        self.assertEqual(s.fuel_level, 30)
        self.assertIsNone(s.laps_remain)
        self.assertIsNone(s.lap_last)
        self.assertEqual(p._ir.freeze, p._ir.unfreeze)

    def test_sampling_independent_of_ui(self):
        class Provider:
            count = 0
            def read(self):
                self.count += 1
                return telemetry.Snapshot(connected=True)
        p = Provider()
        sampler = telemetry.TelemetrySampler(p, telemetry.DerivedTracker(), 120)
        time.sleep(.04)
        sampler.close()
        self.assertGreater(p.count, 2)
        self.assertFalse(sampler._thread.is_alive())


class NumberAndAudioTests(unittest.TestCase):
    def test_integer_out_of_range(self):
        for number in (-1, 10000, 1.5, True):
            with self.assertRaises(ValueError): numbers.int_to_msgids(number)

    def test_decimal_carry(self):
        self.assertEqual(numbers.decimal_to_msgids(1.99), ['SPCC_N2', 'SPCC_POINT', 'SPCC_N0'])

    def test_laptime_carry_no_double_zero(self):
        self.assertEqual(numbers.laptime_to_msgids(59.99), ['SPCC_N1','SPCC_MINUTE','SPCC_N0','SPCC_SECOND'])

    def test_fraction_digits_read_separately(self):
        self.assertEqual(numbers.laptime_to_msgids(83.05,2)[-2:], ['SPCC_N0','SPCC_N5'])

    def test_8bit_fallback_audio(self):
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / 'audio.wav'
            with wave.open(str(path),'wb') as w:
                w.setnchannels(1); w.setsampwidth(1); w.setframerate(5512); w.writeframes(bytes([0,128,255]))
            data, sr = _read_wav(path)
            self.assertEqual(sr,5512)
            self.assertEqual(data[1],0)
            self.assertEqual(data[0],-1)

    def test_two_files_one_audio_submission_and_duck(self):
        class Audio:
            calls = 0
            def play(self,*a,**kw): self.calls += 1
        class Duck:
            ducked = restored = 0
            def duck(self): self.ducked += 1
            def restore(self): self.restored += 1
        player = Player()
        player._sd, player.ducker = Audio(), Duck()
        with patch('src.player._read_wav', return_value=(np.zeros(100,dtype=np.float32),16000)):
            player.play_many(['one','two'])
        self.assertEqual(player._sd.calls,1)
        self.assertEqual(player.ducker.ducked,1)
        self.assertEqual(player.ducker.restored,1)


class ListenerTests(unittest.TestCase):
    def test_one_held_key_only_records_once(self):
        finished = threading.Event(); calls=[]
        asr=SimpleNamespace(transcribe=lambda *a: SimpleNamespace(text='还剩几圈'))
        listener=PttListener(asr,config.DEFAULTS,lambda *a:(calls.append(a),finished.set()),out=io.StringIO())
        down=threading.Event()
        with patch('src.listener._key_down',side_effect=lambda vk:down.is_set()), patch.object(listener,'_record_while',return_value=np.ones(5000,dtype=np.float32)):
            listener.start(); time.sleep(.03); down.set()
            self.assertTrue(finished.wait(1)); time.sleep(.02); listener.stop()
        self.assertEqual(len(calls),1)
    def test_initially_held_binding_waits_for_release(self):
        listener=PttListener(None,config.DEFAULTS,lambda *a:None,out=io.StringIO())
        with patch('src.listener._key_down',return_value=True), patch.object(listener,'_record_while') as record:
            listener.start(); time.sleep(.03); listener.stop()
            record.assert_not_called()
    def test_native_rate_fallback(self):
        import sounddevice as sd
        def settings(**kw):
            if kw['samplerate']==16000: raise sd.PortAudioError('unsupported sample rate')
        with patch('src.listener.sd.query_devices',return_value={'default_samplerate':48000,'max_input_channels':2}), patch('src.listener.sd.check_input_settings',side_effect=settings):
            self.assertEqual(input_format(20),(48000,1))
    def test_stutter_damage_question_not_laptime(self):
        self.assertEqual(PhraseMatcher().match('还能开开吗').intent,'query_damage')
    def test_stop_during_record_does_not_transcribe(self):
        calls = []
        asr = SimpleNamespace(transcribe=lambda *a: calls.append(a))
        listener = PttListener(asr, config.DEFAULTS, lambda *a: None, out=io.StringIO())
        def record(*a):
            listener._stop.set()
            return np.ones(16000,dtype=np.float32)
        down=threading.Event(); entered=threading.Event()
        def observed_record(*a):
            entered.set(); return record(*a)
        with patch('src.listener._key_down', side_effect=lambda vk:down.is_set()), patch.object(listener,'_record_while', side_effect=observed_record):
            listener.start(); time.sleep(.03); down.set(); self.assertTrue(entered.wait(1)); self.assertTrue(listener.stop())
        self.assertFalse(calls)

    def test_invalid_float_audio_rejected(self):
        listener = PttListener(None, config.DEFAULTS, lambda *a: None)
        with self.assertRaises(ValueError): listener._process(np.full(16000,np.nan))

    def test_held_click_not_captured(self):
        with patch('src.listener._key_down',side_effect=lambda vk: vk==1):
            self.assertIsNone(capture_key(timeout_s=.02,poll_s=.001))


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = config.load()
        base['voice_pack']['dir'] = str(config.resolve(base,base['voice_pack']['dir']))
        base['voice_pack']['extra_dir'] = str(config.resolve(base,base['voice_pack']['extra_dir']))
        base['voice_pack']['telemetry_dir'] = str(config.resolve(base,base['voice_pack']['telemetry_dir']))
        base['playback']['enabled'] = False
        cls.path = pathlib.Path(cls.temp.name)/'config.yaml'
        cls.path.write_text(yaml.safe_dump(base),encoding='utf-8')
        cls.service = Service(str(cls.path),sim=True)

    @classmethod
    def tearDownClass(cls):
        cls.service.close(); cls.temp.cleanup()

    def test_relative_paths_use_config_directory(self):
        self.assertEqual(config.resolve(config.load(self.path),'logs').parent,self.path.parent.resolve())

    def test_deep_config_merge_preserves_duck_fields(self):
        svc = self.service
        svc.configure({'playback':{'ducking':{'amount_db':-18}}})
        svc.configure({'playback':{'ducking':{'enabled':False}}})
        self.assertEqual(svc.cfg['playback']['ducking']['amount_db'],-18)
        self.assertEqual(svc.asst.ducker.amount_db,-18)

    def test_duck_enable_after_disabled_boot(self):
        self.service.configure({'playback':{'ducking':{'enabled':True}}})
        self.assertTrue(self.service.asst.ducker.enabled)

    def test_invalid_config_not_saved(self):
        local = self.path.parent/'config.local.yaml'
        before = local.read_bytes() if local.exists() else None
        for patch_ in [{'playback':{'volume':-1}}, {'asr':{'num_threads':1.2}}, {'ptt':{'vk':0}}, {'intent':{'l1_keyword':{'enabled':'yes'}}}]:
            with self.assertRaises(ValueError): self.service.configure(patch_)
        self.assertEqual(local.read_bytes() if local.exists() else None,before)

    def test_api_cannot_redirect_log_paths(self):
        with self.assertRaises(ValueError): self.service.configure({'logging':{'qa_log':'elsewhere'}})

    def test_missing_optional_model_not_saved(self):
        with self.assertRaises(ValueError): self.service.configure({'asr':{'model':'streaming-zip'}})

    def test_asr_change_clears_old_instance(self):
        self.service.asr = object()
        self.service.configure({'asr':{'num_threads':2}})
        self.assertIsNone(self.service.asr)

    def test_all_answers_and_refusals_in_qa(self):
        before = len(self.service._tail_jsonl(self.service.qa_log,1000))
        self.service.asst.ask('还剩几圈')
        self.service.asst.ask('今天晚饭吃什么',source='voice')
        self.assertEqual(len(self.service._tail_jsonl(self.service.qa_log,1000)),before+2)
        self.assertFalse(self.service.asst.history[-1]['ok'])

    def test_playing_debug_uses_normal_qa_pipeline(self):
        rec = self.service.ask('还剩几圈',play=True)['playback']
        self.assertTrue(rec['ok'])
        self.assertEqual(rec['segments'],3)

    def test_preview_does_not_change_cooldown(self):
        recent = json.dumps(self.service.asst.picker.recent,sort_keys=True)
        self.service.ask('还剩几圈')
        self.assertEqual(json.dumps(self.service.asst.picker.recent,sort_keys=True),recent)

    def test_preview_busy_guard_and_serial_playback(self):
        active = []; maximum = [0]
        def fake_play(*a,**kw):
            self.assertTrue(self.service.asst.busy)
            active.append(1); maximum[0] = max(maximum[0],len(active)); time.sleep(.01); active.pop()
        with patch.object(self.service.asst.player,'play',side_effect=fake_play):
            threads = [threading.Thread(target=self.service.asst.play_msgid,args=('SPCC_YELOUT',)) for _ in range(3)]
            for t in threads:t.start()
            for t in threads:t.join()
        self.assertEqual(maximum[0],1)
        self.assertFalse(self.service.asst.busy)

    def test_http_guards_and_bad_body(self):
        class BoundHandler(Handler): pass
        BoundHandler.service = self.service
        server = Server(('127.0.0.1',0),BoundHandler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        def request(path, data=None, headers=None):
            req = urllib.request.Request(url+path,data=data,headers=headers or {})
            try:
                with urllib.request.urlopen(req) as r: return r.status,r.read()
            except urllib.error.HTTPError as exc:return exc.code,exc.read()
        try:
            status,html = request('/')
            self.assertEqual(status,200)
            self.assertIn(self.service.api_token.encode(),html)
            self.assertEqual(request('/api/state')[0],200)
            self.assertEqual(request('/api/quit',b'{}')[0],403)
            headers = {'X-Assistant-Token':self.service.api_token,'Content-Type':'application/json'}
            self.assertEqual(request('/api/config',b'[]',headers)[0],400)
            self.assertEqual(request('/api/config',b'{',headers)[0],400)
            self.assertEqual(request('/api/ask',json.dumps({'text':'x'*513}).encode(),headers)[0],400)
            self.assertEqual(request('/api/ask',b'{}',{**headers,'Origin':'https://other.example'})[0],403)
            self.assertEqual(request('/static/%2e%2e/config.yaml')[0],403)
            self.assertEqual(request('/api/state',headers={'Host':'attacker.example'})[0],403)
            self.assertEqual(request('/api/ask',json.dumps({'text':'还剩几圈'}).encode(),headers)[0],200)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
