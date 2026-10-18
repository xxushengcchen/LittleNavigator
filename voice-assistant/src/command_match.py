"""把一句话解析成一条可执行的改车指令。

为什么坚持确定性匹配、绝不让模型参与：
    模型可以因为一句含糊的话编出一条指令。改胎改错在比赛里是直接的
    时间损失甚至退赛，这条路不能有任何「理解」成分——只认明确的说法，
    认不出来就说认不出来。

为什么不支持轮胎配方：
    iRacing 广播消息里没有「选配方」这个操作（详见 car_control 模块说明）。
    「换软胎」这类话**必须**被识别出来并明确拒绝，不能悄悄退化成
    「换胎」——用户要软胎，我们只勾了换胎，那就是错了还装作做对。
"""
from __future__ import annotations

import re

from .phrase_match import normalize

# 轮胎配方：广播 API 做不到，必须明确拒绝而不是将就执行。
#
# 不能靠枚举配方名——漏一个就变成「悄悄按普通换胎执行」。
# 实测漏过「换湿胎」「换干地胎」两条：用户要湿胎，我们只勾了换胎复选框，
# 进站换下来还是干胎，白丢一次进站。
# 所以改成「有『胎』字 + 有配方修饰词」两个条件同时成立。
_COMPOUND_DESCRIPTORS = ("软", "硬", "中性", "中胎", "超软", "超硬",
                         "干", "湿", "雨", "雪", "全雨", "半雨", "配方")

# 其他车内设置：完全是另一套东西，改不了。
#
# 用「动作词 + 设置名词」两个条件同时成立才判，而不是只认名词——
# 「引擎温度多少」是合法查询（名词在，没有动作），
# 「帮我关掉引擎」才是操作请求（动作 + 名词都在）。
_ACTION_VERBS = ("调", "关", "开", "设置", "改", "换", "切", "设")
_SETUP_NOUNS = ("tc", "abs", "刹车平衡", "刹车比", "胎压", "尾翼", "前翼",
                "悬挂", "差速器", "防倾杆", "避震", "齿比", "下压力", "雨刷",
                "引擎", "方向盘", "转向辅助", "牵引力", "防抱死",
                "最硬", "最软")

_ALL_CORNERS = ("LF", "RF", "LR", "RR")

_CLEAR_PATTERNS = (
    re.compile(r"(清空|清除|清掉|取消)(所有)?进站"),
    re.compile(r"进站(设置)?(全清|清空|清除)"),
    re.compile(r"全部取消|全都取消"),
)

_CLEAR_TYRE_PATTERNS = (
    re.compile(r"(取消|不要|不用|别)(换)?(轮胎|胎|轮)"),
    re.compile(r"不换(轮胎|胎|轮)"),
    # 倒装否定：「换胎不用了」「换胎算了」——否定在后面。
    # 实测漏过这条：用户说「不要换」，结果真的换了胎，白丢一次进站。
    re.compile(r"(换|上)(轮胎|胎|轮)[^，。]{0,4}(不用了|算了|取消|不要了|先不)"),
)

# 模糊量词：说不出具体升数时不能瞎猜，也不能退化成「沿用现有量」
_VAGUE_QUANTITY = ("点油", "加点", "一些", "一点儿", "一些儿")

_TEAROFF_WORDS = ("撕膜", "换挡风", "清挡风", "擦挡风", "清洁挡风", "挡风撕膜",
                  "换遮阳", "清洁一下挡风")

_FAST_REPAIR_WORDS = ("快修", "快速维修", "快速修复")

# 「换左前胎」里角和胎是分开的，所以用正则而不是连写词表。
# 「把四个轮子都换了」是倒装，另收一条。
_TYRE_RE = re.compile(r"(换|把)[^，。！？]{0,8}?(胎|轮子?)[^，。！？]{0,4}换?")
_TYRE_ALT = ("换四条", "四个胎", "四条胎")

# 直接判「做不到」的词（自带动作含义，不需要再配动作词）
_DIRECT_UNSUPPORTED = (
    ("回正", "方向盘要你自己回，软件不碰车"),
    ("该几档", "这是驾驶操作建议，我不给"),
    ("几档过", "这是驾驶操作建议，我不给"),
    ("该怎么过", "这是驾驶操作建议，我不给"),
    ("走哪条线", "这是驾驶操作建议，我不给"),
    ("替我开", "软件不替你开车"),
)

# 疑问句不是指令。「要不要加油」「该不该换胎」是分析问题，必须走正常路由。
# 「还剩几次快修」曾经触发过一次真快修——提问被当成指令，必须先挡掉。
# 这里聚的是「这句话在问，不是在命令」的信号。
_QUESTION_MARKERS = ("吗", "呢", "要不要", "该不该", "够不够", "为什么",
                     "多少", "几时", "多久", "?", "？",
                     # 存量/数量提问：还剩几次快修 / 加油还有多少 / 撕膜还有几张
                     "还剩", "还有", "几次", "几张", "几条", "几次", "要多久")

# 中文数字（ASR 有时输出汉字，有时输出阿拉伯数字，两种都要认）
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100}


def _cn_to_number(text: str) -> int | None:
    """解析「十」「十五」「二十」「一百二十」这类中文数字。范围 1–999。"""
    if not text:
        return None
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            total += (current or 1) * unit
            current = 0
        else:
            return None
    return total + current


def parse_litres(text: str) -> float | None:
    """从话里取加油升数。取不到返回 None。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*升", text)
    if m:
        return float(m.group(1))
    m = re.search(r"升?\s*([零一二两三四五六七八九十百]+)\s*升", text)
    if m:
        value = _cn_to_number(m.group(1))
        return float(value) if value is not None else None
    m = re.search(r"加\s*([零一二两三四五六七八九十百]+)\s*升", text)
    if m:
        value = _cn_to_number(m.group(1))
        return float(value) if value is not None else None
    return None


def _corners_in(q: str) -> list[str]:
    """从话里认角位。认不出来返回空列表（不是全部）。"""
    found: list[str] = []
    if any(w in q for w in ("左前", "前左")):
        found.append("LF")
    if any(w in q for w in ("右前", "前右")):
        found.append("RF")
    if any(w in q for w in ("左后", "后左")):
        found.append("LR")
    if any(w in q for w in ("右后", "后右")):
        found.append("RR")
    if found:
        return found
    # 「四个 / 四条 / 全部」= 四条都换
    if any(w in q for w in ("四个", "四条", "全部", "所有", "全都", "都换")):
        return list(_ALL_CORNERS)
    # 「前胎 / 前轮」= 两条前胎
    if any(w in q for w in ("前胎", "前轮", "前面两个")):
        return ["LF", "RF"]
    if any(w in q for w in ("后胎", "后轮", "后面两个")):
        return ["LR", "RR"]
    return []


def match(text: str) -> dict | None:
    """解析成动作。认不出来返回 None。

    返回的 dict 可能带 `kind="unsupported"`——那种情况表示「听懂了，
    但这件事软件做不到」，调用方要如实告诉用户，不能当成认不出来。
    """
    q = normalize(text or "")
    if not q:
        return None

    # 0) 疑问句不是指令。「要不要加油」「该不该换胎」是分析问题，
    #    交给正常路由，别在这里把疑问当命令执行。
    if any(marker in q for marker in _QUESTION_MARKERS):
        return None

    # 1) 明确做不到的，先说清楚，不要落到下面的「换胎」分支里去
    # 口语常省略「胎」字：「换超软」也是要换配方。
    # 条件是「配方修饰词」+（胎 / 换 / 切）之一同时成立。
    if (any(w in q for w in _COMPOUND_DESCRIPTORS)
            and ("胎" in q or "换" in q or "切" in q)):
        return {"kind": "unsupported", "say": "AST_CC_COMPOUND",
                "reason": "轮胎配方要在黑框里选，广播消息没有这个操作"}
    if "配方" in q:
        return {"kind": "unsupported", "say": "AST_CC_COMPOUND",
                "reason": "轮胎配方要在黑框里选，广播消息没有这个操作"}
    if (any(v in q for v in _ACTION_VERBS)
            and any(n in q for n in _SETUP_NOUNS)):
        return {"kind": "unsupported",
                "reason": "这是车内设置，广播消息改不了，得你自己在车里调"}

    # 这些词本身就说明「要软件直接操纵车辆」或「要驾驶操作建议」，
    # 不需要再配动作词。
    for word, reason in _DIRECT_UNSUPPORTED:
        if word in q:
            return {"kind": "unsupported", "reason": reason}

    # 2) 取消类必须排在设置类前面：「取消换胎」里含「换胎」
    for pattern in _CLEAR_TYRE_PATTERNS:
        if pattern.search(q):
            return {"kind": "clear_tires"}
    for pattern in _CLEAR_PATTERNS:
        if pattern.search(q):
            return {"kind": "clear"}

    # 2b) 多指令：一次只能执行一个。
    #     实测「换胎加油快修」原来只执行了快修，另外两个被静默丢掉。
    if len(_families(q)) >= 2:
        return {"kind": "unsupported",
                "reason": "一次说一个指令，多个我听不准"}

    # 3) 撕膜 / 快修
    if any(w in q for w in _TEAROFF_WORDS):
        return {"kind": "tearoff"}
    if any(w in q for w in _FAST_REPAIR_WORDS):
        return {"kind": "fast_repair"}

    # 4) 换胎。口语常省略「胎」字：「换右后」就是换右后胎。
    corners = _corners_in(q)
    if _TYRE_RE.search(q) or any(w in q for w in _TYRE_ALT):
        return {"kind": "tyre", "corners": corners or list(_ALL_CORNERS)}
    if "换" in q and corners:
        return {"kind": "tyre", "corners": corners}

    # 5) 加油（「加十升油」「加10升」「加油」都算）
    #
    #    「加满」单独处理：广播命令的 var=0 语义是「沿用黑框里现有的量」，
    #    不是「加到满」。直接把加满当 var=0 发出去，用户以为会加满，
    #    实际只加黑框里那个数——白进一次站。
    #    真值要从遥测算：容量 = 当前油量 / 当前油量百分比。算不出来就拒绝。
    if any(w in q for w in ("加满", "加满一箱", "灌满")):
        return _check_multi(q, "fuel") or {"kind": "fuel", "full": True,
                                          "litres": None}
    if "加" in q and ("油" in q or "升" in q):
        multi = _check_multi(q, "fuel")
        if multi:
            return multi
        if any(w in q for w in _VAGUE_QUANTITY):
            return {"kind": "unsupported",
                    "reason": "说不出具体升数，我不猜，你给个数字"}
        litres = parse_litres(q)
        if litres is not None and litres <= 0:
            return {"kind": "unsupported", "reason": "油量不能为零或负数"}
        if litres is not None and litres > FUEL_MAX_L:
            # 超出范围就拒绝，不能悄悄压到上限——用户说 999，
            # 我们加 120 也是错的，只是错得没那么明显。
            return {"kind": "unsupported",
                    "reason": f"{litres:.0f} 升超出合理范围，确认一下"}
        return {"kind": "fuel", "litres": litres}

    return None


FUEL_MAX_L = 120.0

# 各指令族的独立特征。一句话命中两个族就拒绝，让用户一次说一个——
# 实测「换胎加油快修」只执行了快修，另外两个被静默丢掉。
_TEAROFF_HINTS = ("撕膜", "挡风", "遮阳")
_FR_WORDS = ("快修", "快速维修", "快速修复")


def _families(q: str) -> list[str]:
    """这句话命中几个指令族。"""
    found = []
    if _TYRE_RE.search(q):
        found.append("tyre")
    if "加" in q and ("油" in q or "升" in q):
        found.append("fuel")
    if any(w in q for w in _TEAROFF_HINTS):
        found.append("tearoff")
    if any(w in q for w in _FR_WORDS):
        found.append("fast_repair")
    return found


def _check_multi(q: str, current: str) -> dict | None:
    """命中多个指令族时返回拒绝动作。"""
    others = [f for f in _families(q) if f != current]
    if not others:
        return None
    return {"kind": "unsupported",
            "reason": "一次说一个指令，多个我听不准"}


def clamp_litres(litres: float | None, lo: float, hi: float) -> float | None:
    """把升数压进合理范围。None 表示「沿用现有量」（广播 var=0）。"""
    if litres is None:
        return None
    if litres <= 0 or litres != litres:      # 含 NaN
        return None
    return max(lo, min(hi, float(litres)))
