"""遥测采集与语义快照。

provider=irsdk 读 iRacing SDK 共享内存（只读，绝不写游戏文件）
provider=sim   用内置假数据，方便没开游戏时调逻辑
"""
from __future__ import annotations

import time
import math
import threading
import re
from dataclasses import replace
from dataclasses import dataclass, field
from typing import Any

# iRacing SessionFlags 位定义（只挑用得到的）
FLAG_BITS = {
    "checkered": 0x00000001,
    "white": 0x00000002,
    "green": 0x00000004,
    "yellow": 0x00000008,
    "red": 0x00000010,
    "blue": 0x00000020,
    "debris": 0x00000040,
    "yellow_waving": 0x00000100,
    "one_lap_to_green": 0x00000200,
    "green_held": 0x00000400,
    "ten_to_go": 0x00000800,
    "five_to_go": 0x00001000,
    "caution": 0x00004000,
    "caution_waving": 0x00008000,
    "black": 0x00010000,
    "disqualify": 0x00020000,
    # 卷起的黑旗：切弯占了便宜，必须减速把优势还回去（还掉即自动解除）
    "furled": 0x00080000,
    "dq_scoring_invalid": 0x00200000,
    "repair": 0x00100000,
}

# TrackWetness 枚举 → 文案档位
WETNESS = {1: "dry", 2: "dry", 3: "damp", 4: "wet", 5: "wet", 6: "wet", 7: "wet"}


def decode_flags(raw: int) -> list[str]:
    return [name for name, bit in FLAG_BITS.items() if raw & bit]


def flatten_setup(node: Any, prefix: str = "") -> dict[str, Any]:
    """把 session YAML 的 CarSetup 段落展平成 {路径: 值}。

    各车种的段名不统一（MX-5 在 Suspension.Front.AntiRollBar，
    保时捷在 Chassis.Front.ArbSetting），所以先展平、再按叶子名找。
    """
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(flatten_setup(value, path))
            else:
                out[path] = value
    return out


@dataclass
class Snapshot:
    """一次语义快照。字段用不到就留 None，下游要判空。"""

    connected: bool = False
    source: str = "-"
    session_id: tuple | None = None
    session_time: float | None = None
    in_garage: bool | None = None
    t: float = field(default_factory=time.time)

    # 天气
    air_temp: float | None = None
    track_temp: float | None = None
    track_wetness: int | None = None
    skies: int | None = None
    precip: float | None = None
    wind_vel: float | None = None
    relative_humidity: float | None = None
    declared_wet: bool | None = None

    # 燃油
    fuel_level: float | None = None
    fuel_pct: float | None = None
    fuel_use_per_hour: float | None = None

    # 圈
    lap: int | None = None
    lap_completed: int | None = None
    lap_last: float | None = None
    lap_best: float | None = None
    laps_remain: int | None = None
    time_remain_s: float | None = None

    # 位置
    position: int | None = None
    car_idx: int | None = None
    field_size: int | None = None
    class_position: int | None = None
    lap_current: float | None = None
    speed_kph: float | None = None
    water_temp: float | None = None
    oil_temp: float | None = None
    rpm: float | None = None
    gear: int | None = None
    voltage: float | None = None
    engine_warnings: int | None = None
    pits_open: bool | None = None
    in_pit_stall: bool | None = None
    pitstop_active: bool | None = None
    repair_required_s: float | None = None
    repair_optional_s: float | None = None
    fast_repairs_available: int | None = None
    pit_speed_limit_kph: float | None = None
    pit_fuel_add: float | None = None
    tow_time_s: float | None = None
    brake_bias: float | None = None
    abs_level: int | None = None
    tc_level: int | None = None
    tyre_cold_pressure: dict = field(default_factory=dict)
    tyre_raw: dict = field(default_factory=dict)
    tyre_history: dict = field(default_factory=dict)
    # 车辆调教（session YAML 的 CarSetup 段落，展平成 {路径: 值}）
    car_setup: dict = field(default_factory=dict)
    car_setup_update: int | None = None
    driver_setup_name: str | None = None
    driver_setup_modified: bool | None = None
    on_track_player: bool | None = None
    # 赛道橡胶状态（SessionInfo.Sessions[].SessionTrackRubberState）
    track_rubber: str | None = None
    car_lap_dist: list | None = None
    car_est_times: list | None = None
    car_in_pits: list | None = None
    car_surfaces: list | None = None
    car_positions: list | None = None
    car_class_positions: list | None = None
    car_last_laps: list | None = None
    car_best_laps: list | None = None
    car_completed_laps: list | None = None
    pace_car_indices: list = field(default_factory=list)
    gap_estimated: bool = False
    gap_ahead_idx: int | None = None
    gap_behind_idx: int | None = None

    # 状态
    session_flags: int = 0
    session_flags_known: bool = True
    on_pit_road: bool | None = None
    incident_count: int | None = None

    # 派生量（由 DerivedTracker 填）
    fuel_per_lap: float | None = None
    laps_of_fuel: float | None = None
    # 与前后车的差距（秒）与走势：+ 表示在追近/被追近
    gap_ahead_s: float | None = None
    gap_behind_s: float | None = None
    gap_ahead_trend: float | None = None    # 负 = 在追近前车
    gap_behind_trend: float | None = None   # 负 = 后车在追近（对我们不利）

    @property
    def flags(self) -> list[str]:
        return decode_flags(self.session_flags)

    def wetness_word(self) -> str | None:
        return WETNESS.get(self.track_wetness) if self.track_wetness is not None else None

    def setup_value(self, *names: str):
        """按叶子名取调教值，不看层级，兼容不同车种的 CarSetup 段名差异。"""
        for key, value in self.car_setup.items():
            if key.rsplit(".", 1)[-1] in names and value not in (None, ""):
                return value
        return None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class DerivedTracker:
    """由状态层计算的趋势量。SDK 不直接提供。"""

    def __init__(self, keep_laps: int = 20):
        self.keep_laps = keep_laps
        self._last_lap: int | None = None
        self._lap_start_fuel: float | None = None
        self.fuel_per_lap_samples: list[float] = []
        self._session_id = None
        self._session_time = None
        self._last_fuel = None
        self._valid_lap = False
        self._tyre_history={}
        self._gap_history={'ahead':[],'behind':[]}

    def reset(self) -> None:
        self._last_lap = self._lap_start_fuel = self._last_fuel = None
        self._valid_lap = False
        self.fuel_per_lap_samples.clear()
        self._tyre_history={}
        self._gap_history={'ahead':[],'behind':[]}

    def update(self, s: Snapshot) -> Snapshot:
        if (not s.connected or s.in_garage or s.session_id != self._session_id
                or (s.session_time is not None and self._session_time is not None
                    and s.session_time < self._session_time)
                or (s.lap is not None and self._last_lap is not None and s.lap < self._last_lap)):
            self.reset()
        self._session_id, self._session_time = s.session_id, s.session_time
        if not s.connected or s.in_garage:
            return s
        # F6 为进站历史检测值；没有观察到有效进站时不把默认值当读数。
        if s.in_pit_stall and s.lap_completed is not None and s.lap_completed>=1:
            for wheel,values in s.tyre_raw.items():
                wear=values.get('wear')
                if wear is not None and 0<=wear<1:
                    self._tyre_history[wheel]={**values,'session_time':s.session_time}
        s.tyre_history={wheel:dict(values) for wheel,values in self._tyre_history.items()}
        self._derive_gaps(s)
        if s.fuel_level is not None and self._last_fuel is not None and s.fuel_level > self._last_fuel + 0.05:
            self._valid_lap = False
        if s.on_pit_road:
            self._valid_lap = False
        self._last_fuel = s.fuel_level
        # 每圈油量：用「上圈开始时的油量 − 本圈开始时的油量」估
        if s.lap is not None and s.fuel_level is not None:
            if self._last_lap is None:
                self._last_lap = s.lap
                self._lap_start_fuel = s.fuel_level
            elif s.lap != self._last_lap:
                if self._valid_lap and s.lap == self._last_lap + 1 and self._lap_start_fuel is not None:
                    used = self._lap_start_fuel - s.fuel_level
                    if 0.05 < used < 20:      # 过滤进站加油等异常
                        self.fuel_per_lap_samples.append(used)
                        del self.fuel_per_lap_samples[:-self.keep_laps]
                self._last_lap = s.lap
                self._lap_start_fuel = s.fuel_level
                self._valid_lap = not s.on_pit_road

        if self.fuel_per_lap_samples:
            # 用中位数抗单圈异常
            xs = sorted(self.fuel_per_lap_samples)
            n = len(xs)
            s.fuel_per_lap = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
            if s.fuel_level is not None and s.fuel_per_lap:
                s.laps_of_fuel = s.fuel_level / s.fuel_per_lap
        return s

    def _derive_gaps(self,s):
        """按 SDK 的位置/估计时间读取邻车估计秒差，明确标为估计值。"""
        idx=s.car_idx;dist=s.car_lap_dist;times=s.car_est_times
        if s.on_pit_road:return
        # 车库/不在世界上时自己的 EstTime 会保留旧值（2026-10-14 实测），必须挡掉
        if s.on_track_player is False:return
        if idx is None or not dist or not times or not 0<=idx<min(len(dist),len(times)):
            return
        if not 0<=dist[idx]<=1 or not math.isfinite(times[idx]) or times[idx]<0:return
        candidates=[]
        for i,pct in enumerate(dist):
            if i==idx or i in s.pace_car_indices or i>=len(times) or not math.isfinite(pct) or not 0<=pct<=1:continue
            if s.car_in_pits and i<len(s.car_in_pits) and s.car_in_pits[i]:continue
            if s.car_surfaces and i<len(s.car_surfaces) and s.car_surfaces[i] not in (2,3):continue
            if not math.isfinite(times[i]) or times[i]<=0:continue
            candidates.append(i)
        period=s.lap_best if s.lap_best and s.lap_best>0 else s.lap_last
        for side in ('ahead','behind'):
            choices=[(((dist[i]-dist[idx]) if side=='ahead' else (dist[idx]-dist[i]))%1,i) for i in candidates]
            choices=[(d,i) for d,i in choices if .00001<d<=.5]
            if not choices:self._gap_history[side]=[];continue
            _,other=min(choices)
            gap=(times[other]-times[idx]) if side=='ahead' else (times[idx]-times[other])
            wrapped=(dist[other]<dist[idx]) if side=='ahead' else (dist[other]>dist[idx])
            if wrapped:
                if not period or period<=0:self._gap_history[side]=[];continue
                gap+=period
            if not math.isfinite(gap) or not 0<=gap<=60:self._gap_history[side]=[];continue
            setattr(s,f'gap_{side}_s',gap);setattr(s,f'gap_{side}_idx',other);s.gap_estimated=True
            history=self._gap_history[side]
            now=s.session_time if s.session_time is not None else s.t
            if history and (history[-1][2]!=other or now<history[-1][0]):history.clear()
            if not history or now-history[-1][0]>=.25:history.append((now,gap,other))
            history[:]=[row for row in history if now-row[0]<=6]
            if history and now-history[0][0]>=3:
                setattr(s,f'gap_{side}_trend',(gap-history[0][1])/(now-history[0][0]))


class SimTelemetry:
    """内置假数据。模拟一场 GT3 比赛的中段。"""

    def __init__(self, **overrides: Any):
        self.overrides = overrides

    def read(self) -> Snapshot:
        base = dict(
            connected=True, source="sim",
            air_temp=28.0, track_temp=41.0, track_wetness=1, skies=1,
            precip=0.0, wind_vel=3.2, declared_wet=False,
            fuel_level=18.4, fuel_pct=0.42, fuel_use_per_hour=62.1,
            lap=12, lap_completed=11, lap_last=83.512, lap_best=83.104,
            laps_remain=8, time_remain_s=1120.0,
            position=4, car_idx=7, field_size=18,
            session_flags=FLAG_BITS["green"], on_pit_road=False, incident_count=2,
            gap_ahead_s=1.8, gap_behind_s=1.2,
            gap_ahead_idx=2, gap_behind_idx=4,
            car_positions=[1,2,3,6,5,7,8,4],
            car_class_positions=[1,2,2,4,3,5,6,3],
            car_lap_dist=[0.30,0.20,0.25,0.35,0.15,0.40,0.50,0.22],
            car_est_times=[27.0,18.0,22.5,31.5,13.5,36.0,45.0,19.8],
            car_last_laps=[82.1,82.8,83.2,84.3,83.8,84.5,85.0,83.512],
            car_best_laps=[81.9,82.5,83.0,84.0,83.5,84.2,84.8,83.104],
            car_completed_laps=[12,11,11,11,11,11,11,11],
            gap_ahead_trend=-0.30, gap_behind_trend=-0.60,
            relative_humidity=.62,class_position=3,lap_current=34.2,speed_kph=187.2,
            water_temp=86.0,oil_temp=102.0,engine_warnings=0,pits_open=True,in_pit_stall=False,
            pitstop_active=False,repair_required_s=0,repair_optional_s=0,fast_repairs_available=2,
            pit_speed_limit_kph=60.0,pit_fuel_add=24.0,brake_bias=54.2,abs_level=3,tc_level=4,
            tow_time_s=0.0,
            tyre_cold_pressure={'LF':145.0,'RF':145.0,'LR':148.0,'RR':148.0},
            on_track_player=True,
            track_rubber='moderately high usage',
            car_setup={
                'Chassis.InCarDials.AbsSetting':'3 (ABS)',
                'Chassis.InCarDials.TcSetting':'2 (TC)',
                'Chassis.InCarDials.BrakePressureBias':'50.0%',
                'Chassis.Front.ArbSetting':'1',
                'Chassis.Rear.ArbSetting':'6',
                'Chassis.Rear.WingAngle':'12',
                'Tires.LeftFront.StartingPressure':'159 kPa',
            },
            car_setup_update=2, driver_setup_name='sim.sto', driver_setup_modified=False,
        )
        base.update(self.overrides)
        s = Snapshot(**base)
        # 让派生量有值，便于验证油量分支
        s.fuel_per_lap = 1.42
        s.laps_of_fuel = round(s.fuel_level / s.fuel_per_lap, 1) if s.fuel_level is not None else None
        return s


class IrsdkTelemetry:
    """读 iRacing SDK 共享内存。未连上游戏时 connected=False。"""

    def __init__(self):
        self._ir = None
        self._err: str | None = None
        try:
            import irsdk  # type: ignore
            self._ir = irsdk.IRSDK()
        except Exception as exc:  # noqa: BLE001
            self._err = f"pyirsdk 不可用：{exc}"

    @property
    def error(self) -> str | None:
        return self._err

    def read(self) -> Snapshot:
        if self._ir is None:
            return Snapshot(connected=False, source=f"irsdk({self._err})")
        if not self._ir.is_initialized or not self._ir.is_connected:
            try:
                if self._ir.is_initialized:
                    self._ir.shutdown()
                self._ir.startup()
            except Exception:  # noqa: BLE001
                pass
        if not (self._ir.is_initialized and self._ir.is_connected):
            return Snapshot(connected=False, source="irsdk")

        ir = self._ir
        try:
            ir.freeze_var_buffer_latest()
            return self._read_frozen(ir)
        except Exception as exc:
            self._err = f"遥测读取失败：{exc}"
            return Snapshot(connected=False, source="irsdk")
        finally:
            ir.unfreeze_var_buffer_latest()

    def _read_frozen(self, ir) -> Snapshot:
        def get(k, d=None):
            try:
                value = ir[k]
            except (KeyError, TypeError, ValueError):
                return d
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                return d
            return value
        s = Snapshot(
            connected=True, source="irsdk",
            session_id=(get("SessionUniqueID"), get("SessionNum"), get("PlayerCarIdx")),
            session_time=get("SessionTime"), in_garage=get("IsInGarage"),
            air_temp=get("AirTemp"), track_temp=get("TrackTempCrew"),
            track_wetness=get("TrackWetness"), skies=get("Skies"),
            precip=get("Precipitation"), wind_vel=get("WindVel"),
            declared_wet=get("WeatherDeclaredWet"),
            fuel_level=get("FuelLevel"), fuel_pct=get("FuelLevelPct"),
            fuel_use_per_hour=get("FuelUsePerHour"),
            lap=get("Lap"), lap_completed=get("LapCompleted"),
            lap_last=get("LapLastLapTime"), lap_best=get("LapBestLapTime"),
            laps_remain=get("SessionLapsRemainEx"), time_remain_s=get("SessionTimeRemain"),
            position=get("PlayerCarPosition"), car_idx=get("PlayerCarIdx"),
            session_flags=get("SessionFlags") or 0,
            session_flags_known=get('SessionFlags') is not None,
            on_pit_road=get("OnPitRoad"), incident_count=get("PlayerCarMyIncidentCount"),
            relative_humidity=get('RelativeHumidity'),class_position=get('PlayerCarClassPosition'),
            lap_current=get('LapCurrentLapTime'),speed_kph=get('Speed')*3.6 if get('Speed') is not None else None,
            water_temp=get('WaterTemp'),oil_temp=get('OilTemp'),engine_warnings=get('EngineWarnings'),
            rpm=get('RPM'),gear=get('Gear'),voltage=get('Voltage'),
            pits_open=get('PitsOpen'),in_pit_stall=get('PlayerCarInPitStall'),pitstop_active=get('PitstopActive'),
            repair_required_s=get('PitRepairLeft'),repair_optional_s=get('PitOptRepairLeft'),
            fast_repairs_available=get('FastRepairAvailable'),brake_bias=get('dcBrakeBias'),
            tow_time_s=get('PlayerCarTowTime'),
            abs_level=get('dcABS'),tc_level=get('dcTractionControl'),
            car_lap_dist=get('CarIdxLapDistPct'),car_est_times=get('CarIdxEstTime'),
            car_in_pits=get('CarIdxOnPitRoad'),car_surfaces=get('CarIdxTrackSurface'),
            car_positions=get('CarIdxPosition'),car_class_positions=get('CarIdxClassPosition'),
            car_last_laps=get('CarIdxLastLapTime'),
            car_best_laps=get('CarIdxBestLapTime'),car_completed_laps=get('CarIdxLapCompleted'),
        )
        weekend=get('WeekendInfo',{}) or {}
        limit=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(kph|km/h|mph)\s*',str(weekend.get('TrackPitSpeedLimit','')),flags=re.I)
        if limit:s.pit_speed_limit_kph=float(limit[1])*(1.609344 if limit[2].lower()=='mph' else 1)
        driver=get('DriverInfo',{}) or {}
        s.pace_car_indices=[d['CarIdx'] for d in driver.get('Drivers',[])
                            if d.get('CarIsPaceCar') and isinstance(d.get('CarIdx'),int)]
        if isinstance(driver.get('DriverCarFuelMaxLtr'),(int,float)) and driver['DriverCarFuelMaxLtr']>0:
            s.pit_fuel_add=get('PitSvFuel')
        setup=get('CarSetup',{}) or {}
        if setup:
            s.car_setup=flatten_setup({k:v for k,v in setup.items() if k!='UpdateCount'})
            update=setup.get('UpdateCount')
            s.car_setup_update=update if isinstance(update,int) else None
        s.driver_setup_name=driver.get('DriverSetupName')
        try:
            sessions=(get('SessionInfo') or {}).get('Sessions') or []
            want=get('SessionNum')
            row=next((r for r in sessions if r.get('SessionNum')==want),
                     sessions[-1] if sessions else None)
            if row:s.track_rubber=row.get('SessionTrackRubberState')
        except Exception:pass
        modified=driver.get('DriverSetupIsModified')
        s.driver_setup_modified=bool(modified) if modified is not None else None
        if s.car_lap_dist and s.car_idx is not None and 0<=s.car_idx<len(s.car_lap_dist):
            dist=s.car_lap_dist[s.car_idx]
            s.on_track_player=bool(dist is not None and dist>=0)
        for wheel in ('LF','RF','LR','RR'):
            pressure=get(wheel+'coldPressure')
            if pressure is not None and 20<=pressure<=500:s.tyre_cold_pressure[wheel]=pressure
            wear=[get(wheel+'wear'+side) for side in ('L','M','R')]
            # 实测（2026-10-14 正赛）：iRacing 暴露的是 LFtempCL/CM/CR，旧的 LFtempL 并不存在，
            # 只读旧名字会永远拿不到胎温。两个名字都试，优先新命名。
            temps=[get(wheel+'tempC'+side, get(wheel+'temp'+side)) for side in ('L','M','R')]
            s.tyre_raw[wheel]={
                'wear':min(wear) if all(x is not None and 0<=x<=1 for x in wear) else None,
                'temp':sum(temps)/3 if all(x is not None and 0<x<250 for x in temps) else None,
            }
        try:
            arr = ir["CarIdxPosition"]
            if arr:
                s.field_size = len([x for x in arr if x and x > 0])
        except Exception:  # noqa: BLE001
            pass
        if s.laps_remain is not None and not 0 <= s.laps_remain <= 9999:
            s.laps_remain = None
        for name in ("lap_last", "lap_best", "position",'class_position'):
            if getattr(s, name) is not None and getattr(s, name) <= 0:
                setattr(s, name, None)
        self._err = None
        return s

    def close(self) -> None:
        if self._ir is not None:
            self._ir.shutdown()


def build(cfg: dict, force_sim: bool = False):
    """模拟数据必须显式选择；SDK 故障保持未连接，避免比赛中播报假数据。"""
    provider = "sim" if force_sim else cfg.get("telemetry", {}).get("provider", "irsdk")
    if provider == "sim":
        return SimTelemetry(), None
    t = IrsdkTelemetry()
    if t.error:
        return t, t.error
    return t, None


class TelemetrySampler:
    """持续采样，不依赖 UI 轮询或用户提问；所有 SDK 读取在同一线程。"""

    def __init__(self, provider, tracker, hz=60, priority="below_normal"):
        self.provider, self.tracker = provider, tracker
        self.period = 1 / hz
        self.priority = priority
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._snapshot = Snapshot(source="sim" if isinstance(provider, SimTelemetry) else "irsdk")
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telemetry")
        self._thread.start()
        self._ready.wait(1)

    def _loop(self):
        if self.priority == "below_normal":
            try:
                import ctypes
                kernel = ctypes.windll.kernel32
                kernel.GetCurrentThread.restype = ctypes.c_void_p
                kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
                kernel.SetThreadPriority(kernel.GetCurrentThread(), -1)
            except Exception:
                pass
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    snap = self.tracker.update(self.provider.read())
                except Exception:
                    self.tracker.reset()
                    snap = Snapshot(source="irsdk")
                with self._lock:
                    self._snapshot = snap
                self._ready.set()
                period = self.period if snap.connected else max(.5,self.period)
                self._stop.wait(max(0, period - (time.monotonic() - started)))
        finally:
            if hasattr(self.provider, "close"):
                self.provider.close()

    def read(self):
        with self._lock:
            snap = replace(self._snapshot)
        if time.time() - snap.t > 2:
            return Snapshot(source=snap.source)
        return snap

    def close(self):
        self._stop.set()
        self._thread.join(2)
