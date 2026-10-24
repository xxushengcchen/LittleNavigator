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

from .segments import SegmentModel, normalize_sector_starts


def _positive(value: Any) -> bool:
    """有限正数（bool 不算）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _nonneg(value: Any) -> bool:
    """有限非负数（bool 不算）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


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

# 「缓存里还没有值」的哨兵。不能用 None：session_id 本身可能是 None，
# 用它当 key 时「未缓存」和「缓存了 None 键」必须能区分。
_NOT_SET = object()


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
    # --- v1.5.4 赛段计时 ---
    # SplitTimeInfo 边界（0..1 升序，含 0.0；空 tuple = 本场没有分段信息）。
    sector_starts: tuple[float, ...] = ()
    track_num_turns: int | None = None
    # CarIdxClass：同组别才能比较分段；-1/缺失表示未知。
    car_classes: list | None = None

    # 状态
    session_flags: int = 0
    session_flags_known: bool = True
    on_pit_road: bool | None = None
    incident_count: int | None = None
    # 进站服务勾选位（PitSvFlags）。语音改车之后回读它做闭环验证：
    # 「已发送」不等于「已生效」，只有这个位真的变了才算数。
    pit_sv_flags: int | None = None
    pit_sv_fuel: float | None = None

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
        # 圈级历史：给 AI 上下文用。与 fuel_per_lap_samples 是两套判据——
        # 进站加油圈油耗不可信，但圈速与胎温仍然有效，不能因为油耗不可用就整圈丢掉。
        self.lap_records: list[dict] = []
        self._lap_open: dict | None = None
        self._lap_crossed_pit = False
        # v1.5.4：分段计时模型。采样线程在 update() 里喂数据；
        # 对外只读（race_context 通过它取前后车分段对比）。
        self.segments = SegmentModel()

    def reset(self) -> None:
        self._last_lap = self._lap_start_fuel = self._last_fuel = None
        self._valid_lap = False
        self.fuel_per_lap_samples.clear()
        self._tyre_history={}
        self._gap_history={'ahead':[],'behind':[]}
        self.lap_records.clear()
        self._lap_open = None
        self._lap_crossed_pit = False
        self.segments.reset()

    @property
    def valid_lap(self) -> bool:
        """当前圈是否可用于油耗样本。圈记录另有判据，见 `_close_lap`。"""
        return self._valid_lap

    def record_lap(self, summary: dict, trace=None) -> None:
        """写入一条圈记录，保留最近 keep_laps 条。

        `trace` 为弯道级轨迹预留（第二期填 `(distance_pct, lap_time)` 序列），
        本期恒为 None，因此这里不复制也不校验它。
        """
        self.lap_records.append(summary)
        del self.lap_records[:-self.keep_laps]

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
        # 分段计时要在圈末结算（_close_lap）之前喂：它自己在圈末回绕时结算，
        # 圈记录才能挂上刚算出来的分段用时。
        self.segments.update(s)
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
                self._open_lap(s)
            elif s.lap != self._last_lap:
                if self._valid_lap and s.lap == self._last_lap + 1 and self._lap_start_fuel is not None:
                    used = self._lap_start_fuel - s.fuel_level
                    if 0.05 < used < 20:      # 过滤进站加油等异常
                        self.fuel_per_lap_samples.append(used)
                        del self.fuel_per_lap_samples[:-self.keep_laps]
                # 圈记录：`_derive_gaps` 已在上面跑过，此时 s 上的 gap 是本帧的，
                # 而不是上一帧残留——顺序错了圈末差距会晚一圈。
                self._close_lap(s)
                self._last_lap = s.lap
                self._lap_start_fuel = s.fuel_level
                self._valid_lap = not s.on_pit_road
                self._open_lap(s)

        if self.fuel_per_lap_samples:
            # 用中位数抗单圈异常
            xs = sorted(self.fuel_per_lap_samples)
            n = len(xs)
            s.fuel_per_lap = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
            if s.fuel_level is not None and s.fuel_per_lap:
                s.laps_of_fuel = s.fuel_level / s.fuel_per_lap
        return s

    def _open_lap(self, s: Snapshot) -> None:
        """开一个新圈：记住圈初状态，圈末收尾时用来算差值。"""
        self._lap_open = {
            "lap": s.lap,
            "fuel_start": s.fuel_level,
            "session_time": s.session_time,
            "tyre": {w: dict(v) for w, v in (s.tyre_raw or {}).items()},
            "incidents": s.incident_count,
        }
        self._lap_crossed_pit = bool(s.on_pit_road)

    def _close_lap(self, s: Snapshot) -> None:
        """收尾上一圈并写入圈记录。

        两套判据，别合并：
          · `fuel_used` 只在 `self._valid_lap` 为真时给值（进站/加油/跨线维修区不可信）
          · 圈记录本身只要求「圈号连续 + 跨线时不在维修区 + session 未重置」，
            因为这些数据（圈速、胎温、名次、差距）在加油圈里依然是真实的。
        """
        opened = self._lap_open
        if opened is None:
            return
        # 圈号必须连续，否则是跳圈/重连，整圈数据不可信
        if s.lap is None or opened["lap"] is None or s.lap != opened["lap"] + 1:
            self._lap_open = None
            return
        # 跨线时人在维修区：这一圈圈速含维修区行驶时间，不是有效圈速
        if s.on_pit_road:
            self._lap_open = None
            return

        fuel_used = None
        if self._valid_lap and opened["fuel_start"] is not None and s.fuel_level is not None:
            used = opened["fuel_start"] - s.fuel_level
            if 0.05 < used < 20:
                fuel_used = round(used, 2)

        tyre_now = s.tyre_raw or {}
        tyre = {}
        for wheel in ("LF", "RF", "LR", "RR"):
            cur = tyre_now.get(wheel) or {}
            tyre[wheel] = {
                "temp": round(cur["temp"], 1) if cur.get("temp") is not None else None,
                "wear": round(cur["wear"], 3) if cur.get("wear") is not None else None,
            }

        # 分段用时：只有分段模型判定有效的那圈才有；无效圈给空 list，
        # 下游（race_context / 界面）据此知道「这圈不能拿来比」。
        sector_times = self.segments.sectors_for(opened["lap"])
        sectors = ([round(value, 3) if value is not None else None
                    for value in sector_times] if sector_times is not None else [])

        self.record_lap({
            "lap": opened["lap"],
            "lap_time": round(s.lap_last, 3) if s.lap_last and s.lap_last > 0 else None,
            "sectors": sectors,
            "fuel_start": opened["fuel_start"],
            "fuel_used": fuel_used,
            "tyre": tyre,
            "position": s.position,
            "class_position": s.class_position,
            "gap_ahead_s": round(s.gap_ahead_s, 2) if s.gap_ahead_s is not None else None,
            "gap_behind_s": round(s.gap_behind_s, 2) if s.gap_behind_s is not None else None,
            "flags": list(s.flags),
            "in_pit": bool(self._lap_crossed_pit),
            "air_temp": s.air_temp,
            "track_temp": s.track_temp,
            "wetness": s.track_wetness,
            "incidents": opened["incidents"],
        })
        self._lap_open = None

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
    """内置假数据。模拟一场 GT3 比赛的中段。

    `advance=True` 时每 `lap_seconds` 秒推进一圈，让 `--sim` 也能产出圈记录；
    默认关闭，保持既有测试的快照断言不变。
    """

    def __init__(self, advance: bool = False, lap_seconds: float = 8.0,
                 **overrides: Any):
        self.advance = advance
        self.lap_seconds = max(0.05, float(lap_seconds))
        self.overrides = overrides
        self._t0 = time.monotonic()
        self._base_lap = overrides.get("lap", 12)

    def _elapsed_state(self) -> tuple[float, int, float]:
        """一次取样的三个时间量：(总时长, 已完成圈数, 本圈已过秒数)。

        三个值必须来自**同一个** monotonic 读数：分成三次调用会在
        圈边界上出现「圈号还是旧的、位置已经回绕」的错位帧，分段模型
        会把刚跑完的圈当成跳圈丢掉。
        """
        seconds = max(0.0, time.monotonic() - self._t0)
        laps = int(seconds / self.lap_seconds)
        return seconds, laps, seconds - laps * self.lap_seconds

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
            # 分段计时：固定 4 段（0 / 25% / 50% / 75%），让模拟模式与单测可跑；
            # 同组别（class=1）让前后车对比在 --sim 下有数据。
            sector_starts=(0.0, 0.25, 0.5, 0.75),
            track_num_turns=20,
            car_classes=[1, 1, 1, 1, 1, 1, 1, 1],
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
        # --sim-advance：按经过时间推进圈数与圈内位置，让 --sim 也能产出
        # 圈记录与分段用时。不推进时（默认）所有字段与原来完全一致，
        # 既有测试断言不受影响。
        if self.advance:
            seconds, elapsed, progress_s = self._elapsed_state()
            self._apply_advance(s, seconds, elapsed, progress_s)
        return s

    def _apply_advance(self, s: Snapshot, seconds: float, elapsed: int,
                       progress_s: float) -> None:
        """推进模拟遥测：时间/圈号/油量 + 圈内位置（供分段计时验证）。

        位置和 session_time 都按同一个单调时钟线性推进，线性插值才能
        精确还原跨段时刻；否则「哪一段慢」测出来的只是采样抖动。
        """
        progress = progress_s / self.lap_seconds
        original = list(s.car_lap_dist or [])
        s.session_time = seconds
        s.lap = (s.lap or 0) + elapsed
        s.lap_completed = (s.lap_completed or 0) + elapsed
        s.lap_current = round(progress_s, 3)
        # 玩家车从线后起步（pct=0），跨线时刻正好落在 elapsed 的整数倍上，
        # 圈号与位置才自洽；否则「位置已回绕、圈号还没变」会触发跳圈过滤，
        # 模拟模式下永远出不了有效分段。
        player = s.car_idx if isinstance(s.car_idx, int) else None
        if original:
            s.car_lap_dist = [
                round(progress % 1.0, 4) if index == player
                else round((pct + progress) % 1.0, 4)
                for index, pct in enumerate(original)]
        # 圈速与分段之和必须自洽（Σ 校验），所以模拟圈速就是 lap_seconds。
        lap_seconds = round(self.lap_seconds, 3)
        s.lap_last = lap_seconds
        s.lap_best = lap_seconds
        if s.car_last_laps:
            s.car_last_laps = [lap_seconds] * len(s.car_last_laps)
        if s.car_best_laps:
            s.car_best_laps = [lap_seconds] * len(s.car_best_laps)
        if s.car_completed_laps:
            # 每辆车按自己的圈内偏移跨线：玩家落在整圈边界，其他车按
            # (偏移 + 进度) 是否过 1 再补一圈。这样前车/后车的圈号与它
            # 自己的位置一致，分段模型不会把它们当跳圈整圈丢掉。
            completed = []
            for index, laps in enumerate(s.car_completed_laps):
                offset = 0.0 if index == player else (
                    original[index] if index < len(original) else 0.0)
                completed.append(laps + elapsed + int(offset + progress))
            s.car_completed_laps = completed
        # 每圈消耗 1.42 L，最低留 1 L，避免除零
        if s.fuel_level is not None:
            s.fuel_level = round(max(1.0, s.fuel_level - 1.42 * elapsed), 2)
            if s.fuel_per_lap:
                s.laps_of_fuel = round(s.fuel_level / s.fuel_per_lap, 1)
        if s.laps_remain is not None:
            s.laps_remain = max(0, s.laps_remain - elapsed)


class IrsdkTelemetry:
    """读 iRacing SDK 共享内存。未连上游戏时 connected=False。"""

    def __init__(self):
        self._ir = None
        self._err: str | None = None
        # SplitTimeInfo 是 session YAML（不是 60Hz 变量），读一次按 session 缓存。
        # 用 sentinel 而不是 None 作「未缓存」：session_id 本身可能就是 None。
        self._sector_key: Any = _NOT_SET
        self._sector_starts: tuple[float, ...] = ()
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
            pit_sv_flags=get("PitSvFlags"),
            pit_sv_fuel=get("PitSvFuel"),
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
            car_classes=get('CarIdxClass'),
        )
        weekend=get('WeekendInfo',{}) or {}
        limit=re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(kph|km/h|mph)\s*',str(weekend.get('TrackPitSpeedLimit','')),flags=re.I)
        if limit:s.pit_speed_limit_kph=float(limit[1])*(1.609344 if limit[2].lower()=='mph' else 1)
        turns=weekend.get('TrackNumTurns')
        if isinstance(turns,int) and not isinstance(turns,bool) and 0<turns<500:
            s.track_num_turns=turns
        # 分段边界：读失败静默留空。分段计时是增强能力，不能反过来
        # 让整帧主遥测（油量/圈速/前后车）一起失败。
        try:
            s.sector_starts=self._session_sector_starts(ir,s.session_id)
        except Exception:      # noqa: BLE001
            s.sector_starts=()
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
        # iRacing 在部分 AI/测试会话里 CarIdxPosition 只给玩家 1，其余为 0，
        # 但 SessionInfo.ResultsPositions 有完整名次/圈速。用结果页补缺，
        # 否则「第一名最快圈」会选中玩家自己，前后车也会找不到目标。
        try:
            self._apply_session_results(s, ir)
        except Exception:  # noqa: BLE001
            pass
        if s.laps_remain is not None and not 0 <= s.laps_remain <= 9999:
            s.laps_remain = None
        for name in ("lap_last", "lap_best", "position",'class_position'):
            if getattr(s, name) is not None and getattr(s, name) <= 0:
                setattr(s, name, None)
        self._err = None
        return s

    @staticmethod
    def _apply_session_results(s: Snapshot, ir) -> None:
        """用 SessionInfo.ResultsPositions 补 CarIdx* 的空洞。

        真实会话里遇到过：CarIdxPosition 只有玩家是 1，其余 20 辆车全是 0，
        但结果页有完整 Position/FastestTime/LastTime/LapsComplete。只补缺失值，
        不覆盖 SDK 已经给出的有效值；没有结果页时保持原样。
        """
        sessions = (ir["SessionInfo"] or {}).get("Sessions") or []
        want = ir["SessionNum"]
        row = next((r for r in sessions if r.get("SessionNum") == want),
                   sessions[-1] if sessions else None)
        results = (row or {}).get("ResultsPositions") or []
        if not results:
            return
        n = max(len(s.car_lap_dist or []),
                max((int(r.get("CarIdx", -1)) + 1 for r in results
                     if isinstance(r.get("CarIdx"), int)), default=0))

        def widen(raw):
            values = list(raw or [])
            if len(values) < n:
                values += [None] * (n - len(values))
            return values

        positions = widen(s.car_positions)
        class_positions = widen(s.car_class_positions)
        last_laps = widen(s.car_last_laps)
        best_laps = widen(s.car_best_laps)
        completed = widen(s.car_completed_laps)
        result_positions = [
            (int(r["CarIdx"]), int(r["Position"]))
            for r in results
            if isinstance(r, dict) and isinstance(r.get("CarIdx"), int)
            and not isinstance(r.get("CarIdx"), bool)
            and isinstance(r.get("Position"), int)
            and not isinstance(r.get("Position"), bool) and r["Position"] > 0
        ]
        # CarIdxPosition 在该会话里只有玩家有值（其余全 0），此时以结果页
        # 的完整名次为准；正常会话 CarIdxPosition 有效时只补缺，不覆盖。
        if len(result_positions) >= 2 and sum(1 for p in positions if _positive(p)) < 2:
            positions = [0] * n
            for idx, pos in result_positions:
                if 0 <= idx < n:
                    positions[idx] = pos
        got_positions = 0
        for result in results:
            if not isinstance(result, dict):
                continue
            idx = result.get("CarIdx")
            if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < n:
                continue
            pos = result.get("Position")
            if isinstance(pos, int) and not isinstance(pos, bool) and pos > 0:
                if not _positive(positions[idx]):
                    positions[idx] = pos
                got_positions += 1
            cpos = result.get("ClassPosition")
            if isinstance(cpos, int) and not isinstance(cpos, bool) and cpos > 0 \
                    and not _positive(class_positions[idx]):
                class_positions[idx] = cpos
            fast = result.get("FastestTime")
            if _positive(fast) and not _positive(best_laps[idx]):
                best_laps[idx] = fast
            last = result.get("LastTime")
            if _positive(last) and not _positive(last_laps[idx]):
                last_laps[idx] = last
            laps = result.get("LapsComplete")
            if isinstance(laps, int) and not isinstance(laps, bool) and laps >= 0 \
                    and not _nonneg(completed[idx]):
                completed[idx] = laps
        if got_positions:
            s.car_positions = positions
            s.car_class_positions = class_positions
            s.car_last_laps = last_laps
            s.car_best_laps = best_laps
            s.car_completed_laps = completed
            # field_size 以结果页的有效名次为准；pace car 不应算入。
            pace = set(s.pace_car_indices or [])
            s.field_size = len([
                r for r in results
                if isinstance(r.get("Position"), int) and r["Position"] > 0
                and r.get("CarIdx") not in pace
            ]) or s.field_size

    def _session_sector_starts(self, ir, session_key) -> tuple[float, ...]:
        """读 SplitTimeInfo.Sectors[].SectorStartPct，按 session 缓存。

        为什么按 session 缓存：这是 session YAML（不是 60Hz 变量），
        每帧都深拷一份没有意义；但换赛道/换场次时边界会变，所以用
        session_id 当缓存键，键一变就重读。

        读失败（键缺失/旧版 SDK/结构不认识）返回空 tuple 或上一次同
        session 的缓存，**绝不抛异常**：分段计时是附加能力，不能让一次
        会话 YAML 解析失败把主遥测拖死。
        """
        same_session = self._sector_key == session_key
        if same_session and self._sector_starts:
            return self._sector_starts
        try:
            starts = normalize_sector_starts(ir["SplitTimeInfo"])
        except Exception:      # noqa: BLE001
            # 同一 session 里偶发读失败可以继续用已缓存的边界；
            # 换 session 后的失败绝不回退到旧赛道的边界。
            return self._sector_starts if same_session else ()
        if not starts:
            # 键在但结构为空：session YAML 可能还没写完，下一帧再试，不缓存空值
            return self._sector_starts if same_session else ()
        self._sector_key = session_key
        self._sector_starts = starts
        return starts

    def close(self) -> None:
        if self._ir is not None:
            self._ir.shutdown()


def build(cfg: dict, force_sim: bool = False, sim_advance: bool = False):
    """模拟数据必须显式选择；SDK 故障保持未连接，避免比赛中播报假数据。

    `sim_advance` 只在 sim 模式下有意义：让假数据随时间推进圈数，
    便于验证圈记录与依赖「最近几圈」的逻辑。
    """
    provider = "sim" if force_sim else cfg.get("telemetry", {}).get("provider", "irsdk")
    if provider == "sim":
        return SimTelemetry(advance=sim_advance), None
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
