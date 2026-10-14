import unittest
from src.phrase_match import PhraseMatcher,normalize
from src import router,telemetry,config,numbers
from src.voicepack import VoicePack

class DriverQuestionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher=PhraseMatcher()
        cfg=config.load()
        cls.pack=VoicePack.load_many([config.resolve(cfg,cfg['voice_pack'][key]) for key in ('dir','extra_dir','telemetry_dir')])
    def ask(self,q,s=None):
        hit=self.matcher.match(q)
        self.assertIsNotNone(hit.intent,q)
        return router.route(hit.intent,s or telemetry.SimTelemetry().read(),q)
    def test_actual_missed_questions(self):
        cases={
            '当前第一名的全速是什么':'AST2_LEADER_LAST_LAP_LEAD',
            '目前还有多少油':'AST2_FUEL_LEAD',
            '还有多少油':'AST2_FUEL_LEAD',
            '目前剩余有量读数时':'AST2_FUEL_LEAD',
            '目前的剩余有量是':'AST2_FUEL_LEAD',
            '目前的降雨强度是':'AST2_PRECIP_LEAD',
            '这场的最佳圈速是':'AST2_BEST_LAP_LEAD',
            '这场的最最佳圈数是什么':'AST2_BEST_LAP_LEAD',
            '第一名的圈数是':'AST2_LEADER_LAPS_LEAD',
            '第一名什么圈数':'AST2_LEADER_LAPS_LEAD',
            '我们现在距离前面的车多远':'AST_GAP_AHEAD_LEAD',
        }
        for q,first in cases.items():
            with self.subTest(q=q):self.assertEqual(self.ask(q).plan[0],first)
    def test_damage_is_recognized_but_not_invented(self):
        q='车有没有坏的地方'
        self.assertEqual(self.matcher.match(q).intent,'query_damage')
        self.assertFalse(self.ask(q).ok)
    def test_polite_wrappers_preserve_subject(self):
        for q in ('请问目前还有多少油啊谢谢','麻烦你帮我看看后车最佳圈速是多少呢','小小领航员报一下第一名圈速是多少'):
            with self.subTest(q=q):self.assertTrue(self.ask(q).ok)
    def test_driver_paraphrases_read_the_right_metric(self):
        cases={'还能撑多少圈':'AST2_FUEL_LAPS_LEAD','我已经跑完几圈':'AST2_LAPS_COMPLETED_LEAD','ABS现在几档':'AST2_ABS_LEAD','TC现在几档':'AST2_TC_LEAD','第一名的最快圈速是多少':'AST2_LEADER_BEST_LAP_LEAD','我现在比最快圈慢几秒':'AST2_LAP_LOSS_LEAD'}
        for q,prefix in cases.items():
            with self.subTest(q=q):self.assertEqual(self.ask(q).plan[0],prefix)
    def test_pace_difference_is_not_gap(self):
        self.assertFalse(self.ask('前面那辆车比我快多少').ok)
    def test_current_opponent_lap_is_not_last_completed_lap(self):
        self.assertFalse(router.route('query_opponent',telemetry.SimTelemetry().read(),'第一名这圈圈速多少').ok)
    def test_leader_uses_correct_index_and_lap_kind(self):
        s=telemetry.Snapshot(connected=True,car_positions=[3,2,1],car_last_laps=[99,88,77.123],car_best_laps=[98,87,76.234],lap_last=66,lap_best=65)
        for q,value in [('第一名圈速多少',77.123),('第一名最佳圈速多少',76.234)]:
            with self.subTest(q=q):self.assertEqual(self.ask(q,s).plan[1:],numbers.laptime_to_msgids(value))
    def test_neighbors_are_not_assumed_to_be_rank_neighbors(self):
        s=telemetry.SimTelemetry(gap_ahead_idx=4,gap_behind_idx=2).read()
        self.assertEqual(self.ask('前车圈速多少',s).plan[1:],numbers.laptime_to_msgids(83.8))
        self.assertEqual(self.ask('后车最佳圈速多少',s).plan[1:],numbers.laptime_to_msgids(83.0))
    def test_invalid_opponent_data_refuses(self):
        for values in (None,[],[-1],[0],[float('nan')],[float('inf')]):
            with self.subTest(values=values):
                s=telemetry.Snapshot(connected=True,car_positions=[1],car_last_laps=values,lap_last=90)
                self.assertFalse(self.ask('第一名圈速多少',s).ok)
        for positions in (None,[],[2,3],[1,1]):
            with self.subTest(positions=positions):
                s=telemetry.Snapshot(connected=True,car_positions=positions,car_last_laps=[80,90])
                self.assertFalse(self.ask('第一名圈速多少',s).ok)
    def test_ambiguous_or_unsupported_targets_refuse(self):
        s=telemetry.SimTelemetry().read()
        for q in ('前车和后车圈速多少','同组第一名圈速多少','第二名圈速多少'):
            with self.subTest(q=q):self.assertFalse(router.route('query_opponent',s,q).ok)
        self.assertFalse(router.route('query_laptime',s,'第一名这圈圈速多少').ok)
    def test_safety_car_is_not_leader(self):
        s=telemetry.Snapshot(connected=True,car_positions=[1],car_last_laps=[80],pace_car_indices={0})
        self.assertFalse(self.ask('第一名圈速多少',s).ok)
    def test_no_connection_no_numeric_answer(self):
        self.assertFalse(self.ask('还有多少油',telemetry.Snapshot(connected=False,fuel_level=20)).ok)
    def test_numbers_are_not_collapsed(self):
        self.assertEqual(normalize('第11名100秒'),'第11名100秒')
    def test_every_phrase_exact_intent_and_all_plans_have_audio(self):
        s=telemetry.SimTelemetry().read()
        for phrase in self.matcher.phrases:
            with self.subTest(q=phrase.text):
                self.assertEqual(self.matcher.match(phrase.text).intent,phrase.intent)
                d=router.route(phrase.intent,s,phrase.text)
                for mid in d.plan:self.assertTrue(self.pack.has(mid),mid)

if __name__=='__main__':unittest.main()
