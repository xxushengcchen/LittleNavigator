"""把遥测快照与近期圈史组装成给模型看的紧凑上下文。

两条硬约束决定了这里的形状：

1. **token 有上限**（1500）。模型每次调用都按输入计费，而且越长越慢；
   比赛场景里延迟比全面更重要，所以按问题类型分层给，不是无脑全量。
2. **数字闸只接受原样引用**（见 answer_checks）。模型自己算出来的数会被判
   失败，因此所有需要展示的派生量必须在这里算好、按口语习惯四舍五入，
   并明确告诉模型"只准引用给定数值"。
"""
from __future__ import annotations

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
    # derived（预计算值，模型只准引用这些）
    "laps_spare", "delta_to_best", "avg3", "avg5", "trend_per_lap",
    "tyre_temp_delta", "position_change", "gap_ahead_change", "gap_behind_change",
    # session
    "total_laps", "flags",
    # opponents
    "ahead", "behind", "leader",
})

# 层名本身也可以被引用：模型说「用了 recent_laps」是诚实的——我们确实给了它。
# 但数据闸会再核对「这一层在本次上下文里是否真的存在」（见 answer_checks），
# 所以把层名放进白名单不等于放行不存在的数据。
LAYER_NAMES: frozenset[str] = frozenset({
    "now", "derived", "session", "recent_laps", "opponents",
})

ALLOWED_FIELDS = ALLOWED_FIELDS | LAYER_NAMES

# 问题里出现这些词时才附加对手层——它约 120 token，不该为「还有多少油」付这笔钱。
_OPPONENT_HINTS = ("前车", "后车", "前面", "后面", "对手", "第一名", "领跑", "差距", "名次")


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


def build(snap: Snapshot, laps: list[dict], kind: QueryKind, question: str) -> dict:
    """按问题类型组装上下文。

    简单问题只给 now + derived（约 300 token）；分析型再带最近 5 圈。
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
