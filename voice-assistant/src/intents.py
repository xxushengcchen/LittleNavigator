"""L1 意图识别：关键词 / 正则。不联网、不调模型，毫秒级。"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Match:
    intent: str
    confidence: float
    why: str


# 顺序有意义：先命中先返回。把更具体的规则放前面。
RULES: list[tuple[str, str, list[str]]] = [
    ('query_engine','引擎与挡位',[r'水温',r'油温',r'转速',r'电压',r'几[挡档]',r'空[挡档]',r'倒[挡档]']),
    ('query_car_settings','车内设置',[r'刹车.*比例',r'制动.*分配',r'防抱死',r'防锁死',r'循迹',r'牵引力控制']),
    ('query_speed','车速',[r'车速',r'速度.*多少',r'跑多快']),
    # (意图, 说明, 关键词/正则)
    ("query_fuel", "油量", [r"油", r"加油", r"燃油", r"油量", r"油耗"]),
    ("query_tyre", "轮胎", [r"胎", r"轮胎", r"胎温", r"胎压", r"磨损", r"抓地"]),
    ("query_weather", "天气", [r"天气", r"下雨", r"雨", r"路面", r"地[面]?", r"湿",
                               r"气温", r"温度", r"风", r"放晴", r"赛道状态"]),
    ("query_flags", "旗语", [r"旗", r"黄旗", r"绿旗", r"白旗", r"黑旗", r"蓝旗", r"判定"]),
    ("query_damage", "车损", [r"损伤", r"车损", r"撞", r"事故", r"车还好", r"incident",
                              r"扣分", r"伤"]),
    ("query_pit", "进站", [r"进站", r"维修区", r"换胎", r"进pit", r"pit"]),
    ("query_laptime", "圈速", [r"圈速", r"单圈", r"上圈", r"最快圈", r"慢了", r"快了",
                               r"丢时间", r"成绩"]),
    ("query_gap", "差距", [r"差距", r"多远", r"差[几多少\d]", r"[几多少]\s*秒",
                           r"追", r"拉[开远]", r"前车", r"后车", r"贴近"]),
    # 剩余圈数必须排在名次前面：两者都可能带「圈」字，先命中先返回
    ("query_laps", "剩余圈数", [r"还剩几圈", r"还有几圈", r"剩几圈", r"剩多少圈",
                                r"还剩\s*\d+\s*圈", r"还有\s*\d+\s*圈", r"剩\s*\d+\s*圈",
                                r"还有[一二两三四五六七八九十]+\s*圈",
                                r"剩[一二两三四五六七八九十]+\s*圈",
                                r"跑完多少", r"还有多久", r"快结束", r"跑几圈"]),
    ("query_position", "名次", [r"第几", r"名次", r"位置", r"排位", r"第\s*\d+"]),
]


def match(text: str) -> Match | None:
    """返回最具体的一条命中；没有就返回 None（交给 L2/L3）。"""
    q = (text or "").strip()
    if not q:
        return None
    for intent, why, pats in RULES:
        for pat in pats:
            m = re.search(pat, q)
            if m:
                return Match(intent=intent, confidence=0.95, why=f"{why}：命中「{m.group()}」")
    return None


def disambiguate(text: str) -> str | None:
    """「还剩几圈」这类歧义：带「油」走油量，带「比赛/结束」走剩余圈数。"""
    q = text or ""
    if "圈" in q and ("油" in q or "够跑" in q or "撑" in q):
        return "query_fuel"
    if "圈" in q and any(k in q for k in ("比赛", "结束", "剩多少", "还有几")):
        return "query_laps"
    return None
