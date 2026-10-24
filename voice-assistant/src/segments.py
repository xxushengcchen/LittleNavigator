"""赛段计时：用 iRacing 的分段边界 + 60Hz 位置采样还原每一段的用时。

iRacing 只提供 `SplitTimeInfo.Sectors[].SectorStartPct`（分段边界），
不提供分段成绩。所以只能自己从 `CarIdxLapDistPct` 的 60Hz 采样里插值出
「跨过边界那一刻的 SessionTime」，再把相邻两次跨界相减得到分段用时。

为什么必须插值、不能「看到跨段就记当前采样时间」：
    60Hz 下边界几乎总落在两次采样之间，直接记当前采样时间最多引入
    一整帧（16.7ms）误差；而用户关心的分段差距常在 0.1~0.3s 量级，
    20% 的误差会让「哪一段慢」直接失去意义。线性插值把误差压到
    「位移在两次采样之间线性变化」这一假设内——正常行驶时成立。

为什么每圈都要拿官方圈速复核：
    插值会受丢帧/跳变/进站影响。只有 |Σ分段 − 官方整圈| < 0.1s 才认为
    这一圈的分段可信；宁可这一圈不出结论，也不把错位的数据讲给车手。

并发模型：只有采样线程调用 `update()` 写入；对外 API 在锁内拷贝出
不可变 tuple / 新 list。读方拿到的都是快照，不需要自己加锁。
"""
from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:                       # 仅类型注解；运行期不 import，避免循环依赖
    from .telemetry import Snapshot


# 分段用时之和与官方整圈时间的最大偏差：超过说明插值或结算错位，整圈作废。
LAP_TIME_TOLERANCE_S = 0.1
# 单圈出界累计（按 SessionTime 计）超过这个秒数，本圈不参与对比。
OFF_TRACK_LIMIT_S = 3.0
# 圈速相对近 LAP_OUTLIER_WINDOW 个有效圈中位数的容差：
# 碰撞/黄旗/失误圈不能当基准，否则「差距」里混进的是事故损失。
LAP_OUTLIER_RATIO = 0.08
LAP_OUTLIER_WINDOW = 3
# 相邻采样位移超过半圈视为跳变（重置/传送/重连），该次位移丢弃。
MAX_STEP_PCT = 0.5
# 位移速率上限（圈百分比/秒）。正常行驶时速率 = 1/圈速，90s 圈约 0.011；
# 拖回维修区/重置的瞬时位移往往不到半圈但速率远超它，单靠半圈阈值抓不到。
# 阈值按「正常速率的 6.4 倍」自适应，避免把压缩时间的模拟圈（lap_seconds
# 很短）误判成传送；拿不到圈速时跳过速率判定，只保留半圈判定。
MAX_RATE_FACTOR = 6.4
# 刹车/浮点噪声导致的圈内位置微后退：这个量级内的负位移当作「没动」，
# 不触发跳变保护（否则每个采样抖动都会把整圈标成 torn）。
NOISE_TOLERANCE_PCT = 0.00005
# 短于这个的采样间隔视为同一 tick 的重复读取：不插值，等真实时间跨度。
MIN_TICK_S = 0.001
# 每辆车保留多少条已完成的分段圈（含无效圈，供内部判据与调试）。
KEEP_LAPS = 8


def _finite(value: Any) -> bool:
    """是不是有限数值。bool 不算；超大 int 转 float 溢出也按非数值处理。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:               # 10**400 这类值不是有效遥测数据
        return False


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if not count:
        return 0.0
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def normalize_sector_starts(raw: Any) -> tuple[float, ...]:
    """把 SplitTimeInfo 结构（或纯 pct 序列）规整成升序边界 tuple。

    规则：只保留有限且落在 [0,1) 的值，去重后升序；0.0 必须存在——
    iRacing 保证首段从 0 开始，但模拟数据/旧会话 YAML 可能漏，缺了它
    第 0 段就永远结算不出来，所以这里补上。

    空/坏结构返回 ``()``，调用方把它当作「本场没有分段信息」：
    分段计时是增强能力，不能反过来让主遥测崩掉。
    """
    if raw is None:
        return ()
    if isinstance(raw, dict):
        rows = raw.get("Sectors") or raw.get("sectors") or []
    else:
        rows = raw
    try:
        candidates = list(rows)
    except TypeError:
        return ()
    values: set[float] = set()
    for row in candidates:
        if isinstance(row, dict):
            pct = row.get("SectorStartPct", row.get("sector_start_pct"))
        else:
            pct = row
        if isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue
        try:
            finite = math.isfinite(pct)
        except OverflowError:
            finite = False
        if not finite or not 0.0 <= pct < 1.0:
            continue
        values.add(float(pct))
    if not values:
        return ()
    values.add(0.0)
    return tuple(sorted(values))


@dataclass(frozen=True)
class SectorLap:
    """一条已完成圈的分段结果。字段全部不可变，读方可安全持有。"""

    lap: int
    sectors: tuple[float | None, ...]     # 长度 = num_sectors；None = 该段没测到
    lap_time: float | None                # 官方圈速（优先）或插值测得的整圈
    valid: bool
    in_pit: bool = False


class _CarState:
    """单车逐帧状态机。只由 SegmentModel.update（采样线程）读写。"""

    __slots__ = ("last_pct", "last_t", "lap", "lap_start_t", "sector_start_t",
                 "sectors", "in_pit", "off_track_s", "torn", "laps",
                 "best_sectors", "pending")

    def __init__(self, num_sectors: int):
        self.last_pct: float | None = None
        self.last_t: float | None = None
        # 当前圈号：优先用官方圈号，官方缺失时靠圈末回绕自增
        self.lap: int | None = None
        # 本圈起点（跨 0.0 的插值时刻）；None = 起跑时就在圈中，本圈没有完整起点
        self.lap_start_t: float | None = None
        # 当前段起点（上一次跨界时刻；圈首时等于 lap_start_t）
        self.sector_start_t: float | None = None
        self.sectors: list[float | None] = [None] * num_sectors
        self.in_pit = False
        self.off_track_s = 0.0
        # 观测到跳变（重置/传送/重连）：本圈分段已不可信，结算时作废
        self.torn = False
        # iRacing 在跨线当帧可能还没更新 LapLastLapTime；先存下本圈，
        # 下一帧拿到官方圈速再结算，不能当场作废。
        self.pending: dict | None = None
        self.laps: deque[SectorLap] = deque(maxlen=KEEP_LAPS)
        # 本场最佳分段（只统计有效圈 = clean best；借鉴 irdashies 的 session best）
        self.best_sectors: list[float | None] = [None] * num_sectors


class SegmentModel:
    """分段计时模型：喂 Snapshot，产出每车每圈的分段用时。

    传入的快照只需带这些字段（`Snapshot` 已全部提供）：
        connected / in_garage / session_id / session_time / t / lap /
        car_idx / car_lap_dist / car_completed_laps / car_last_laps /
        car_in_pits / car_surfaces / on_pit_road / on_track_player /
        gap_ahead_idx / gap_behind_idx / car_classes / sector_starts

    数据不足时所有 API 返回空结果（空 list / 全 None tuple / None），
    绝不猜、也绝不抛异常给主遥测循环。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._starts: tuple[float, ...] = ()
        self._cars: dict[int, _CarState] = {}
        self._player_idx: int | None = None
        self._session_id: Any = None
        self._clock: float | None = None
        self._gap_ahead: int | None = None
        self._gap_behind: int | None = None
        self._car_classes: list | None = None
        self._car_completed: list | None = None

    # ------------------------------------------------------------------ 状态
    @property
    def num_sectors(self) -> int:
        """本场分段数；没有分段信息时为 0（不要假设一定是 3 段）。"""
        with self._lock:
            return len(self._starts)

    def reset(self) -> None:
        """清空全部状态（换会话/断开时由 DerivedTracker 调用）。

        连边界一起清：下一帧快照会重新提供 sector_starts，这样「换赛道后
        先 reset 再喂数据」不会拿旧赛道的边界去解释新位置。
        """
        with self._lock:
            self._starts = ()
            self._cars.clear()
            self._player_idx = None
            self._session_id = None
            self._clock = None
            self._gap_ahead = self._gap_behind = None
            self._car_classes = self._car_completed = None

    # ------------------------------------------------------------------ 写入
    def update(self, snapshot: "Snapshot") -> None:
        """喂一帧快照。无分段信息 / 无位置数组 / 不在世界时静默跳过。"""
        starts = normalize_sector_starts(getattr(snapshot, "sector_starts", None))
        with self._lock:
            if not bool(getattr(snapshot, "connected", False)) or \
                    bool(getattr(snapshot, "in_garage", False)):
                self._drop_open_laps()
                self._session_id = getattr(snapshot, "session_id", None)
                return
            if starts != self._starts:
                # 分段边界变了（换赛道/换会话）：段号全部失效，旧状态只能整份丢掉
                self._starts = starts
                self._cars.clear()
            if not starts:
                return
            clock = getattr(snapshot, "session_time", None)
            if not _finite(clock):
                # 模拟模式/单测可能只给墙钟；两种时钟都单调递增，可互相替代
                clock = getattr(snapshot, "t", None)
            if not _finite(clock):
                return
            session_id = getattr(snapshot, "session_id", None)
            if self._session_id is not None and session_id != self._session_id:
                self._cars.clear()
            if self._clock is not None and clock < self._clock:
                self._cars.clear()      # 会话时间回退 = 重新开局
            self._session_id = session_id
            self._clock = float(clock)
            self._player_idx = getattr(snapshot, "car_idx", None)
            # 前后车候选直接沿用 DerivedTracker 已经算好的邻车索引：
            # 那边已经排除了维修区/出界/安全车，这里不重复一份选车逻辑。
            self._gap_ahead = getattr(snapshot, "gap_ahead_idx", None)
            self._gap_behind = getattr(snapshot, "gap_behind_idx", None)
            self._car_classes = getattr(snapshot, "car_classes", None)
            self._car_completed = getattr(snapshot, "car_completed_laps", None)
            dist = getattr(snapshot, "car_lap_dist", None)
            if not dist:
                return
            for index, pct in enumerate(dist):
                state = self._cars.get(index)
                if not _finite(pct) or not 0.0 <= pct <= 1.0:
                    # 不在世界/数据无效：清掉上一次位置，恢复后重新起步，
                    # 否则恢复瞬间会算出一个横跨全场的假插值。
                    if state is not None:
                        state.last_pct = None
                        state.last_t = self._clock
                    continue
                self._track_car(index, state, float(pct), snapshot, starts)

    # ------------------------------------------------------------------ 内部
    def _flush_pending(self, index: int, state: "_CarState",
                       snapshot: "Snapshot") -> None:
        """官方圈速晚一帧到达时，补结算上一圈。"""
        pending = state.pending
        if pending is None:
            return
        reported = self._reported_lap_time(snapshot, index)
        if reported is None:
            return
        # LapLastLapTime 可能还停在上上圈： Σ 对不上时先别结算，再等一帧。
        measured = (pending["t_end"] - pending["start"]
                    if pending.get("start") is not None else None)
        if (measured is not None
                and abs(measured - reported) > LAP_TIME_TOLERANCE_S):
            pending["tries"] = pending.get("tries", 0) + 1
            if pending["tries"] <= 120:      # 约 2 秒@60Hz
                return
            # 官方值一直对不上：放弃这条，宁可不报也不拿错圈速自证。
            state.pending = None
            return
        state.pending = None
        live = (
            state.lap, state.lap_start_t, state.sector_start_t,
            state.sectors, state.in_pit, state.off_track_s, state.torn,
        )
        try:
            state.lap = pending["lap"]
            state.lap_start_t = pending["start"]
            state.sectors = list(pending["sectors"])
            state.in_pit = pending["in_pit"]
            state.off_track_s = pending["off_track_s"]
            state.torn = pending["torn"]
            self._settle_lap(state, index, pending["t_end"], snapshot)
        finally:
            (state.lap, state.lap_start_t, state.sector_start_t,
             state.sectors, state.in_pit, state.off_track_s,
             state.torn) = live

    def _track_car(self, index: int, state: _CarState | None, pct: float,
                   snapshot: "Snapshot", starts: tuple[float, ...]) -> None:
        if state is None:
            state = _CarState(len(starts))
            self._cars[index] = state
        # 上一圈跨线时 iRacing 的 LapLastLapTime 可能还没更新；本帧再补。
        self._flush_pending(index, state, snapshot)
        clock = self._clock if self._clock is not None else 0.0
        reported = self._reported_lap(snapshot, index)
        in_pit, off_track = self._car_flags(snapshot, index)

        previous_t = state.last_t
        raw_dt = clock - previous_t if previous_t is not None else 0.0
        dt = raw_dt if 0.0 <= raw_dt <= 5.0 else 0.0
        if in_pit:
            state.in_pit = True
        if off_track:
            state.off_track_s += dt

        # 圈号：官方值跳变（跳圈/重连）说明当前未完成圈不可信
        if state.lap is None:
            state.lap = reported
        elif reported is not None and (reported < state.lap
                                       or reported > state.lap + 1):
            self._discard_partial(state)
            state.lap = reported

        previous_pct = state.last_pct
        if previous_pct is None:
            state.last_pct, state.last_t = pct, clock
            return
        if raw_dt <= MIN_TICK_S:
            # 同一 tick 的重复读取：会话时间几乎没前进。不插值、也不更新基准点，
            # 等真实 tick 到了再按完整时间跨度回推跨界（否则会插出零时长分段）。
            return
        raw_delta = pct - previous_pct
        if raw_delta > 0.0:
            distance = raw_delta
        elif raw_delta > -NOISE_TOLERANCE_PCT:
            # 刹车/浮点抖动导致的微小后退：当作没动。不更新基准位置——
            # 把噪声吃进 last_pct 会慢慢累积成假位移。
            state.last_t = clock
            return
        else:
            # 负位移：大多是圈末回绕（-0.99 → +0.01）；倒车/传送会得到
            # 接近 1 的 d，交给下面的 >0.5 判定拦掉。
            distance = raw_delta % 1.0
        if distance <= 0.0:
            state.last_t = clock
            return
        # 跳变防护三条（对照 irdashies SectorTimingProcessor）：
        #   1) 正向位移超过半圈；
        #   2) 负向位移的噪声容差已在上面处理，超出容差且回绕后仍过半圈即跳变；
        #   3) 位移速率远超正常行驶（按该车圈速自适应，短 tick 已排除）。
        rate = distance / raw_dt if raw_dt > 0 else 0.0
        rate_limit = self._rate_limit(snapshot, index)
        teleported = (distance > MAX_STEP_PCT
                      or (rate_limit is not None and rate > rate_limit))
        if teleported:
            state.torn = True
            state.last_pct, state.last_t = pct, clock
            return

        previous_clock = state.last_t if state.last_t is not None else clock
        span = clock - previous_clock
        events: list[tuple[float, str, int]] = []
        for sector_index in range(1, len(starts)):
            # d = (pct - prev_pct) % 1，这里对每个边界同样按模 1 取位移：
            # 圈末回绕时 (b - prev_pct) 是负的，必须加上 1 才是真实的位移。
            delta = (starts[sector_index] - previous_pct) % 1.0
            if 0.0 < delta <= distance:
                events.append((delta, "sector", sector_index))
        # 圈末：1.0 相当于下一圈的 0.0，单独作为「结算」事件
        lap_delta = (1.0 - previous_pct) % 1.0
        if 0.0 < lap_delta <= distance:
            events.append((lap_delta, "lap", 0))
        events.sort(key=lambda item: item[0])

        for delta, kind, sector_index in events:
            # 线性插值：位移在两次采样之间均匀发生，按比例回推跨界时刻
            t_cross = previous_clock + (delta / distance) * span
            if kind == "sector":
                if state.sector_start_t is not None:
                    state.sectors[sector_index - 1] = \
                        t_cross - state.sector_start_t
                # 即使本圈起点未知，也从这一刻起重新起算，后面的段仍可测
                state.sector_start_t = t_cross
            else:
                # 最后一段止于圈末：它没有「下一次跨界」，必须在这里补上，
                # 否则每圈的末段永远是 None，Σ 校验永远不过、整圈全废。
                if state.sector_start_t is not None:
                    state.sectors[len(starts) - 1] = \
                        t_cross - state.sector_start_t
                reported = self._reported_lap_time(snapshot, index)
                measured = (t_cross - state.lap_start_t
                            if state.lap_start_t is not None else None)
                stale = (reported is not None and measured is not None
                         and abs(measured - reported) > LAP_TIME_TOLERANCE_S)
                if state.lap_start_t is not None and (reported is None or stale):
                    # 跨线当帧官方圈速还没刷新/还是上一圈：先暂存，下一帧补结算。
                    # 没有圈首的半圈直接走 _settle_lap 作废，不暂存。
                    if state.pending is None:
                        state.pending = {
                            "lap": state.lap,
                            "start": state.lap_start_t,
                            "sectors": tuple(state.sectors),
                            "t_end": t_cross,
                            "in_pit": state.in_pit,
                            "off_track_s": state.off_track_s,
                            "torn": state.torn,
                            "tries": 0,
                        }
                else:
                    self._settle_lap(state, index, t_cross, snapshot)
                state.lap_start_t = t_cross
                state.sector_start_t = t_cross
                state.sectors = [None] * len(starts)
                state.in_pit = False
                state.off_track_s = 0.0
                state.torn = False
                if state.lap is not None:
                    state.lap += 1
        state.last_pct, state.last_t = pct, clock

    def _settle_lap(self, state: _CarState, index: int, t_end: float,
                    snapshot: "Snapshot") -> None:
        """圈末结算：算整圈、做 Σ 校验与各类无效过滤，然后入库。"""
        num = len(self._starts)
        lap_number = state.lap if state.lap is not None else -1
        in_pit = state.in_pit
        if state.lap_start_t is None:
            # 开始记录时人已经在圈中：这一段圈的起点不可知，整圈作废
            state.laps.append(SectorLap(
                lap=lap_number, sectors=tuple([None] * num),
                lap_time=None, valid=False, in_pit=in_pit))
            return
        measured = t_end - state.lap_start_t
        sectors = tuple(state.sectors)
        complete = (len(sectors) == num
                    and all(value is not None and value > 0
                            for value in sectors))
        reported = self._reported_lap_time(snapshot, index)
        lap_time = reported if reported is not None else (
            measured if measured > 0 else None)
        valid = (complete and not state.torn and not in_pit
                 and state.off_track_s <= OFF_TRACK_LIMIT_S)
        if valid and reported is None:
            # 没有官方整圈时间就做不了 Σ 校验；不拿自己算的时间自证
            valid = False
        if valid:
            total = math.fsum(sectors)          # type: ignore[arg-type]
            valid = abs(total - reported) < LAP_TIME_TOLERANCE_S
        if valid and lap_time is not None and self._is_outlier(state, lap_time):
            valid = False
        if valid:
            # 本场最佳分段只在有效圈里更新：无效圈（进站/出界）的「快」
            # 往往来自抄近道或数据错位，不能当标杆。
            for sector_index, value in enumerate(sectors):
                if value is None:
                    continue
                current = state.best_sectors[sector_index]
                if current is None or value < current:
                    state.best_sectors[sector_index] = value
        state.laps.append(SectorLap(lap=lap_number, sectors=sectors,
                                    lap_time=lap_time, valid=valid,
                                    in_pit=in_pit))

    @staticmethod
    def _is_outlier(state: _CarState, lap_time: float) -> bool:
        """圈速偏离近几个有效圈中位数 ±8%：事故/黄旗圈不能当对比基准。"""
        history = [lap.lap_time for lap in state.laps
                   if lap.valid and lap.lap_time is not None]
        history = history[-LAP_OUTLIER_WINDOW:]
        if not history:
            return False
        reference = _median(history)
        return reference > 0 and \
            abs(lap_time - reference) > LAP_OUTLIER_RATIO * reference

    def _discard_partial(self, state: _CarState) -> None:
        state.lap_start_t = None
        state.sector_start_t = None
        state.sectors = [None] * len(self._starts)
        state.in_pit = False
        state.off_track_s = 0.0
        state.torn = False

    def _drop_open_laps(self) -> None:
        for state in self._cars.values():
            self._discard_partial(state)

    def _reported_lap(self, snapshot: "Snapshot", index: int) -> int | None:
        if self._player_idx is not None and index == self._player_idx:
            lap = getattr(snapshot, "lap", None)
            return lap if _lap_count(lap) else None
        completed = self._car_completed
        if not isinstance(completed, list) or index >= len(completed):
            return None
        value = completed[index]
        return value + 1 if _lap_count(value) else None

    def _reported_lap_time(self, snapshot: "Snapshot", index: int) -> float | None:
        if self._player_idx is not None and index == self._player_idx:
            value = getattr(snapshot, "lap_last", None)
            # 在线/重连场景实测过 lap_last 暂时为 None，但 CarIdxLastLapTime
            # 仍有上一圈成绩。逐车数组是同一份数据，缺主字段时用它兜底，
            # 否则整圈分段会因为拿不到官方圈速而全部作废。
            if not _finite(value) or value <= 0:
                laps = getattr(snapshot, "car_last_laps", None)
                value = laps[index] if isinstance(laps, list) and index < len(laps) else None
        else:
            laps = getattr(snapshot, "car_last_laps", None)
            value = laps[index] if isinstance(laps, list) and index < len(laps) else None
        if not _finite(value) or value <= 0:
            return None
        return float(value)

    def _car_flags(self, snapshot: "Snapshot", index: int) -> tuple[bool, bool]:
        """返回 (本帧是否在维修区, 本帧是否出界/不在世界)。"""
        in_pit = False
        off_track = False
        if self._player_idx is not None and index == self._player_idx:
            if snapshot.on_pit_road:
                in_pit = True
            if getattr(snapshot, "on_track_player", None) is False:
                off_track = True
        pits = getattr(snapshot, "car_in_pits", None)
        if isinstance(pits, list) and index < len(pits) and pits[index]:
            in_pit = True
        surfaces = getattr(snapshot, "car_surfaces", None)
        if isinstance(surfaces, list) and index < len(surfaces):
            surface = surfaces[index]
            # CarIdxTrackSurface: -1 不在世界 / 0 出界 / 1 维修区停车格 /
            # 2 正在进维修区 / 3 在赛道上。其他值按未知处理，不做推断。
            if isinstance(surface, int) and not isinstance(surface, bool):
                if surface in (-1, 0):
                    off_track = True
                elif index != self._player_idx and surface in (1, 2):
                    in_pit = True
        return in_pit, off_track

    def _rate_limit(self, snapshot: "Snapshot", index: int) -> float | None:
        """该车允许的最大位移速率（pct/s）；拿不到可信圈速时返回 None。

        跳变阈值必须随圈速缩放：真实 80s 圈正常速率 0.0125，而压缩到 0.5s
        的模拟圈正常速率就是 2.0——写死 0.08 会把整场模拟遥测判成传送。
        """
        if self._player_idx is not None and index == self._player_idx:
            period = getattr(snapshot, "lap_best", None)
            if not _finite(period) or period <= 0:
                period = getattr(snapshot, "lap_last", None)
        else:
            period = None
            for name in ("car_best_laps", "car_last_laps"):
                laps = getattr(snapshot, name, None)
                if isinstance(laps, list) and index < len(laps):
                    value = laps[index]
                    if _finite(value) and value > 0:
                        period = value
                        break
        if not _finite(period) or period <= 1.0:
            # 1s 以下的圈速只可能来自压缩模拟，真实速率阈值不适用
            return None
        return MAX_RATE_FACTOR / float(period)

    # ------------------------------------------------------------------ 读取
    def car_recent(self, car_idx: int | None, n: int = 3) -> list[SectorLap]:
        """某辆车最近的 n 条「有效」分段圈（最新的在前）。"""
        limit = max(0, int(n))
        with self._lock:
            state = self._cars.get(car_idx) if isinstance(car_idx, int) else None
            if state is None or not limit:
                return []
            return [lap for lap in reversed(state.laps) if lap.valid][:limit]

    def player_recent(self, n: int = 3) -> list[SectorLap]:
        """玩家自己最近的 n 条有效分段圈（最新的在前）。"""
        with self._lock:
            return self.car_recent(self._player_idx, n)

    def sectors_for(self, lap: int) -> tuple[float | None, ...] | None:
        """玩家某一圈的分段用时；该圈无效/未结算时返回 None。

        给 `DerivedTracker.lap_records` 用：圈记录只挂真实有效的分段。
        """
        with self._lock:
            state = self._cars.get(self._player_idx) \
                if isinstance(self._player_idx, int) else None
            if state is None:
                return None
            for record in reversed(state.laps):
                if record.lap == lap:
                    return record.sectors if record.valid else None
            return None

    def session_best(self, car_idx: int | None = None) -> tuple[float | None, ...]:
        """本场最佳分段（只来自有效圈 = clean best）；None 表示该段还没跑过。

        借鉴 irdashies 的 session best：给「第 N 段最佳是多少」这类问句用。
        注意这里刻意不收无效圈：出界/进站圈的「最快段」多半是抄近道或
        数据错位，拿它当标杆会引出误导性的「你还差 X 秒」。
        """
        with self._lock:
            num = len(self._starts)
            target = self._player_idx if car_idx is None else car_idx
            state = self._cars.get(target) if isinstance(target, int) else None
            if state is None:
                return tuple([None] * num)
            return tuple(state.best_sectors)

    def sector_medians(self, car_idx: int | None = None,
                       n: int = 3) -> tuple[float | None, ...]:
        """按段取最近 n 条有效圈的中位数；None 表示该段没有数据。"""
        with self._lock:
            num = len(self._starts)
            target = self._player_idx if car_idx is None else car_idx
            laps = self.car_recent(target, n)
            if not laps:
                return tuple([None] * num)
            result: list[float | None] = []
            for sector in range(num):
                values = [lap.sectors[sector] for lap in laps
                          if sector < len(lap.sectors)
                          and lap.sectors[sector] is not None]
                result.append(_median(values) if values else None)  # type: ignore[arg-type]
            return tuple(result)

    def compare(self, target_idx: int | None, n: int = 3) -> tuple[float | None, ...]:
        """按段算 target − mine（正数 = 对方更慢/自己更快）。"""
        with self._lock:
            num = len(self._starts)
            if target_idx is None:
                return tuple([None] * num)
            mine = self.sector_medians(None, n)
            theirs = self.sector_medians(target_idx, n)
            return tuple(
                None if mine[i] is None or theirs[i] is None
                else theirs[i] - mine[i]         # type: ignore[operator]
                for i in range(num))

    def target_for(self, role: str) -> int | None:
        """可对比的前车/后车索引；不可识别、不同组别或已套圈时返回 None。"""
        with self._lock:
            if not self._starts:
                return None
            if role == "ahead":
                index = self._gap_ahead
            elif role == "behind":
                index = self._gap_behind
            else:
                return None
            player = self._player_idx
            if not _car_index(index) or not _car_index(player) or index == player:
                return None
            classes = self._car_classes
            if not isinstance(classes, list) or index >= len(classes) \
                    or player >= len(classes):
                return None
            mine_class, their_class = classes[player], classes[index]
            if not _car_index(mine_class) or not _car_index(their_class):
                return None
            if mine_class != their_class:        # 不同组别速度不可比
                return None
            completed = self._car_completed
            if not isinstance(completed, list) or index >= len(completed) \
                    or player >= len(completed):
                return None
            mine_laps, their_laps = completed[player], completed[index]
            if not _lap_count(mine_laps) or not _lap_count(their_laps):
                return None
            # 完成圈数不同 = 差一圈（套圈/被套圈）：分段差距没有可比性
            return index if mine_laps == their_laps else None


def _car_index(value: Any) -> bool:
    """有效车辆索引：int 且 >=0（bool 不算，-1 表示无数据）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _lap_count(value: Any) -> bool:
    """有效圈数：int 且 >=0（bool 不算，-1 表示无数据）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
