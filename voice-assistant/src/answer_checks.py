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
_CN_DIGIT_RE = re.compile(r"[一二三四五六七八九十零百千万]")

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
    """容差随小数位收紧：整数 0.05，一位小数 0.06。"""
    if "." in number_text:
        decimals = len(number_text.split(".")[1])
        return 0.05 + 0.01 * decimals
    return 0.05


def _matches(value: float, candidates: list[tuple[str, float]]) -> bool:
    return any(abs(value - c) <= 0.051 for _, c in candidates)


def _seconds_from_min_sec(answer: str) -> list[float]:
    """把「1 分 23 秒」这类表述换算成总秒数，用于与秒制候选值比对。"""
    return [int(m) * 60 + float(s) for m, s in _MIN_SEC_RE.findall(answer)]


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
    q_numbers = {float(x) for x in _NUM_RE.findall(question or "")}

    ordinals = {float(x) for x in _ORDINAL_RE.findall(text)}
    percents = {float(x) for x in _PERCENT_RE.findall(text)}
    min_sec_seconds = _seconds_from_min_sec(text)

    # 只有分秒表述里的两个数字需要跳过独立校验——它们由末尾的总秒数检查统一裁决。
    # 序数与百分比**不能**放进这里：那样会绕过各自的验证分支，导致
    # 「第 2 名」（实际第 4）与「91%」（实际 62%）也被放行。
    consumed: set[str] = set()
    for m in _MIN_SEC_RE.finditer(text):
        consumed.update({m.group(1), m.group(2)})

    for literal in _NUM_RE.findall(stripped):
        value = float(literal)
        if literal in consumed:
            continue
        # 来自问题本身的数字可豁免（「确实慢了 0.3 秒」）
        if value in q_numbers:
            continue
        # 序数：必须与 position / class_position 之一相符，否则拒绝
        if value in ordinals:
            pos = [v for k, v in numbers if k.endswith("position")]
            if any(abs(value - p) <= 0.051 for p in pos):
                continue
            return f"名次 {literal} 与遥测不符"
        # 百分比：只接受 0-1 比例值 ×100（容差 0.5），不接受任意数值近似
        if value in percents:
            scaled = [c * 100 for _, c in numbers if 0.0 <= c <= 1.0]
            if any(abs(value - s) <= 0.51 for s in scaled):
                continue
            return f"百分比 {literal}% 在上下文中没有对应值"
        # 普通数值：直接容差比对
        if any(abs(value - c) <= _tolerance(literal) for _, c in numbers):
            continue
        return f"数值 {literal} 在上下文中没有对应值"

    # 分秒表述本身也要能对上总秒数
    for total in min_sec_seconds:
        # 口语常把秒四舍五入到整数，秒部分留 ±0.6 余地
        if any(abs(total - c) <= 0.6 for _, c in numbers):
            continue
        return f"分秒表述 {total:.1f} 秒在上下文中没有对应值"

    return None


def run_all(payload: dict, ctx: dict, question: str, max_chars: int
            ) -> tuple[bool, dict[str, str]]:
    """依次跑三道闸，返回 (是否全部通过, 每道闸的结论)。"""
    answer = (payload or {}).get("answer", "") or ""
    checks: dict[str, str] = {}
    reasons: list[str] = []

    for name, failed in (
        ("length", check_length(answer, max_chars)),
        ("data", check_data((payload or {}).get("data_used", []), ctx)),
        ("number", check_numbers(answer, ctx, question)),
    ):
        checks[name] = "fail" if failed else "pass"
        if failed:
            reasons.append(failed)

    if reasons:
        checks["reasons"] = "; ".join(reasons)
    return (not reasons), checks
