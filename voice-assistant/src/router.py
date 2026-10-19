"""路由：(意图, 遥测快照, 问句) → 要播的 msgId 序列。

设计原则：
  · 只回答遥测能直接给出的东西；判不出来就明说拿不到，绝不猜
  · 每个 msgId 都要在语音包里真实存在
  · 需要具体数值时走 numbers 拼装（前缀 + 数字样本 + 单位），
    拼出来的序列同样逐条校验存在性
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from .phrase_match import normalize
from .question_forms import canonicalize

from . import numbers
from .telemetry import Snapshot


@dataclass
class Decision:
    plan: list[str] = field(default_factory=list)   # 空 = 答不了
    reason: str = ""
    refusal_msg: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.plan)


def _need(s: Snapshot, *fields: str) -> str | None:
    missing = [f for f in fields if getattr(s, f, None) is None
               or (isinstance(getattr(s, f), float) and not math.isfinite(getattr(s, f)))]
    return None if not missing else "遥测里没有 " + "、".join(missing)


def _d(msg: str, reason: str) -> Decision:
    return Decision([msg], reason)


def _refuse(reason: str) -> Decision:
    return Decision([], reason)


_NUM_WORDS = ("多少", "几", "多大", "是多少", "多远", "多近")


def asks_lap_gap(q: str) -> bool:
    """圈差不能由秒差替代，也不能交给模型从秒差猜算。"""
    import re
    return bool(re.search(r'(?:差|落后|领先|相隔|套).{0,6}(?:几|多少|[0-9]+)圈|圈差|套圈', normalize(q)))


def _asks_number(q: str) -> bool:
    return any(w in (q or "") for w in _NUM_WORDS)


def route(intent: str, s: Snapshot, query: str = "") -> Decision:
    query = canonicalize(normalize(query))
    if not s.connected:
        return _refuse("iRacing 没连上，现在拿不到任何数据")
    if any(word in query for word in ("为什么", "什么原因", "分析", "如何", "怎么开", "怎么办")):
        return _refuse("当前仅回答遥测状态，不能分析原因或提供驾驶建议")
    if any(word in query for word in ("的原因", "处罚是什么", "什么处罚", "怎么判", "会被罚")):
        return _refuse("处罚原因和规则解释不在遥测范围内，只能播报当前旗号与指示")
    if any(word in query for word in ("几点", "几月", "几号", "星期几", "什么时间",
                                      "备用胎", "备用轮", "库存")):
        return _refuse("这不在比赛遥测的范围里")
    if any(word in query for word in ('调高','调低','调到','改成','设置为','怎么调','替我开','替我关')):
        return _refuse('这里只读取遥测，不操作车辆或提供调校指令')
    from .telemetry_answers import answer
    try:
        extended=answer(intent,s,query)
        if extended is not None:return extended
    except (ValueError,OverflowError):return _refuse('遥测数值无效或超出可表达范围')
    fn = _ROUTES.get(intent)
    if fn is None:
        return _refuse(f"没有为 {intent} 准备回答")
    try:
        return fn(s, normalize(query)) if intent in _WANTS_QUERY else fn(s)
    except (ValueError, OverflowError):
        return _refuse("遥测数值无效或超出语音包的可表达范围")


_WANTS_QUERY = {"query_laptime", "query_tyre", "query_laps", "query_fuel",
                "query_gap", "query_weather", "query_pit"}


# ---------------------------------------------------------------- 天气


def _weather(s: Snapshot, q: str = "") -> Decision:
    if _asks_number(q) or any(w in q for w in ("温", "度", "风", "会", "什么时候", "雨胎", "要不要", "大不大", "严重")):
        return _refuse("当前只能播报干湿与降雨状态，尚无温度数值、风况或天气预测的素材")
    if s.track_wetness is not None and 3 <= s.track_wetness <= 7:
        return _d("SPCC_SESSIONWET", f"路面积水档位 {s.track_wetness}（湿）")
    if s.declared_wet:
        return _d("SPCC_SESDECLAREDWET", "赛会已宣告湿地")
    if s.precip is not None and s.precip > 0:
        return _d("SPCC_RAININAREA", f"起终点降水 {s.precip}，附近正在下雨")
    if s.skies == 0 and s.precip == 0:
        return _d("SPCC_ALLSUN", "天空晴朗、无降水")
    if s.track_wetness in (1, 2):
        return _d("SPCC_SESSIONDRY", f"路面积水档位 {s.track_wetness}（干）")
    return _refuse("天气字段一个都没读到")


# ---------------------------------------------------------------- 燃油


def _fuel(s: Snapshot, q: str = "") -> Decision:
    if _asks_number(q) or any(w in q for w in ("几升", "油耗")):
        return _refuse("语音素材缺少燃油数值的前缀和单位，暂不能播报准确油量")
    bad = _need(s, "fuel_level")
    if bad:
        return _refuse(bad)
    if s.laps_remain is not None and 0 < s.laps_remain <= 9999 and s.laps_of_fuel is not None and math.isfinite(s.laps_of_fuel) and s.laps_of_fuel >= 0:
        r = s.laps_of_fuel / max(s.laps_remain, 0.1)
        why = f"可跑 {s.laps_of_fuel:.1f} 圈 / 剩 {s.laps_remain} 圈"
        if r >= 1.5:
            return _d("SPCC_FUEL_RACE1", why + " → 富余")
        if r >= 1.15:
            return _d("SPCC_FUEL_RACE2", why + " → 够")
        if r >= 1.0:
            return _d("SPCC_FUEL_RACE3", why + " → 刚好")
        if r >= 0.9:
            return _d("SPCC_FUEL_RACE5", why + " → 偏紧")
        if r >= 0.75:
            return _d("SPCC_FUEL_RACE6", why + " → 要补")
        return _d("SPCC_FUEL_RACE7", why + " → 不够")
    return _refuse("缺少有效剩余圈数或完整圈油耗，无法判断油是否够跑到终点")


# ---------------------------------------------------------------- 轮胎


def _tyre(s: Snapshot, q: str = "") -> Decision:
    return _refuse("当前未采集有效轮胎状态，事故点不能用于判断胎温、胎压或磨损")


# ---------------------------------------------------------------- 差距（数值拼装）


def _gap(s: Snapshot, q: str = "") -> Decision:
    if any(w in q for w in ('第几', '名次', '排名')):
        return _refuse('这是名次问题，不是前后车距离')
    if any(w in q for w in ('比我快','比我慢','圈速差')):
        return _refuse('圈速差不能用前后车时间间隔代替，请分别查询圈速')
    ahead = any(w in q for w in ("前车", "前面", "前方"))
    behind = any(w in q for w in ("后车", "后面", "后方"))
    if ahead and behind:
        return _refuse("请分别询问前车或后车的差距")
    if not ahead and not behind:
        return _refuse("需要明确询问前车或后车")
    direction = "ahead" if ahead else "behind"
    label = "前车" if ahead else "后车"
    gap = getattr(s, f"gap_{direction}_s")
    trend = getattr(s, f"gap_{direction}_trend")
    if gap is None or not math.isfinite(gap) or gap < 0:
        return _refuse(f"读不到{label}的有效差距")
    if _asks_number(q):
        lead = ('AST2_GAP_AHEAD_EST_LEAD' if ahead else 'AST2_GAP_BEHIND_EST_LEAD') if s.gap_estimated else ("AST_GAP_AHEAD_LEAD" if ahead else "AST_GAP_BEHIND_LEAD")
        return Decision([lead, *numbers.gap_to_msgids(gap), "SPCC_GAPIS_PT2"], f"{label} {gap:.1f}s → 拼装数值")
    if trend is None or not math.isfinite(trend) or abs(trend) <= 0.05:
        return _d('AST2_GAP_STABLE',f'{label}差距基本稳定') if trend is not None and math.isfinite(trend) else _refuse(f'{label}走势尚未建立')
    mid = ("SPCC_GAINCARFRONT" if trend < 0 else "SPCC_LOSECARFRONT") if ahead else ("SPCC_CARBEHINDGAIN" if trend < 0 else "SPCC_CARBEHINDLOSE")
    return _d(mid, f"{label} {gap:.1f}s，差距趋势 {trend:+.2f}")


# ---------------------------------------------------------------- 圈速（数值拼装）


def _laptime(s: Snapshot, q: str = "") -> Decision:
    if (s.lap_completed is not None and s.lap_completed >= 1
            and (s.lap_last is None or s.lap_last <= 0)
            and (s.lap_best is None or s.lap_best <= 0)):
        return _d("AST2_NO_VALID_LAP", "完成了圈数但没有有效圈速，不能拿 -1 当成绩")
    if any(word in q for word in ("前车", "后车", "对手", "前面", "后面", "第一名", "领跑")):
        return _refuse("当前仅采集自己的圈速，不能以它代替对手圈速")
    if any(w in q for w in ("最快", "最佳", "这圈", "当前圈")):
        return _refuse("当前数值播报仅支持上一完整圈，不能以它代替最佳圈或当前圈")
    bad = _need(s, "lap_last")
    if bad:
        return _refuse(bad)
    if s.lap_last <= 0:
        return _refuse("尚无有效上一圈成绩")
    if _asks_number(q):
        return Decision(
            ["AST_LAST_LAP_LEAD", *numbers.laptime_to_msgids(s.lap_last)],
            f"上圈 {s.lap_last:.3f}s → 拼装成「分 / 秒 / 十分位」")
    if s.lap_best and s.lap_best > 0 and s.lap_last <= s.lap_best + 0.05:
        return _d("SPCC_PERSONALBESTLAP", f"上圈 {s.lap_last:.3f} = 个人最佳")
    if s.lap_best and s.lap_best > 0:
        return _d("SPCC_DOBETTER",
                  f"上圈 {s.lap_last:.3f}，比最佳慢 {s.lap_last - s.lap_best:.3f}s")
    return _refuse("没有有效最佳圈用于判断上一圈的走势")


# ---------------------------------------------------------------- 名次 / 剩余圈


_POS = {1: "SPCC_IN1ST", 2: "SPCC_IN2ND", 3: "SPCC_IN3RD", 4: "SPCC_IN4TH", 5: "SPCC_IN5TH"}
_LAPS = {1: "SPCC_1TOGO", 2: "SPCC_2TOGO", 5: "SPCC_5TOGO", 10: "SPCC_10TOGO", 20: "SPCC_20TOGO"}


def _position(s: Snapshot) -> Decision:
    if s.position is None or not 1 <= s.position <= 9999:
        return _refuse("拿不到名次")
    if s.position in _POS:
        return _d(_POS[s.position], f"当前第 {s.position} 位")
    return _refuse(f"当前第 {s.position} 位，尚无该名次的准确播报素材")


def _laps(s: Snapshot, q: str = "") -> Decision:
    if any(w in q for w in ("雨", "天气", "湿", "放晴")):
        return _refuse("这是天气问题，当前不提供降雨预测")
    n = s.laps_remain
    if n is None or not 0 <= n <= 9999:
        if s.time_remain_s:
            return _refuse(f"这场比赛按时间算，剩 {s.time_remain_s / 60:.0f} 分钟；时间档还没做拼装")
        return _refuse("拿不到剩余圈数")
    if n in _LAPS:
        return _d(_LAPS[n], f"剩余 {n} 圈，正好有整句消息")
    if n <= 0:
        return _refuse(f"剩余 {n} 圈，没有可播的内容")
    return Decision(["AST_LAPS_LEAD", *numbers.int_to_msgids(n), "AST_LAPS_TAIL"],
                    f"剩余 {n} 圈 → 拼装「还剩 + {n} + 圈」")


# ---------------------------------------------------------------- 车损


_DAMAGE = [
    (25, "SPCC_DAMBAD"), (18, "SPCC_DAMMULAREA"), (14, "SPCC_DAMFLR"),
    (10, "SPCC_DAMMID"), (7, "SPCC_DAMREAR"), (5, "SPCC_DAML"),
    (1, "SPCC_DAMFNT"), (0, "SPCC_DAMGOK"),
]


def _damage(s: Snapshot) -> Decision:
    if "repair" in s.flags:
        return _d("SPCC_REPAIR", "SDK 维修旗要求进站处理")
    return _refuse("事故点无法判断车损部位或严重程度，当前未采集可确认的车损数据")


# ---------------------------------------------------------------- 旗语


def _flags(s: Snapshot) -> Decision:
    f = set(s.flags)
    if "disqualify" in f or "dq_scoring_invalid" in f:
        return _d("AST2_DISQUALIFY_OUT", "取消资格")
    if "repair" in f:
        return _d("SPCC_REPAIR", "维修旗")
    if "black" in f:
        return Decision(["AST2_PENALTY_SERVE", "AST2_PENALTY_NO_REASON"],
                        "黑旗：本轮必须进站受罚，但不解释原因")
    if "furled" in f:
        return _d("AST2_GIVEBACK_ADVANTAGE", "卷起黑旗：切弯占了便宜，要减速把优势还回去")
    if "red" in f:
        return _refuse("红旗；当前素材没有对应红旗播报")
    if "checkered" in f:
        return _d("SPCC_CHECKFLG", "格子旗")
    if "white" in f:
        return _d("SPCC_1TOGO", "白旗（最后一圈）")
    if f & {"yellow", "caution", "yellow_waving", "caution_waving"}:
        return _d("SPCC_YELOUT", "黄旗 / 全场 caution")
    if "blue" in f:
        return _d("SPCC_FASTERCAR", "蓝旗（后方快车）")
    if "green" in f:
        return _d("SPCC_GREEN", "绿旗")
    return _refuse(f"SessionFlags={s.session_flags} 没有可播的档位")


# ---------------------------------------------------------------- 进站


def _pit(s: Snapshot, q: str = "") -> Decision:
    if any(w in q for w in ("前车", "后车", "对手", "第一名", "领跑", "别人",
                            "上次", "最近一次", "第几圈", "了吗")):
        return _refuse("对手的进站记录当前没有采集，不能拿自己的策略代替")
    if s.on_pit_road:
        return _refuse("已经在维修区，不能再用即将进站的语音回答")
    if s.laps_of_fuel is not None and math.isfinite(s.laps_of_fuel) and s.laps_of_fuel >= 0 and s.laps_remain is not None and 0 < s.laps_remain <= 9999:
        if s.laps_of_fuel < s.laps_remain:
            return _d("SPCC_FUELPITLAP",
                      f"油只够 {s.laps_of_fuel:.1f} 圈、还要跑 {s.laps_remain} 圈 → 得进站")
        if s.laps_of_fuel < s.laps_remain * 1.15:
            return _d("SPCC_FUELCUTCLOSE",
                      f"油 {s.laps_of_fuel:.1f} 圈 vs 剩 {s.laps_remain} 圈 → 很紧")
        return _d("SPCC_FUELFINRACE",
                  f"油 {s.laps_of_fuel:.1f} 圈够跑完 {s.laps_remain} 圈 → 不用进站")
    return _refuse("缺少燃油策略数据，无法判断何时进站或维修区是否开放")


_ROUTES = {
    'query_speed':lambda s:_refuse('没有有效车速数据'),
    'query_engine':lambda s:_refuse('没有有效引擎温度数据'),
    'query_car_settings':lambda s:_refuse('没有有效车内设置数据'),
    "query_weather": _weather,
    "query_fuel": _fuel,
    "query_tyre": _tyre,
    "query_gap": _gap,
    "query_laptime": _laptime,
    "query_position": _position,
    "query_laps": _laps,
    "query_pit": _pit,
    "query_damage": _damage,
    "query_flags": _flags,
}
