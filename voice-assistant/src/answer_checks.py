"""三道校验闸：拦住模型编造的数字与字段。

为什么必须有这些闸：
    一个胡说「你还有 3 圈油」的工程师，比一个说「读不到」的助手危险得多。
    模型不知道自己在编，只能靠程序侧核对——这是「绝不猜」红线在 AI 层的延伸。

为什么数字闸这么严（±0.05 容差、不认中文数字）：
    宽松的闸门等于没有闸门。中文数字歧义大（「一点八」是 1.8 还是 1 和 8？），
    与其猜，不如要求模型用阿拉伯数字；四舍五入到整数也不接受，因为
    「油还够跑 5 圈」与「油还够跑 6 圈」在比赛里是两个决策。
    需要展示的派生值应在 race_context 里预计算好，而不是让模型现算。
"""
from __future__ import annotations

import re

from .race_context import ALLOWED_FIELDS

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 中文数字：只把这些当「数值表述」；「上一圈」「一档」这类量词搭配不算。
# 用前后文排除法而不是维护一份量词白名单——中文里「一」做量词太常见，
# 逐个列反而更容易漏。
# 审计 A10：「两」也是常用数量词（「油还够两圈」），旧表漏掉它是稳定绕过。
_CN_DIGIT_RE = re.compile(r"[一二两三四五六七八九十零百千万]")

# 中文数字出现在这些搭配里时是量词/序数，不是要播报的数值
_CN_MEASURE_PATTERNS = (
    re.compile(r"上一[圈次名段个]"),
    re.compile(r"下一[圈次名段个]"),
    re.compile(r"第一[次圈名个]"),
    re.compile(r"同一[圈次名个]"),
    # 「最近一圈 83.512」这类：中文数字是量词，真正的数值是后面的阿拉伯数字。
    # 实测三个问句（前车圈速/圈速稳定吗/有没有进步）都栽在这里——回答里
    # 明明只有阿拉伯数字，却被「一圈」判成中文数字表述。
    # 注意只收「最近/这/那/每」+ 数字 + 圈次：不覆盖「还剩一圈」，
    # 那是在陈述数量（1 圈），必须继续由数字闸拦。
    re.compile(r"最近[一二两三四五六七八九十]\s*[圈次]"),
    re.compile(r"[这那每][一二两三四五六七八九十]\s*[圈次名段个]"),
    re.compile(r"一[档个位]"),
    re.compile(r"[一二两]个"),
    re.compile(r"十[分种]"),
    re.compile(r"一[点些]"),
)

# 「第 4 名」这类序数词里的数字按名次处理，不与其它候选值混用
_ORDINAL_RE = re.compile(r"第\s*(\d+)\s*(?:名|位)")

# 百分比：模型会把 0-1 的比例说成 62%
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

# 分秒：1 分 23 秒 / 1 分 23.5 秒
_MIN_SEC_RE = re.compile(r"(\d+)\s*分\s*(\d+(?:\.\d+)?)\s*秒")

# 分段引用（v1.5.4）：「第 2 段」/「第二段」。段号不是遥测字段值，不能指望
# 它出现在上下文数字里；改为按 segments.count 校验范围。
# 中文段号是**序数**（「第二段」的「二」不是可播报数值），单独解析，
# 不参与后面「中文数字一律拒绝」的规则。
_SECTOR_REF_RE = re.compile(r"第\s*([0-9]+|[一二三四五六七八九十]+)\s*段")

# 分段差：「第 2 段比前车慢 0.31 秒」「第 4 段落后 0.18 秒」。
# 必须带方向词才算段差——「第 2 段用时 25.3 秒」是段用时，仍走普通数字闸。
_SECTOR_DELTA_RE = re.compile(
    r"第\s*([0-9]+|[一二三四五六七八九十]+)\s*段[^0-9]{0,12}?"
    r"(慢|快|落后|领先|多|少)[^0-9]{0,6}?"
    r"(\d+(?:\.\d+)?)\s*秒")

_CN_FOR_SECTOR = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _sector_number(text: str) -> int | None:
    """解析段号：阿拉伯或中文序数（「十二」=12）。认不出返回 None。"""
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CN_FOR_SECTOR.get(text[1], 0) if len(text) > 1 else 10
    if "十" in text:
        head, _, tail = text.partition("十")
        if head not in _CN_FOR_SECTOR or (tail and tail not in _CN_FOR_SECTOR):
            return None
        return _CN_FOR_SECTOR[head] * 10 + (_CN_FOR_SECTOR[tail] if tail else 0)
    return _CN_FOR_SECTOR.get(text)

# data_used 的字段别名：模型会按游戏/日志里的习惯叫法引用同一份数据。
# v1.5.2 实测：回答内容完全正确，只因为 data_used 写了 `lap_time` 就整条被丢。
# 别名先解析成白名单字段、再走原来的两道检查（白名单 + 本次上下文存在），
# 数值校验不受影响，真未知字段照样拒绝。
FIELD_ALIASES: dict[str, str] = {
    "lap_time": "lap_last",        # iRacing 圈速日志的叫法
    "last_lap": "lap_last",        # 与对手层的 last_lap 同义
    "best_lap": "lap_best",        # 与对手层的 best_lap 同义
}


def _flatten(node, prefix: str = "") -> list[tuple[str, float]]:
    """把上下文展平成 (字段路径, 数值) 列表，供比对。"""
    out: list[tuple[str, float]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten(value, path))
    elif isinstance(node, (list, tuple)):
        for i, value in enumerate(node):
            out.extend(_flatten(value, f"{prefix}[{i}]"))
    elif isinstance(node, bool):
        pass                       # bool 不是可引用的数值
    elif isinstance(node, (int, float)):
        out.append((prefix, float(node)))
    return out


def _tolerance(number_text: str) -> float:
    """容差随小数位收紧：整数 0.05，一位小数 0.06。

    审计 A10：旧实现按回答里的小数位**增加**容差，写出更多小数位反而
    更容易蒙混过关（6.300000000000 能匹配 6.2）。正确方向是收紧并封顶：
    上下文数值本身已按一位小数给出，回答多写的小数位不提供任何精度信息。
    """
    if "." in number_text:
        decimals = len(number_text.split(".")[1])
        return 0.05 + 0.01 * min(decimals, 1)
    return 0.05


def _matches(value: float, candidates: list[tuple[str, float]]) -> bool:
    return any(abs(value - c) <= 0.051 for _, c in candidates)


def _seconds_from_min_sec(answer: str) -> list[float]:
    """把「1 分 23 秒」这类表述换算成总秒数，用于与秒制候选值比对。"""
    return [int(m) * 60 + float(s) for m, s in _MIN_SEC_RE.findall(answer)]


# ---------------------------------------------------------------- A10 辅助
#
# 审计 A10：只证明「某个数字在上下文里出现过」不够。同一个数字可能来自
# 完全不同的量（18.4 升 vs 18.4 圈），负号会被 `_NUM_RE` 吃掉，问题里
# 出现过的数字会被当成豁免，分秒表述还会把同字面量的别处数字一起跳过。
# 下面按「符号 + 单位/字段归属 + 文本 span」补上绑定。

# 单位 -> 允许的字段类别；字段名 -> 类别。未识别的单位不做推断（避免误杀）。
_UNIT_FIELD_CATEGORIES: dict[str, set[str]] = {
    # "lap" 是圈号（第几圈），不是圈数量；不绑定「圈」单位，避免
    # 「还剩 12 秒」撞上 lap=12 时被误判成单位不符（既有行为约定）。
    "laps": {"lap_completed", "laps_remain", "laps_of_fuel",
             "total_laps", "laps_spare", "completed"},
    "fuel": {"fuel_level", "fuel_per_lap"},
    "seconds": {"lap_last", "lap_best", "avg3", "avg5", "trend_per_lap",
                "delta_to_best", "gap_ahead_s", "gap_behind_s",
                "time_remain_s", "delta_s", "lap_time", "gap_s",
                "last_lap", "best_lap", "sectors", "deltas"},
    "temp": {"air_temp", "track_temp", "water_temp", "oil_temp"},
    "speed": {"speed_kph"},
    "position": {"position", "class_position", "pos"},
    "gear": {"gear"},
    "percent": {"track_wetness", "relative_humidity", "precip"},
}
_UNIT_TOKENS: tuple[tuple[str, str], ...] = (
    ("公里每小时", "speed"), ("公里/小时", "speed"), ("km/h", "speed"),
    ("摄氏度", "temp"), ("℃", "temp"),
    ("圈", "laps"), ("升", "fuel"), ("秒", "seconds"), ("分", "seconds"),
    ("度", "temp"), ("公里", "speed"), ("档", "gear"), ("名", "position"),
    ("位", "position"),
)

# 问题里作为「前提」给出的数字（用户自己说的差距），回答复述时可以豁免。
# 只有比较/差距语境才算前提——「还能跑99圈吗」这种断言不能借问题数字洗白。
_QUESTION_PREMISE_MARKERS = (
    "慢", "快", "差", "落后", "领先", "损失", "丢", "推头", "甩尾",
    "降低", "提高", "增加", "减少", "超过", "不足", "不到", "多了", "少了",
)


def _signed_value(text: str, match: re.Match) -> float:
    """取数字的字面值并识别前置负号（范围连字符不算负号）。"""
    value = float(match.group())
    index = match.start() - 1
    while index >= 0 and text[index] in " \t":
        index -= 1
    if index < 0 or text[index] not in "-−":
        return value
    before = index - 1
    while before >= 0 and text[before] == " ":
        before -= 1
    # 「18.4-18.6」「1-2 圈」的连字符前面是数字：这是范围，不是负号。
    if before >= 0 and (text[before].isdigit() or text[before] == "."):
        return value
    return -value


def _following_unit(text: str, end: int) -> tuple[str, str] | None:
    """数字后面紧跟的单位（跳过空格）；未识别返回 None。"""
    index = end
    while index < len(text) and text[index] in " \t":
        index += 1
    for token, category in _UNIT_TOKENS:
        if text.startswith(token, index):
            return token, category
    return None


def _leaf_name(path: str) -> str:
    """``segments.best_loss[0].delta_s`` -> ``delta_s``；``deltas[2]`` -> ``deltas``。"""
    leaf = path.rsplit(".", 1)[-1]
    return leaf.split("[", 1)[0]


def _unit_matches(category: str, matched: list[tuple[str, float]]) -> bool:
    """匹配到的候选字段里，是否至少有一个属于该单位对应的量。

    没有任何候选字段能归入已知量纲时不做推断（例如 12 撞上圈号 lap=12，
    无法证明用户说的「12 秒」是错的）；一旦有已知量纲，就要求单位对得上。
    """
    known: set[str] = set()
    for path, _ in matched:
        leaf = _leaf_name(path)
        for name, fields in _UNIT_FIELD_CATEGORIES.items():
            if leaf in fields:
                known.add(name)
    if not known:
        return True
    return category in known


def _question_premises(question: str) -> set[float]:
    """问题里被比较/差距语境标记过的数值，视为用户给出的前提。"""
    q = question or ""
    out: set[float] = set()
    for match in _NUM_RE.finditer(q):
        window = q[max(0, match.start() - 12):match.start()]
        if any(marker in window for marker in _QUESTION_PREMISE_MARKERS):
            out.add(float(match.group()))
    return out


# ---------------------------------------------------------------- A11 内容闸
#
# 审计 A11：禁区不能只靠 system 提示词。这里用有限词法兜住两类最危险的
# 自由发挥：操作/调校指令、处罚与规则解释。这是有限语法，不声称能覆盖
# 所有改写——但比「只信提示词」强，且误杀方向是安全的（拒答）。
# 强操作动词：几乎只出现在「替用户改车」的指令/建议里。
_OPERATION_ADVICE_RE = re.compile(
    r"调软|调硬|调紧|调松|调高|调低|调到|设置成|设置到|改成|改为|换成|更换|"
    r"拆下|装上|拧紧|拧松|打到|放到")
# 开/关类必须带祈使前缀才算建议：遥测事实「维修区已关闭」「DRS 已打开」不能误杀。
_OPERATION_SWITCH_RE = re.compile(
    r"(?:把|将|请|帮我|替你|建议|应该|可以|需要|记得|试试|试着|先)"
    r"[^。；;！!？?]{0,8}(?:打开|关闭|关掉)")
_UNSAFE_RULE_RE = re.compile(r"处罚|判罚|罚时|罚秒|犯规|违规|黑旗|禁赛|申诉成功")
_OUT_OF_DOMAIN_ANSWER_RE = re.compile(
    r"(?:现在|今天|当前)(?:是|的|的日期是)?(?:几月|几号|几点|什么时间|什么日期|星期几)")


def check_content(answer: str, question: str) -> str | None:
    """回答内容闸（审计 A11）：不解释处罚、不给操作指令、不聊比赛外的事。"""
    text = answer or ""
    if not text.strip():
        return None
    for regex, label in (
        (_OPERATION_ADVICE_RE, "操作/调校指令"),
        (_OPERATION_SWITCH_RE, "操作/调校指令"),
        (_UNSAFE_RULE_RE, "处罚或规则解释"),
        (_OUT_OF_DOMAIN_ANSWER_RE, "比赛遥测范围外的时间/日期"),
    ):
        match = regex.search(text)
        if match:
            return f"回答含{label}：{match.group(0)}"
    return None


def check_length(answer: str, max_chars: int) -> str | None:
    """超长直接拒绝，不截断——截断会留下半句话，比不说更糟。"""
    if len(answer or "") > max_chars:
        return f"回答 {len(answer)} 字超过上限 {max_chars}"
    return None


def check_data(data_used: list[str], ctx: dict) -> str | None:
    """`data_used` 里的字段必须真实存在于本次上下文。

    两道检查缺一不可：
      1. 名字在 ALLOWED_FIELDS 内（挡住「编造字段名」）
      2. 名字在**本次 ctx** 里确实存在（挡住「引用了这轮没给的数据」）

    第 2 条是实测发现的：模型会诚实地说「我用了 recent_laps」，
    但如果这轮上下文只给了 now+derived（简单问题不带历史），
    那它就是在引用不存在的数据——必须拒绝。
    """
    if not isinstance(data_used, (list, tuple)):
        return "data_used 不是列表"
    present = _present_names(ctx)
    for name in data_used:
        if not isinstance(name, str):
            return f"data_used 含非字符串项：{name!r}"
        error = _field_error(name, ctx, present)
        if error is None:
            continue
        # 别名容错：只在原名不成立时才尝试，且解析后仍要走完整两道检查。
        # 未知字段（没有别名 / 别名也不在本次上下文）继续按原名拒绝。
        alias = _alias_target(name)
        if alias is not None and _field_error(alias, ctx, present) is None:
            continue
        return error
    return None


def _alias_target(name: str) -> str | None:
    """把别名换成白名单字段名，保留点路径前缀（now.lap_time → now.lap_last）。"""
    if "." in name:
        head, leaf = name.rsplit(".", 1)
        target = FIELD_ALIASES.get(leaf)
        return f"{head}.{target}" if target else None
    return FIELD_ALIASES.get(name)


def _field_error(name: str, ctx: dict, present: set[str]) -> str | None:
    """单个 data_used 项的两道检查；返回 None 表示这条引用成立。"""
    if _path_exists(ctx, name):
        # 点路径：末段字段名仍须在白名单内，且整条路径在 ctx 里真实存在
        leaf = name.rsplit(".", 1)[-1]
        if leaf not in ALLOWED_FIELDS:
            return f"data_used 引用了白名单外的字段：{name}"
        return None
    if name not in ALLOWED_FIELDS:
        return f"data_used 引用了白名单外的字段：{name}"
    if name not in present:
        return f"data_used 引用了本次上下文里没有的字段：{name}"
    return None


def _path_exists(ctx: dict, path: str) -> bool:
    """按点路径在上下文里查，支持从任意嵌套层开始的省略写法。

    模型会写 `opponents.leader.best_lap`，也会偷懒写成 `leader.best_lap`。
    两种都算合理——只要这条路径在本次上下文里真的走得通。
    先试整条路径，再试各层的后缀路径；任一层缺失即视为不存在。
    """
    if "." not in path:
        return False
    parts = path.split(".")

    def descends(node, rest) -> bool:
        if not rest:
            return True
        part, tail = rest[0], rest[1:]
        if not isinstance(node, dict) or part not in node:
            return False
        return descends(node[part], tail)

    if descends(ctx, parts):
        return True

    # 省略前缀：从任意嵌套 dict 起试着走完整条路径
    def walk(node) -> bool:
        if not isinstance(node, dict):
            return False
        if descends(node, parts):
            return True
        return any(walk(v) for v in node.values())

    return walk(ctx)


def _present_names(ctx: dict) -> set[str]:
    """列出本次上下文里实际可引用的名字：层名 + 所有叶子字段名。"""
    names: set[str] = set()

    def walk(node, key=None):
        if key is not None:
            names.add(str(key))
        if isinstance(node, dict):
            for k, v in node.items():
                names.add(str(k))
                walk(v, k)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v, key)

    walk(ctx)
    return names


def _segment_layer(ctx: dict) -> dict | None:
    node = ctx.get("segments") if isinstance(ctx, dict) else None
    return node if isinstance(node, dict) else None


def _segment_count(ctx: dict) -> int | None:
    node = _segment_layer(ctx)
    if not node:
        return None
    count = node.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 64:
        return None
    return count


def _segment_deltas(ctx: dict) -> list[float | None]:
    """上下文里对比车逐段差的**带符号**列表（target - mine）。"""
    node = _segment_layer(ctx) or {}
    target = node.get("target") if isinstance(node.get("target"), dict) else None
    raw = (target or {}).get("deltas")
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[float | None] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out.append(None)
        else:
            out.append(float(value))
    return out


def _check_segments(text: str, stripped: str, ctx: dict) -> tuple[str | None, str]:
    """校验分段表述（v1.5.4），返回 (错误, 摘掉分段后的文本)。

    两件事单独裁决，因为它们在上下文里都不是「某个字段的数字」：
      · 段号：按 segments.count 判范围（第 7 段 / 共 3 段必须拒绝）；
      · 段差：必须对上对比车该段的 |delta|，方向词还要与符号一致。
    「第 2 段用时 25.3 秒」不是段差，留给普通数字闸按段用时核对。
    """
    if not _SECTOR_REF_RE.search(text):
        return None, stripped
    count = _segment_count(ctx)
    if count is None:
        return "回答引用了分段数据，但本次上下文没有 segments 层", stripped
    deltas = _segment_deltas(ctx)

    # 硬边界：模型也不能把整圈所有分段逐条念完；全量请求在入口已拦成
    # AST_REFUSE_LATER，这里兜住模型自由发挥。
    referenced = {
        _sector_number(m.group(1)) for m in _SECTOR_REF_RE.finditer(text)
    }
    referenced.discard(None)
    if len(referenced) >= count:
        return "不能播报全部分段", stripped

    # 先处理「段差」整段：它的文本里包含「第 N 段」，先摘掉才不会把
    # 「比前车慢 0.20 秒」里的 0.20 留到普通数字闸去误杀。
    for match in _SECTOR_DELTA_RE.finditer(text):
        number = _sector_number(match.group(1))
        direction, raw = match.group(2), match.group(3)
        if number is None:
            return f"分段编号无法识别：{match.group(1)}", stripped
        if not 1 <= number <= count:
            return f"分段编号 {number} 超出上下文的分段数 {count}", stripped
        delta = deltas[number - 1] if number - 1 < len(deltas) else None
        value = float(raw)
        if delta is None:
            return f"第 {number} 段的段差在上下文中没有对应值", stripped
        if abs(abs(delta) - value) > _tolerance(raw):
            return f"第 {number} 段 {raw} 秒与上下文段差 {delta:+.2f} 不符", stripped
        # 方向词不能反着说：慢/落后 = 对方更快（delta < 0），快/领先 = delta > 0。
        loss = -delta
        if direction in ("慢", "落后", "多") and loss <= 0:
            return f"第 {number} 段说「{direction}」但上下文是领先", stripped
        if direction in ("快", "领先", "少") and loss >= 0:
            return f"第 {number} 段说「{direction}」但上下文是落后", stripped
        stripped = stripped.replace(match.group(0), " ")

    for match in _SECTOR_REF_RE.finditer(text):
        number = _sector_number(match.group(1))
        if number is None:
            return f"分段编号无法识别：{match.group(1)}", stripped
        if not 1 <= number <= count:
            return f"分段编号 {number} 超出上下文的分段数 {count}", stripped
        stripped = stripped.replace(match.group(0), " ")

    return None, stripped


def check_numbers(answer: str, ctx: dict, question: str) -> str | None:
    """回答里的每个数字都必须能在上下文中找到出处。"""
    text = answer or ""

    # 先摘掉分秒表述，避免「1 分 24 秒」里的数字被后面的规则重复处理
    min_sec_spans = [m.group(0) for m in _MIN_SEC_RE.finditer(text)]
    stripped = text
    for span in min_sec_spans:
        stripped = stripped.replace(span, " ")

    # 分段表述单独裁决（段号 / 段差），通过后从 stripped 里摘掉，
    # 剩下的数字继续走原来的普通规则。
    segment_error, stripped = _check_segments(text, stripped, ctx)
    if segment_error:
        return segment_error

    # 中文数字一律拒绝：让它改用阿拉伯数字，比猜「一点八」的含义可靠。
    # 先摘掉量词搭配（「上一圈」的「一」不是数值），再判断剩下的有没有中文数字。
    # 例外：问题里本来就有的说法（用户自己说了「五圈」）。
    measure_scrubbed = stripped
    for pattern in _CN_MEASURE_PATTERNS:
        measure_scrubbed = pattern.sub(" ", measure_scrubbed)
    cn_in_question = set(_CN_DIGIT_RE.findall(question or ""))
    cn_in_answer = set(_CN_DIGIT_RE.findall(measure_scrubbed))
    if cn_in_answer - cn_in_question:
        return "回答含中文数字，要求改用阿拉伯数字"

    numbers = _flatten(ctx)
    premises = _question_premises(question)

    ordinals = {float(x) for x in _ORDINAL_RE.findall(text)}
    percents = {float(x) for x in _PERCENT_RE.findall(text)}
    min_sec_seconds = _seconds_from_min_sec(text)

    # 分秒表述的数字已从 stripped 里整段移除，因此这里不再需要「字面量跳过表」。
    # 审计 A10：旧实现按字面量跳过（consumed），「1 分 23.5 秒」会让答案别处
    # 同字面量的「1 升」一起免检——现在只按文本位置处理。
    for match in _NUM_RE.finditer(stripped):
        literal = match.group()
        unsigned = float(literal)
        # 用户当前提给出的比较数值可以复述（「确实慢了 0.3 秒」）；
        # 问题里普通出现的数字不豁免（审计 A10：不能拿问题数字给断言洗白）。
        if unsigned in premises:
            continue
        # 序数：必须与 position / class_position 之一相符，否则拒绝
        if unsigned in ordinals:
            pos = [v for k, v in numbers if k.endswith("position")]
            if any(abs(unsigned - p) <= 0.051 for p in pos):
                continue
            return f"名次 {literal} 与遥测不符"
        # 百分比：只接受 0-1 比例值 ×100（容差 0.5），不接受任意数值近似
        if unsigned in percents:
            scaled = [c * 100 for _, c in numbers if 0.0 <= c <= 1.0]
            if any(abs(unsigned - s) <= 0.51 for s in scaled):
                continue
            return f"百分比 {literal}% 在上下文中没有对应值"
        # 普通数值：带符号比对（负号不能被当成同绝对值的正数）
        value = _signed_value(stripped, match)
        matched = [(path, candidate) for path, candidate in numbers
                   if abs(value - candidate) <= _tolerance(literal)]
        if not matched:
            return f"数值 {literal} 在上下文中没有对应值"
        # 单位 → 字段绑定：18.4 升不能变成 18.4 圈（审计 A10）
        unit = _following_unit(stripped, match.end())
        if unit is not None and not _unit_matches(unit[1], matched):
            return f"数值 {literal} 与单位「{unit[0]}」在上下文中没有对应字段"

    # 分秒表述本身也要能对上总秒数
    for total in min_sec_seconds:
        # 口语常把秒四舍五入到整数，秒部分留 ±0.6 余地
        if any(abs(total - c) <= 0.6 for _, c in numbers):
            continue
        return f"分秒表述 {total:.1f} 秒在上下文中没有对应值"

    return None


def run_all(payload: dict, ctx: dict, question: str, max_chars: int
            ) -> tuple[bool, dict[str, str]]:
    """依次跑四道闸，返回 (是否全部通过, 每道闸的结论)。

    第四道 ``content``（审计 A11）只在**失败**时写进 checks，保持通过时
    ``{"length","data","number"}`` 的既有形状不变，免得既有调用方断言崩掉。
    """
    answer = (payload or {}).get("answer", "") or ""
    checks: dict[str, str] = {}
    reasons: list[str] = []

    for name, failed in (
        ("length", check_length(answer, max_chars)),
        ("data", check_data((payload or {}).get("data_used", []), ctx)),
        ("number", check_numbers(answer, ctx, question)),
        ("content", check_content(answer, question)),
    ):
        checks[name] = "fail" if failed else "pass"
        if failed:
            reasons.append(failed)

    if reasons:
        checks["reasons"] = "; ".join(reasons)
    else:
        checks.pop("content", None)
    return (not reasons), checks
