import json,pathlib,threading,time,unittest
from unittest.mock import patch
from src import config,telemetry,router,numbers
from src.wheel import WheelInput

class ExpansionTests(unittest.TestCase):
    def s(self,**kw):return telemetry.Snapshot(connected=True,**kw)
    def test_all_response_families_have_six_unique_forms(self):
        path=pathlib.Path(__file__).resolve().parents[2]/'content/assistant-telemetry-v1.1.json'
        for item in json.loads(path.read_text(encoding='utf-8'))['items']:
            if not item['msgId'].startswith('AST2_UNIT_'):self.assertEqual(len(set(item['variants'])),6)
    def test_negative_temperature_has_minus(self):
        plan=router.route('query_weather',self.s(air_temp=-4),'气温多少').plan
        self.assertIn('AST2_UNIT_MINUS',plan);self.assertIn('SPCC_N4',plan)
    def test_wind_has_meters_per_second(self):
        plan=router.route('query_weather',self.s(wind_vel=3.2),'风速多少').plan
        self.assertEqual(plan[-1],'AST2_UNIT_MPS')
    def test_humidity_fraction_converted(self):
        plan=router.route('query_weather',self.s(relative_humidity=.62),'湿度多少').plan
        self.assertIn('AST2_UNIT_PERCENT',plan);self.assertIn('SPCC_N62',plan)
    def test_unknown_humidity_refuses(self):
        self.assertFalse(router.route('query_weather',self.s(),'湿度多少').ok)
    def test_car_speed_has_kmh_unit(self):
        self.assertEqual(router.route('query_speed',self.s(speed_kph=187.2)).plan[-1],'AST2_UNIT_KMH')
    def test_speed_missing_refuses(self):self.assertFalse(router.route('query_speed',self.s()).ok)
    def test_rpm_reading(self):self.assertEqual(router.route('query_engine',self.s(rpm=7120),'转速多少').plan[-1],'AST2_UNIT_RPM')
    def test_high_fractional_rpm(self):self.assertIn('AST2_UNIT_TEN_THOUSAND',router.route('query_engine',self.s(rpm=12400.3),'转速多少').plan)
    def test_long_remaining_time_does_not_overflow(self):self.assertTrue(router.route('query_laps',self.s(time_remain_s=604790),'还剩多久').ok)
    def test_voltage_reading(self):self.assertEqual(router.route('query_engine',self.s(voltage=13.2),'电压多少').plan[-1],'AST2_UNIT_VOLT')
    def test_gear_neutral(self):self.assertEqual(router.route('query_engine',self.s(gear=0),'现在几挡').plan,['AST2_GEAR_NEUTRAL'])
    def test_gear_reverse(self):self.assertEqual(router.route('query_engine',self.s(gear=-1),'现在几挡').plan,['AST2_GEAR_REVERSE'])
    def test_gear_forward(self):self.assertIn('SPCC_N6',router.route('query_engine',self.s(gear=6),'现在几挡').plan)
    def test_gear_missing(self):self.assertFalse(router.route('query_engine',self.s(),'现在几挡').ok)
    def test_fuel_use_does_not_use_kg_per_hour(self):
        self.assertFalse(router.route('query_fuel',self.s(fuel_use_per_hour=80),'每圈油耗多少').ok)
    def test_fuel_estimated_time_requires_lap(self):
        self.assertFalse(router.route('query_fuel',self.s(laps_of_fuel=10),'油还能跑多久').ok)
    def test_fuel_estimated_time_matches_numeric(self):
        plan=router.route('query_fuel',self.s(laps_of_fuel=10,lap_last=90),'油还能跑多久').plan
        self.assertIn('SPCC_N15',plan);self.assertIn('SPCC_MINUTE',plan)
    def test_completed_laps_not_remaining(self):
        plan=router.route('query_laps',self.s(lap_completed=12,laps_remain=8),'已经完成几圈').plan
        self.assertEqual(plan[0],'AST2_LAPS_COMPLETED_LEAD');self.assertIn('SPCC_N12',plan)
    def test_remaining_time_uses_minutes(self):
        plan=router.route('query_laps',self.s(time_remain_s=125),'比赛还剩多久').plan
        self.assertEqual(plan[1:],['SPCC_N2','SPCC_MINUTE','SPCC_N5','SPCC_SECOND'])
    def test_unlimited_time_refuses(self):
        self.assertFalse(router.route('query_laps',self.s(time_remain_s=604800),'还剩多久').ok)
    def test_class_position_not_overall(self):
        plan=router.route('query_position',self.s(position=9,class_position=3),'同组第几名').plan
        self.assertEqual(plan[0],'AST2_CLASS_POSITION_LEAD');self.assertIn('SPCC_N3',plan)
    def test_position_six_is_exact(self):
        self.assertIn('SPCC_N6',router.route('query_position',self.s(position=6)).plan)
    def test_lap_delta_not_last_lap(self):
        plan=router.route('query_laptime',self.s(lap_last=90,lap_best=89),'比最佳慢多少').plan
        self.assertEqual(plan[0],'AST2_LAP_LOSS_LEAD');self.assertNotIn('SPCC_MINUTE',plan)
    def test_pit_permissions_false(self):
        self.assertEqual(router.route('query_pit',self.s(pits_open=False),'维修区开放吗').plan,['AST2_PITS_CLOSED'])
    def test_repair_time_not_valid_outside_active_service(self):
        self.assertFalse(router.route('query_pit',self.s(repair_required_s=0,pitstop_active=False),'必修还剩多久').ok)
    def test_mandatory_repair_timer(self):
        plan=router.route('query_pit',self.s(repair_required_s=80,pitstop_active=True),'必修还剩多久').plan
        self.assertEqual(plan[0],'AST2_REPAIR_REQUIRED_LEAD');self.assertIn('SPCC_MINUTE',plan)
    def test_fast_repair_255_is_unlimited(self):
        self.assertEqual(router.route('query_pit',self.s(fast_repairs_available=255),'还剩几次快修').plan,['AST2_FAST_UNLIMITED'])
    def test_tyre_live_pressure_not_invented(self):
        self.assertFalse(router.route('query_tyre',self.s(tyre_cold_pressure={'LF':145}),'左前胎压多少').ok)
    def test_cold_pressure_is_labeled(self):
        self.assertEqual(router.route('query_tyre',self.s(tyre_cold_pressure={'LF':145}),'左前冷胎气压多少').plan[0],'AST2_LF_COLD_PRESSURE_LEAD')
    def test_cold_temperature_not_reported_as_pressure(self):
        self.assertFalse(router.route('query_tyre',self.s(tyre_cold_pressure={'LF':145}),'左前冷胎温度多少').ok)
    def test_history_tyre_label(self):
        plan=router.route('query_tyre',self.s(tyre_history={'RF':{'wear':.93}}),'右前胎磨损多少').plan
        self.assertEqual(plan[0],'AST2_RF_WEAR_LEAD');self.assertIn('SPCC_N93',plan)
    def test_first_session_default_tyre_values_not_history(self):
        s=self.s(in_pit_stall=True,lap_completed=0,tyre_raw={'LF':{'wear':.9,'temp':80}})
        telemetry.DerivedTracker().update(s);self.assertEqual(s.tyre_history,{})
    def test_derived_gap_is_explicit_estimate(self):
        s=self.s(car_idx=0,car_lap_dist=[.5,.52,.48],car_est_times=[50,52,48],car_surfaces=[3,3,3],car_in_pits=[False]*3)
        telemetry.DerivedTracker().update(s)
        self.assertEqual(s.gap_ahead_s,2);self.assertEqual(s.gap_behind_s,2)
        self.assertEqual(router.route('query_gap',s,'前车多远').plan[0],'AST2_GAP_AHEAD_EST_LEAD')
    def test_wrapped_gap_needs_valid_lap_period(self):
        s=self.s(car_idx=0,car_lap_dist=[.98,.02],car_est_times=[98,2],lap_best=100)
        telemetry.DerivedTracker().update(s);self.assertEqual(s.gap_ahead_s,4)
    def test_pit_opponent_excluded(self):
        s=self.s(car_idx=0,car_lap_dist=[.5,.52],car_est_times=[50,52],car_in_pits=[False,True])
        telemetry.DerivedTracker().update(s);self.assertIsNone(s.gap_ahead_s)
    def test_pace_car_excluded(self):
        s=self.s(car_idx=0,car_lap_dist=[.5,.52,.53],car_est_times=[50,52,53],pace_car_indices={1})
        telemetry.DerivedTracker().update(s);self.assertEqual(s.gap_ahead_idx,2)
    def test_red_flag_coexisting_dq_prefers_dq(self):
        self.assertEqual(router.route('query_flags',self.s(session_flags=0x20010)).plan,
                         ['AST2_DISQUALIFY_OUT'])
    def test_absent_flags_not_reported_as_no_flag(self):
        self.assertFalse(router.route('query_flags',self.s(session_flags_known=False),'现在什么旗').ok)
    def test_brake_bias_percent(self):
        plan=router.route('query_car_settings',self.s(brake_bias=54.2),'前轮刹车比例多少').plan
        self.assertIn('AST2_UNIT_PERCENT',plan)

class BindingTests(unittest.TestCase):
    def test_unique_guid_reconnect_matches(self):
        self.assertEqual(len(WheelInput.matches({'guid':'a','path':'old'},[{'guid':'a','path':'new'}])),1)
    def test_same_guid_two_devices_ambiguous_refuses(self):
        self.assertEqual(WheelInput.matches({'guid':'a'},[{'guid':'a','path':'x'},{'guid':'a','path':'y'}]),[])
    def test_same_guid_path_exact(self):
        self.assertEqual(WheelInput.matches({'guid':'a','path':'y'},[{'guid':'a','path':'x'},{'guid':'a','path':'y'}]),[{'guid':'a','path':'y'}])
    def test_wrong_serial_does_not_fall_back(self):
        self.assertEqual(WheelInput.matches({'guid':'a','serial':'1'},[{'guid':'a','serial':'2'}]),[])
    def test_invalid_button_not_saved(self):
        cfg=config._merge(config.DEFAULTS,{'ptt':{'binding':{'type':'wheel','guid':'0'*32,'button':256}}})
        with self.assertRaises(ValueError):config.validate(cfg)

if __name__=='__main__':unittest.main()
