"""知识库：把问法库、意图路由和遥测字段整理成界面可画的一张图。

为什么放在 src/ 而不是直接写死在页面里：
  · 提问页要显示「这条问法会走到哪」，这份结构必须和真实匹配器、真实路由同源；
    页面里再抄一份常量，改词库时两边就会悄悄对不上。
  · 只读、不联网、不改配置；全部由 intents_examples.yaml、question_forms、
    PhraseMatcher、router 和 telemetry_answers 现算。
  · 只用运行期拿得到的数据，不读 .py 源码：打包后源码不随程序发布，
    读源码会让这个接口在安装版里直接 500（v1.5.0 首版正是这个原因坏掉）。

返回结构（GET /api/knowledge）：
    totals   总数字
    intents  每个意图的短语数、规范问句数、问法分布、字段与播报方式
    fields   遥测字段分组（名字逐条对照 Snapshot）
    outputs  播报方式枚举
    probes   真实问法的完整链路
    conflicts 现算出来的规则问题（误判 / 缺对象）
    data_gaps 因当前快照缺数据而拒绝的条数（单独计数，不算缺陷）
    generated 生成时间与版本

分类计数（对象/时间/趋势/比较/状态/基础）是按问法关键词的启发式归类，
只用于看分布，不参与匹配，也不改变任何播报。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import yaml

from . import question_forms
from .phrase_match import PhraseMatcher, default_library, normalize
from .telemetry import SimTelemetry, Snapshot


def _library_path() -> Path:
    """短语库路径：和匹配器共用同一份解析规则。

    不能写成 Path(__file__).parents[1]：打包后本模块在 _internal 里，
    而短语库既随包内置一份，也允许用户把改过的版本放在 exe 旁边。
    """
    return default_library()

# 界面标题用；顺序即页面顺序（与 intents_examples.yaml 的分节顺序无关）。
INTENT_LABELS: dict[str, str] = {
    "query_fuel": "燃油",
    "query_laptime": "圈速",
    # v1.5.4：只报 iRacing 官方赛段（SplitTimeInfo）的分段用时差，不报弯道。
    "query_sector_loss": "赛段损失",
    "query_laps": "比赛进度",
    "query_position": "名次",
    "query_gap": "前后车",
    "query_opponent": "对手圈速",
    "query_pit": "进站",
    "query_weather": "天气",
    "query_tyre": "轮胎",
    "query_damage": "车损",
    "query_flags": "旗语",
    "query_engine": "引擎",
    "query_car_settings": "设置",
    "query_speed": "车速",
}

# 遥测字段分组：名字必须能在 Snapshot 上找到，_validate_fields() 会逐条核对。
FIELD_GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("wx", "天气与环境", ("air_temp", "track_temp", "track_wetness", "precip", "skies", "wind_vel")),
    ("fuel", "燃油", ("fuel_level", "fuel_per_lap", "laps_of_fuel")),
    ("lap", "圈速与进度", ("lap_last", "lap_best", "laps_remain", "lap_completed", "time_remain_s")),
    ("pos", "对手与位置", ("car_positions", "gap_ahead_s", "gap_behind_s", "gap_ahead_trend", "gap_behind_trend")),
    ("pit", "进站", ("on_pit_road", "pit_sv_flags", "repair_required_s", "fast_repairs_available")),
    ("dmg", "车损与旗语", ("incident_count", "session_flags")),
    ("car", "车辆状态", ("speed_kph", "water_temp", "oil_temp", "rpm", "gear", "voltage")),
    ("set", "车辆设置", ("brake_bias", "abs_level", "tc_level", "car_setup")),
]

# 分节里没有 fields 的意图（速度/引擎/车辆设置/对手圈速），按实际读取它的代码归类。
EXTRA_INTENT_FIELDS: dict[str, tuple[str, ...]] = {
    "query_engine": ("car",),
    "query_speed": ("car",),
    "query_car_settings": ("set",),
    "query_opponent": ("pos", "lap"),
    # 赛段计时来自 60Hz CarIdxLapDistPct 过段插值（src/segments.py），
    # 与圈速、前后车共用「圈速与进度」「对手与位置」两组遥测；段数随赛道变化。
    "query_sector_loss": ("lap", "pos"),
    "query_tyre": ("car", "set"),
}

CATEGORIES = ("对象/目标", "时间/圈数", "趋势/预测", "比较/差值", "状态/是否", "基础读数")

# 顺序有意义：先匹配更具体的类别。
_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("对象/目标", r"前车|后车|前車|後車|第一名|领跑|对手|同组|组别|左前|右前|左后|右后|第[一二三四五六七八九十\d]+名"),
    ("时间/圈数", r"多久|几分钟|多少分钟|还剩|剩几|还有几|几圈|圈数|时间|什么时候|分钟|秒"),
    ("趋势/预测", r"趋势|越来越|追近|缩小|拉大|变化|稳定|预测|接下来|之后"),
    ("比较/差值", r"[比差]|慢了|快了|落后|领先|超过|一样|多少圈|几秒"),
    ("状态/是否", r"吗|没|是不是|够不够|有没有|湿不湿|开放|开没开|状态|情况|怎么样|正常"),
)

# 现算冲突用的真实问法：前几条来自实测/文档里出现过的句子，
# 其余由规范问句自动补齐（见 build()）。它们只是被「问一遍」，不执行、不播报。
PROBE_QUERIES: tuple[str, ...] = (
    "现在几档",
    "胎压多少",
    "前车是第几名",
    "油还够吗",
    "还剩多少圈",
    "今天几点了",
)

_CACHE: dict = {"key": None, "value": None}
_CACHE_SECONDS = 30.0


def _category(text: str) -> str:
    s = re.sub(r"\s+", "", text)
    for name, pattern in _CATEGORY_RULES:
        if re.search(pattern, s):
            return name
    return "基础读数"


def _load_library() -> dict:
    data = yaml.safe_load(_library_path().read_text(encoding="utf-8")) or {}
    return {k: v for k, v in data.items()
            if isinstance(v, dict) and v.get("route") == "state_layer"}


def _validate_fields() -> None:
    """字段名打错时立刻炸掉，不要把错误藏进界面。"""
    known = set(Snapshot.__dataclass_fields__)
    for _key, label, names in FIELD_GROUPS:
        missing = [n for n in names if n not in known]
        if missing:
            raise ValueError(f"知识库字段组「{label}」不在 Snapshot 上：{missing}")


def _fields_for(intent: str, spec: dict) -> list[str]:
    """yaml 的 fields 是人写的 SDK 字段名；映射到界面用的分组。"""
    if intent in EXTRA_INTENT_FIELDS:
        return list(EXTRA_INTENT_FIELDS[intent])
    declared = {str(x) for x in (spec.get("fields") or [])}
    out: list[str] = []
    mapping = {
        "wx": {"AirTemp", "TrackTempCrew", "TrackWetness", "Precipitation", "Skies", "WindVel",
               "relative_humidity", "declared_wet"},
        "fuel": {"FuelLevel", "FuelLevelPct", "fuel_per_lap", "laps_of_fuel"},
        "lap": {"LapLastLapTime", "LapBestLapTime", "LapDeltaToBestLap", "lap_time_median",
                "LapCompleted", "SessionLapsRemainEx", "lap_current", "laps_remain"},
        "pos": {"CarIdxPosition", "CarIdxLapDistPct", "gap_trend", "position", "class_position"},
        "pit": {"OnPitRoad", "PitSvFlags", "pit_window"},
        "dmg": {"PlayerCarMyIncidentCount", "SessionFlags", "incident_count", "session_flags"},
    }
    for key, names in mapping.items():
        if declared & names:
            out.append(key)
    return out or ["car"]


# 每个意图可能产出的播报方式（顺序即界面顺序）。
#
# 运行期只用这份表，不再读 telemetry_answers.py / router.py 的源码：
# 打包后源码不随程序发布，读 .py 会直接抛 FileNotFoundError，整个接口 500。
# 代价是「改代码要记得改表」，因此 tests/test_knowledge_packaging.py 会
# 重新解析两个源码文件的分支，与这张表逐项比对，漏改就测试失败。
OUTPUT_MODES: dict[str, tuple[str, ...]] = {
    "query_fuel": ("整句", "数值", "时间"),
    "query_laptime": ("整句", "数值"),
    # 赛段回答是「第 N 段 + 比前车慢 + 数字 + 秒」的 atom 拼装：
    # 固定片段（整句）与逐位数字（数值）混用，两种口径都标出来。
    "query_sector_loss": ("整句", "数值"),
    "query_laps": ("整句", "数值", "时间"),
    "query_position": ("整句", "数值"),
    "query_gap": ("整句", "数值"),
    "query_opponent": ("整句", "数值"),
    "query_pit": ("整句", "数值", "时间"),
    "query_weather": ("整句", "数值"),
    "query_tyre": ("数值",),
    "query_damage": ("整句", "数值"),
    "query_flags": ("整句",),
    "query_engine": ("整句", "数值"),
    "query_car_settings": ("数值",),
    "query_speed": ("数值",),
}


def _output_modes() -> dict[str, list[str]]:
    return {intent: list(kinds) for intent, kinds in OUTPUT_MODES.items()}


def _trace(matcher: PhraseMatcher, snap: Snapshot, query: str, router) -> dict:
    """一句问法的完整链路；不播报、不改车、不调模型。"""
    hit = matcher.match(query)
    row: dict = {
        "q": query,
        "intent": hit.intent,
        "score": round(hit.score, 3),
        "margin": round(hit.margin, 3),
        "phrase": hit.phrase,
        "reason": hit.reason,
        "accept": matcher.accept,
        "min_margin": matcher.margin,
        "ranked": [{"intent": i, "score": round(s, 3), "phrase": p}
                   for i, s, p in hit.ranked[:3]],
        "ok": False,
        "why": "未命中任何意图",
        "plan": [],
        "fields": [],
    }
    if not hit.intent:
        return row
    decision = router.route(hit.intent, snap, query)
    row["ok"] = bool(decision.ok)
    row["why"] = decision.reason
    row["plan"] = list(decision.plan)
    return row


def _is_data_gap(reason: str) -> bool:
    """这句话的拒绝原因是「当前读不到数据」，而不是问法/规则有问题。

    模拟快照本来就没有 RPM、没有进站检测记录；把这些算成缺陷，
    会让页面把「没开游戏」误报成「词库有问题」。真实连接时同样的问法
    往往能答，所以它们只计数、不列为可疑项。
    """
    words = (
        "缺少", "没有读到", "读不到", "拿不到", "尚未采集", "未采集",
        "没有观察到", "没有有效", "暂无", "尚未", "无效",
    )
    return any(w in (reason or "") for w in words)


def _intent_accepts(intent: str, query: str) -> bool:
    """这个意图的规则分支是否「受理」这类问法（只看分支，不看遥测值）。

    用来判断误判：例如「现在几档」被路由到 query_car_settings，而
    query_car_settings 只会受理 ABS/TC/刹车比，对「几档」直接说
    「这项车内设置尚未提供」；query_engine 里却明确有挡位分支。
    这里传一个字段齐全的假快照，仍然不改任何真实状态。
    """
    from . import router as router_mod

    fake = Snapshot(
        connected=True,
        gear=4, rpm=3000.0, voltage=12.6, water_temp=86.0, oil_temp=95.0,
        abs_level=5, tc_level=3, brake_bias=52.0,
        fuel_level=18.4, laps_of_fuel=13.0, laps_remain=8,
        lap_last=83.8, lap_best=83.0, lap_current=40.0, lap_completed=5,
        position=4, class_position=3, field_size=12,
        gap_ahead_s=1.8, gap_behind_s=2.4,
        air_temp=24.0, track_temp=31.0, track_wetness=1, skies=1,
        precip=0.0, wind_vel=2.0, relative_humidity=0.4,
        speed_kph=187.2, incident_count=2,
        pit_speed_limit_kph=60.0, fast_repairs_available=3,
        repair_required_s=0.0, repair_optional_s=0.0,
    )
    try:
        decision = router_mod.route(intent, fake, query)
    except Exception:  # noqa: BLE001 - 探针不能影响接口
        return False
    if not decision.ok:
        return False
    # 被拒的典型话术：没有明确数值、也没有状态播报。
    reason = decision.reason or ""
    return not any(w in reason for w in ("尚未提供", "不能用", "不提供"))


def build(force: bool = False) -> dict:
    """组装整份知识库数据；结果缓存 30 秒，避免每次切页都重算。"""
    key = (_library_path().stat().st_mtime, time.time() // _CACHE_SECONDS)
    if not force and _CACHE["key"] == key and _CACHE["value"] is not None:
        return _CACHE["value"]

    _validate_fields()
    from . import router as router_mod

    examples = _load_library()
    matcher = PhraseMatcher()
    snap = SimTelemetry().read()
    outputs = _output_modes()

    loaded: dict[str, int] = {}
    for phrase in matcher.phrases:
        loaded[phrase.intent] = loaded.get(phrase.intent, 0) + 1

    canonical: dict[str, int] = {}
    for intent, _canonical, _forms in question_forms.GROUPS:
        canonical[intent] = canonical.get(intent, 0) + 1

    intents = []
    for key_name, label in INTENT_LABELS.items():
        rows = [str(x) for x in (examples.get(key_name, {}).get("examples") or [])]
        buckets = {c: 0 for c in CATEGORIES}
        for q in rows:
            buckets[_category(q)] += 1
        fields = _fields_for(key_name, examples.get(key_name, {}))
        intents.append({
            "key": key_name,
            "label": label,
            "phrases": loaded.get(key_name, 0),
            "examples": len(rows),
            "canonical": canonical.get(key_name, 0),
            "buckets": buckets,
            "fields": fields,
            "outputs": outputs.get(key_name, []),
            "samples": [c for i, c, _f in question_forms.GROUPS if i == key_name][:3],
        })

    # 真实问法链路：固定探针在前，再补每个意图的第一条规范问句。
    seen: set[str] = set()
    probes_q: list[str] = []
    for q in PROBE_QUERIES:
        if q not in seen:
            seen.add(q)
            probes_q.append(q)
    for intent, canon, _forms in question_forms.GROUPS:
        if canon not in seen:
            seen.add(canon)
            probes_q.append(canon)

    probes = [_trace(matcher, snap, q, router_mod) for q in probes_q]

    # 只报「规则问题」，不报数据缺口：
    #   · 误判 —— 命中了 A（A 答不了），但排第二的 B 明明能答。
    #   · 缺对象/条件 —— 命中正确意图，但问法本身缺必要信息（如胎压没说哪个轮）。
    #   · 数据缺失（模拟快照没有 RPM、没有进站检测等）不算缺陷，单独计数不列出。
    conflicts = []
    data_gaps = 0
    for row in probes:
        if row["ok"] or not row["intent"]:
            continue
        second = next((r for r in row["ranked"][1:] if r["intent"] != row["intent"]), None)
        if second:
            # 不看「当前快照能不能答」：模拟快照本来就没有挡位/RPM 等实时值，
            # 那会把「连接真游戏后能答」的问法也报成误判。改成看意图本身是否
            # 受理这句话——_intent_accepts() 只读规则分支，不读遥测。
            alt = _intent_accepts(second["intent"], row["q"])
            if alt:
                conflicts.append({
                    "q": row["q"], "kind": "误判", "intent": row["intent"],
                    "score": row["score"], "second": second["intent"],
                    "second_score": second["score"],
                    "note": (f"命中 {row['intent']}（{row['score']:.2f}）但规则层答不了；"
                             f"{second['intent']}（{second['score']:.2f}）才是这类问法该走的意图")[:160],
                })
                continue
        why = row["why"] or ""
        if _is_data_gap(why):
            data_gaps += 1
            continue
        if "请说明" in why or "需要明确" in why or "请分别" in why:
            conflicts.append({
                "q": row["q"], "kind": "缺对象", "intent": row["intent"],
                "score": row["score"], "second": second["intent"] if second else None,
                "second_score": second["score"] if second else None,
                "note": why[:160],
            })


    fields = [
        {"key": k, "label": label, "items": list(names),
         "users": [i["key"] for i in intents if k in i["fields"]]}
        for k, label, names in FIELD_GROUPS
    ]

    value = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "totals": {
            "intents": len(intents),
            "phrases": len(matcher.phrases),
            "examples": sum(len(v.get("examples") or []) for v in examples.values()),
            "canonical": len(question_forms.GROUPS),
            "aliases": len(question_forms.ALIASES),
            "negatives": len(matcher.negatives),
            "candidates": len(matcher._canonical_candidates),
            "fields": len(FIELD_GROUPS),
        },
        "categories": list(CATEGORIES),
        "intents": intents,
        "fields": fields,
        "outputs": ["整句", "数值", "时间"],
        "probes": probes,
        "conflicts": conflicts,
        "data_gaps": data_gaps,
        "limits": {
            "categories": "按问法关键词的启发式归类，只用于看分布，不参与匹配。",
            "conflicts": "只覆盖固定探针与规范问句；数据缺失类的拒绝单独计数，不列为可疑项。",
            "sim": "链路里的遥测来自模拟快照，不代表真实比赛数据。",
        },
    }
    _CACHE.update({"key": key, "value": value})
    return value


if __name__ == "__main__":  # 手动看一眼：python -m src.knowledge
    import json
    data = build(force=True)
    print(json.dumps(data["totals"], ensure_ascii=False))
    for row in data["conflicts"]:
        print(row["kind"], row["q"], "|", row["note"][:80])
