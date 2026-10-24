"""把遥测快照与近期圈史组装成给模型看的紧凑上下文。

两条硬约束决定了这里的形状：

1. **token 有上限**（1500）。模型每次调用都按输入计费，而且越长越慢；
   比赛场景里延迟比全面更重要，所以按问题类型分层给，不是无脑全量。
2. **数字闸只接受原样引用**（见 answer_checks）。模型自己算出来的数会被判
   失败，因此所有需要展示的派生量必须在这里算好、按口语习惯四舍五入，
   并明确告诉模型"只准引用给定数值"。
"""
from __future__ import annotations

import re

from .numbers import seconds_to_spoken
from .query_class import QueryKind
from .telemetry import Snapshot

# 模型可以在回答里引用的字段名。模板校验与数据闸都用它，必须单一来源。
ALLOWED_FIELDS: frozenset[str] = frozenset({
    # now
    "connected", "lap", "lap_completed", "lap_last", "lap_best", "laps_remain",
    "time_remain_s", "position", "class_position", "field_size",
    "fuel_level", "fuel_per_lap", "laps_of_fuel", "speed_kph",
    "air_temp", "track_temp", "track_wetness", "relative_humidity",
    "precip", "declared_wet", "wind_vel",
    "gap_ahead_s", "gap_behind_s", "gap_estimated",
    "on_pit_road", "pits_open", "incident_count",
    "water_temp", "oil_temp", "rpm", "gear",
    "tyre_cold_pressure", "tyre_history", "track_rubber",
    # now 里的口语化时长（v1.5.2）：与数值字段成对出现，模型优先引用它们，
    # 避免把 83.5 秒念成「八十三点五秒」。数值字段保留，不改旧行为。
    "lap_last_spoken", "lap_best_spoken", "time_remain_spoken",
    # derived（预计算值，模型只准引用这些）
    "laps_spare", "delta_to_best", "avg3", "avg5", "trend_per_lap",
    "tyre_temp_delta", "position_change", "gap_ahead_change", "gap_behind_change",
    # derived 里的口语化时长（v1.5.2）
    "avg3_spoken", "avg5_spoken",
    # session
    "total_laps", "flags",
    # opponents 层内的叶子字段（模型会用点路径引用，如 opponents.leader.best_lap）
    "ahead", "behind", "leader",
    "pos", "gap_s", "last_lap", "best_lap", "completed",
    # segments 层（v1.5.4）：分段对比。count/player 是层内结构；
    # target 里的 role/car_idx/sectors/deltas/deltas_spoken 逐段给出；
    # best_loss 是已经算好的「最亏的 2 段」，模型只准引用这些数。
    "segments", "count", "player", "target", "best_loss",
    "role", "car_idx", "sectors", "deltas", "deltas_spoken",
    "sector", "delta_s", "delta_spoken", "lap_time", "lap_time_spoken",
})

# 层名本身也可以被引用：模型说「用了 recent_laps」是诚实的——我们确实给了它。
# 但数据闸会再核对「这一层在本次上下文里是否真的存在」（见 answer_checks），
# 所以把层名放进白名单不等于放行不存在的数据。
LAYER_NAMES: frozenset[str] = frozenset({
    "now", "derived", "session", "recent_laps", "opponents", "segments",
})

ALLOWED_FIELDS = ALLOWED_FIELDS | LAYER_NAMES

# 问题里出现这些词时才附加对手层——它约 120 token，不该为「还有多少油」付这笔钱。
_OPPONENT_HINTS = ("前车", "后车", "前面", "后面", "对手", "第一名", "领跑", "差距", "名次")

# 问题里出现这些词时才附加 segments 层（v1.5.4）。分段数据比对手层更贵
# （逐段数组），只在真的问到「哪一段/丢时间」时才给。
_SECTOR_HINTS = ("哪一段", "哪段", "分段", "赛段", "区间", "丢时间", "丢了时间",
                 "哪里慢", "哪边慢", "慢在哪", "损失时间", "sector",
                 "各段", "每段", "第几段")
# 「第 2 段用了多少」这类点名某一段的问法：数字形式不在子串表里，单独判。
_SECTOR_REF_RE = re.compile(r"第\s*(?:[0-9]+|[一二三四五六七八九十]+)\s*段")


def _asks_sectors(question: str) -> bool:
    q = question or ""
    return any(h in q for h in _SECTOR_HINTS) or bool(_SECTOR_REF_RE.search(q))

# 分段的「损失」判定阈值：小于这个数的段差当噪声，不报。
LOSS_EPS = 0.05


def _numeric(value) -> float | None:
    """只留可用数值；None / NaN / inf 一律当缺失。"""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _round(value, digits: int = 1) -> float | None:
    v = _numeric(value)
    return None if v is None else round(v, digits)


def _now_layer(s: Snapshot) -> dict:
    """当前状态。缺字段直接省略，不要塞 None 让模型以为「值是 null」。"""
    raw = {
        "connected": bool(s.connected),
        "lap": s.lap, "lap_completed": s.lap_completed,
        "lap_last": _round(s.lap_last, 3), "lap_best": _round(s.lap_best, 3),
        "laps_remain": s.laps_remain, "time_remain_s": _round(s.time_remain_s, 0),
        "position": s.position, "class_position": s.class_position,
        "field_size": s.field_size,
        "fuel_level": _round(s.fuel_level, 1),
        "fuel_per_lap": _round(s.fuel_per_lap, 2),
        "laps_of_fuel": _round(s.laps_of_fuel, 1),
        "speed_kph": _round(s.speed_kph, 0),
        "air_temp": _round(s.air_temp, 1), "track_temp": _round(s.track_temp, 1),
        "track_wetness": s.track_wetness,
        "relative_humidity": _round(s.relative_humidity, 2),
        "precip": _round(s.precip, 2), "declared_wet": s.declared_wet,
        "wind_vel": _round(s.wind_vel, 1),
        "gap_ahead_s": _round(s.gap_ahead_s, 2),
        "gap_behind_s": _round(s.gap_behind_s, 2),
        "gap_estimated": bool(s.gap_estimated),
        "on_pit_road": s.on_pit_road, "pits_open": s.pits_open,
        "incident_count": s.incident_count,
        "water_temp": _round(s.water_temp, 1), "oil_temp": _round(s.oil_temp, 1),
        "rpm": s.rpm, "gear": s.gear,
        "track_rubber": s.track_rubber,
    }
    out = {k: v for k, v in raw.items() if v is not None}
    # 口语化时长（v1.5.2）：跟数值字段成对给。模型直接引用这里的字符串，
    # 就不会把 83.5 秒念成「八十三点五秒」；answer_checks 的数值闸靠原字段
    # 照旧核对，这里的字符串只是给播报用。
    for key in ("lap_last", "lap_best"):
        if out.get(key) is not None:
            out[f"{key}_spoken"] = seconds_to_spoken(out[key], 1)
    if out.get("time_remain_s") is not None:
        # 剩余时间是整秒计时（time_remain_s 已按 0 位小数取整），口播不带小数
        out["time_remain_spoken"] = seconds_to_spoken(out["time_remain_s"], 0)
    if s.tyre_history:
        out["tyre_history"] = {
            w: {k: _round(v, 3) if k != "session_time" else v
                for k, v in vals.items() if v is not None}
            for w, vals in s.tyre_history.items()
        }
    if s.tyre_cold_pressure:
        out["tyre_cold_pressure"] = dict(s.tyre_cold_pressure)
    return out


def _derived_layer(s: Snapshot, laps: list[dict]) -> dict:
    """预计算的展示值。数字闸只认这些，所以必须算好并四舍五入。"""
    out: dict = {}

    if _numeric(s.laps_of_fuel) is not None:
        if _numeric(s.laps_remain) is not None:
            out["laps_spare"] = round(s.laps_of_fuel - s.laps_remain, 1)

    if _numeric(s.lap_last) is not None and _numeric(s.lap_best) is not None:
        out["delta_to_best"] = round(s.lap_last - s.lap_best, 1)

    times = [l.get("lap_time") for l in laps if _numeric(l.get("lap_time")) is not None]
    if times:
        recent = times[-3:]
        out["avg3"] = round(sum(recent) / len(recent), 1)
        if len(times) >= 2:
            # 每圈平均变化量：正值 = 在变慢（圈速变大）
            deltas = [times[i] - times[i - 1] for i in range(1, len(times))]
            out["trend_per_lap"] = round(sum(deltas) / len(deltas), 1)
    if len(times) >= 5:
        last5 = times[-5:]
        out["avg5"] = round(sum(last5) / len(last5), 1)

    # 胎温变化：最近一圈相对更早一圈，逐轮
    if len(laps) >= 2:
        cur, prev = laps[-1].get("tyre") or {}, laps[-2].get("tyre") or {}
        delta = {}
        for wheel in ("LF", "RF", "LR", "RR"):
            a = _numeric((cur.get(wheel) or {}).get("temp"))
            b = _numeric((prev.get(wheel) or {}).get("temp"))
            if a is not None and b is not None:
                delta[wheel] = round(a - b, 1)
        if delta:
            out["tyre_temp_delta"] = delta

    # 名次变化：正值 = 掉名次
    if len(laps) >= 2:
        a = _numeric(laps[-1].get("position"))
        b = _numeric(laps[-2].get("position"))
        if a is not None and b is not None:
            out["position_change"] = int(a - b)

    if len(laps) >= 2:
        a = _numeric(laps[-1].get("gap_ahead_s"))
        b = _numeric(laps[-2].get("gap_ahead_s"))
        if a is not None and b is not None:
            out["gap_ahead_change"] = round(a - b, 2)
        a = _numeric(laps[-1].get("gap_behind_s"))
        b = _numeric(laps[-2].get("gap_behind_s"))
        if a is not None and b is not None:
            out["gap_behind_change"] = round(a - b, 2)

    # 口语化时长（v1.5.2）：avg3 / avg5 同样给出「X分Y.Z秒」，避免模型自己换算。
    for key in ("avg3", "avg5"):
        if out.get(key) is not None:
            out[f"{key}_spoken"] = seconds_to_spoken(out[key], 1)

    return out


def _session_layer(s: Snapshot) -> dict:
    out: dict = {}
    if s.laps_remain is not None and s.lap is not None:
        out["total_laps"] = s.lap + s.laps_remain - 1
    flags = list(s.flags)
    if flags:
        out["flags"] = flags
    return out


def _opponents_layer(s: Snapshot) -> dict:
    """只带前后邻车 + 领跑者共 3 台，约 120 token。"""
    pos = s.car_positions or []
    last = s.car_last_laps or []
    best = s.car_best_laps or []
    done = s.car_completed_laps or []

    def one(idx, gap_key: str | None = None) -> dict | None:
        if idx is None or not (0 <= idx < len(pos)):
            return None
        row = {"pos": pos[idx]}
        if gap_key is not None:
            gap = _numeric(getattr(s, gap_key, None))
            if gap is not None:
                row["gap_s"] = round(gap, 2)
        if idx < len(last) and _numeric(last[idx]) is not None:
            row["last_lap"] = round(last[idx], 3)
        if idx < len(best) and _numeric(best[idx]) is not None:
            row["best_lap"] = round(best[idx], 3)
        if idx < len(done) and done[idx] is not None:
            row["completed"] = done[idx]
        return row

    out: dict = {}
    ahead = one(s.gap_ahead_idx, "gap_ahead_s")
    if ahead:
        out["ahead"] = ahead
    behind = one(s.gap_behind_idx, "gap_behind_s")
    if behind:
        out["behind"] = behind
    leaders = [i for i, p in enumerate(pos) if p == 1 and i not in s.pace_car_indices]
    if len(leaders) == 1:
        leader = one(leaders[0])
        if leader:
            out["leader"] = leader
    if s.field_size:
        out["field_size"] = s.field_size
    return out


def sector_role(question: str) -> str | None:
    """问句里点名的是前车还是后车；没点名返回 None（由调用方决定默认）。"""
    q = question or ""
    if any(w in q for w in ("后车", "后面", "后方", "后車", "後車")):
        return "behind"
    if any(w in q for w in ("前车", "前面", "前方", "前車")):
        return "ahead"
    return None


def sectors_for(snap: Snapshot, segments=None) -> object | None:
    """取分段模型：显式传入优先，其次取快照上的只读引用。

    第二优先是给 AI 上下文用的——`ai_pipeline` 只把 Snapshot 传进来，
    引擎在 `Assistant.snapshot()` 里把 `DerivedTracker.segments` 挂上去，
    这里就不必改 AI 流水线的调用签名。
    """
    if segments is not None:
        return segments
    return getattr(snap, "segments", None)


class SectorView:
    """`SegmentModel`（T1 的 src/segments.py）的只读适配层。

    为什么要有这一层：分段模型还没落地时（或版本对不上、方法缺失），
    调用方需要「明确知道没有数据」并拒答，而不是 AttributeError 把整条
    回答链路带崩。所有取值失败一律返回 None/空，`available` 为假。
    """

    def __init__(self, model=None):
        self._model = model
        try:
            count = int(getattr(model, "num_sectors", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        self.num_sectors = max(0, count)

    @property
    def available(self) -> bool:
        return self._model is not None and self.num_sectors >= 2

    # ---- 内部取值 -------------------------------------------------
    def _call(self, name: str, *args):
        fn = getattr(self._model, name, None)
        if not callable(fn):
            return None
        try:
            return fn(*args)
        except Exception:      # noqa: BLE001 - 分段是展示层，坏数据不该炸链路
            return None

    def _series(self, raw) -> list[float | None]:
        """把 T1 返回的 tuple 收敛成长度 = num_sectors 的列表。"""
        if not isinstance(raw, (list, tuple)):
            return [None] * self.num_sectors
        values = [_round(v, 2) for v in list(raw)[:self.num_sectors]]
        values += [None] * (self.num_sectors - len(values))
        return values

    def _sectors_of(self, lap) -> list[float | None] | None:
        raw = getattr(lap, "sectors", None)
        if not isinstance(raw, (list, tuple)):
            return None
        return self._series(raw)

    def _use_lap(self, lap) -> bool:
        if getattr(lap, "valid", True) is False:
            return False
        if bool(getattr(lap, "in_pit", False)):
            return False
        sectors = self._sectors_of(lap)
        return sectors is not None and any(s is not None for s in sectors)

    def _laps(self, name: str, n: int = 3) -> list:
        raw = self._call(name, n) or []
        if not isinstance(raw, (list, tuple)):
            return []
        laps = [lap for lap in raw if self._use_lap(lap)]
        # 统一按圈号升序：「最近」= 末尾。T1 若按「最近在前」返回也能对齐。
        if laps and all(isinstance(getattr(lap, "lap", None), int) for lap in laps):
            laps = sorted(laps, key=lambda lap: lap.lap)
        return laps[-n:]

    # ---- 对外视图（AI 上下文）-------------------------------------
    def player_laps(self, n: int = 3) -> list[dict]:
        if not self.available:
            return []
        out: list[dict] = []
        for lap in self._laps("player_recent", n):
            row: dict = {"lap": getattr(lap, "lap", None),
                         "sectors": self._sectors_of(lap)}
            lap_time = _numeric(getattr(lap, "lap_time", None))
            if lap_time is not None:
                row["lap_time"] = round(lap_time, 3)
                row["lap_time_spoken"] = seconds_to_spoken(round(lap_time, 3), 1)
            out.append({k: v for k, v in row.items() if v is not None})
        return out

    def target(self, role: str) -> dict | None:
        """对比车（前车/后车）的逐段数值。没有可识别目标时返回 None。"""
        if not self.available:
            return None
        idx = self._call("target_for", role)
        if isinstance(idx, bool) or not isinstance(idx, int):
            return None
        deltas = self._series(self._call("compare", idx, 3))
        sectors = self._series(self._call("sector_medians", idx, 3))
        if all(v is None for v in deltas) and all(v is None for v in sectors):
            return None
        return {"role": role, "car_idx": idx, "sectors": sectors,
                "deltas": deltas,
                "deltas_spoken": [self._delta_spoken(d) for d in deltas]}

    def pick_target(self, question: str = "") -> dict | None:
        """按问句点名的方向找对比车；没点名就前车优先、后车兜底。"""
        role = sector_role(question)
        if role is not None:
            return self.target(role)
        return self.target("ahead") or self.target("behind")

    def gains(self, target: dict | None, limit: int = 2) -> list[dict]:
        """对比车里「我比它快」的段（delta > 0），按赚得多的排前。"""
        if not isinstance(target, dict):
            return []
        out: list[dict] = []
        for i, delta in enumerate(target.get("deltas") or []):
            if delta is None or delta < LOSS_EPS:
                continue
            out.append({"sector": i + 1,
                        "delta_s": round(delta, 2),
                        "delta_spoken": seconds_to_spoken(round(delta, 2), 2)})
        out.sort(key=lambda row: row["delta_s"], reverse=True)
        return out[:max(0, limit)]

    def player_medians(self, n: int = 3) -> list[float | None]:
        """自己最近 n 个有效圈的逐段中位数（None = 该段没有样本）。"""
        if not self.available:
            return [None] * self.num_sectors
        return self._series(self._call("sector_medians", None, n))

    def best_lap(self) -> dict | None:
        """最近有效圈里圈速最快的那条（没有圈速就返回 None）。"""
        laps = [lap for lap in self.player_laps(3)
                if _numeric(lap.get("lap_time")) is not None]
        if not laps:
            return None
        return min(laps, key=lambda lap: lap["lap_time"])

    def losses_vs_best(self, limit: int = 2) -> list[dict]:
        """没有可对比的前后车时：最近分段中位数 vs 个人最佳圈的逐段差。

        这是**自己跟自己**的观察值，仍然只陈述「哪段比最佳圈慢多少」，
        不冒充对手数据、不归因。
        """
        best = self.best_lap()
        if best is None:
            return []
        medians = self.player_medians(3)
        sectors = best.get("sectors") or []
        out: list[dict] = []
        for i, median in enumerate(medians):
            target = sectors[i] if i < len(sectors) else None
            if median is None or target is None:
                continue
            loss = median - target          # 正值 = 比最佳圈慢
            if loss < LOSS_EPS:
                continue
            out.append({"sector": i + 1,
                        "delta_s": round(loss, 2),
                        "delta_spoken": seconds_to_spoken(round(loss, 2), 2)})
        out.sort(key=lambda row: row["delta_s"], reverse=True)
        return out[:max(0, limit)]

    def losses(self, target: dict | None, limit: int = 2) -> list[dict]:
        """从对比结果里挑「我比它慢」的段，按亏得多的排前，最多 limit 条。"""
        if not isinstance(target, dict):
            return []
        out: list[dict] = []
        for i, delta in enumerate(target.get("deltas") or []):
            if delta is None:
                continue
            loss = -delta                       # deltas = target - mine
            if loss < LOSS_EPS:
                continue
            out.append({"sector": i + 1,
                        "delta_s": round(loss, 2),
                        "delta_spoken": seconds_to_spoken(round(loss, 2), 2)})
        out.sort(key=lambda row: row["delta_s"], reverse=True)
        return out[:max(0, limit)]

    @staticmethod
    def _delta_spoken(delta: float | None) -> str | None:
        if delta is None:
            return None
        if abs(delta) < 0.005:
            return "持平"
        if delta < 0:                            # 对方更快 = 我慢
            return f"慢{abs(delta):.2f}秒"
        return f"快{abs(delta):.2f}秒"

    def layer(self, question: str = "") -> dict | None:
        """组装 AI 上下文的 segments 层；没有有效分段数据时返回 None。"""
        if not self.available:
            return None
        player = self.player_laps(3)
        if not player:
            return None
        layer: dict = {"count": self.num_sectors, "player": player}
        target = self.pick_target(question)
        if target is not None:
            layer["target"] = target
            best = self.losses(target)
            if best:
                layer["best_loss"] = best
        return layer


def build(snap: Snapshot, laps: list[dict], kind: QueryKind, question: str,
          segments=None) -> dict:
    """按问题类型组装上下文。

    简单问题只给 now + derived（约 300 token）；分析型再带最近 5 圈；
    问到分段/丢时间时才附加 segments 层（v1.5.4）。
    """
    ctx: dict = {
        "now": _now_layer(snap),
        "derived": _derived_layer(snap, laps or []),
        "session": _session_layer(snap),
    }

    if kind in (QueryKind.ANALYSIS, QueryKind.UNKNOWN) and laps:
        ctx["recent_laps"] = [dict(l) for l in laps[-5:]]

    q = question or ""
    if any(h in q for h in _OPPONENT_HINTS):
        opp = _opponents_layer(snap)
        if opp:
            ctx["opponents"] = opp

    if _asks_sectors(q):
        seg = SectorView(sectors_for(snap, segments)).layer(q)
        if seg:
            ctx["segments"] = seg

    return ctx


def estimate_tokens(ctx: dict) -> int:
    """粗估 token 数：中文按 1 字 1 token、ASCII 按 4 字符 1 token 近似。

    刻意不引入 tokenizer 依赖——估算只用于裁剪决策，差几成不影响正确性。
    """
    import json

    text = json.dumps(ctx, ensure_ascii=False, default=str)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + other // 4 + 1


def trim(ctx: dict, max_tokens: int) -> dict:
    """按「远圈 → 对手非邻车 → derived 对手字段 → 只留 now+session」的顺序裁剪。"""
    import copy

    out = copy.deepcopy(ctx)
    if estimate_tokens(out) <= max_tokens:
        return out

    # 1) 由远及近丢近期圈
    while out.get("recent_laps") and estimate_tokens(out) > max_tokens:
        out["recent_laps"].pop(0)
    if not out.get("recent_laps"):
        out.pop("recent_laps", None)
    if estimate_tokens(out) <= max_tokens:
        return out

    # 1b) 分段层：先只留最近一圈（逐段数组很贵），再整层丢掉。
    seg = out.get("segments")
    if isinstance(seg, dict) and estimate_tokens(out) > max_tokens:
        player = seg.get("player") or []
        if len(player) > 1:
            seg["player"] = player[-1:]
    if "segments" in out and estimate_tokens(out) > max_tokens:
        out.pop("segments", None)
    if estimate_tokens(out) <= max_tokens:
        return out

    # 2) 对手层只留前后邻车（去掉领跑者与 derived 里的差距变化）
    if "opponents" in out:
        out["opponents"].pop("leader", None)
    for key in ("gap_ahead_change", "gap_behind_change"):
        (out.get("derived") or {}).pop(key, None)
    if estimate_tokens(out) <= max_tokens:
        return out

    # 3) 整个对手层丢掉
    out.pop("opponents", None)
    if estimate_tokens(out) <= max_tokens:
        return out

    # 4) 只剩 now + session
    out.pop("derived", None)
    return out
