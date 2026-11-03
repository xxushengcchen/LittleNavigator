"""分段问题的硬边界（由底层统一执行，不交给模型自由发挥）。

用户明确要求：
1. 分段相关的问题全部交给 AI 回答；
2. 任何时候都不能把全部分段成绩念出来；
3. 问全部分段成绩时，用 voice pack 里 AST_REFUSE_LATER（“专心开车…”）的内容。

`is_full_split` 在 engine 入口、router 本地兜底各拦一次，模型也拿不到这类问题；
`is_segment_question` 只负责把普通分段问题推进 AI 分析路径。
"""
from __future__ import annotations

# 明确要“整份清单”的说法：一律不回答数据。
FULL_SPLIT_WORDS: tuple[str, ...] = (
    "全部分段", "所有分段", "全部赛段", "所有赛段",
    "每一段", "每段", "各段", "全段", "所有段",
    "完整分段", "分段列表", "全部分段用时",
    "全部圈速", "所有圈速",
)

# 审计 T10：这些短语只是“提到分段”，并不等于要整份清单。
# 「第二段分段用时是多少」「前车上一圈分段差多少」以前被当成全量请求硬拒答，
# 超出了“只禁止播报全部分段”的边界。现在只有在没有任何
# 指定段号 / 比较对象时才算全量。
FULL_SPLIT_WEAK: tuple[str, ...] = (
    "上一圈分段", "上圈分段", "刚才那圈分段",
    "分段成绩", "分段用时", "赛段用时", "分段圈速", "赛段成绩",
)

# 指定了某一段，或明确要跟别人/别的圈比较：这是单段/对比问题，可以回答。
import re as _re

_SEGMENT_INDEX_RE = _re.compile(
    r"第\s*[0-9一二三四五六七八九十]+\s*(?:段|分断|赛段|赛断)")
_COMPARE_WORDS: tuple[str, ...] = (
    "差", "比", "快", "慢", "多", "少", "前车", "前車", "后车", "後車",
    "前后", "相邻", "队友", "对手",
)

# 分段相关问题：送 AI 分析，而不是走本地“逐段报数”。
SEGMENT_WORDS: tuple[str, ...] = (
    "分段", "分断", "赛段", "赛断", "哪一段", "哪段", "哪一断", "哪断",
    "段速", "段用时", "丢时间", "丢了时间", "掉时间",
)

CORNER_WORDS: tuple[str, ...] = ("弯", "彎")


def _asks_single_or_comparison(q: str) -> bool:
    if _SEGMENT_INDEX_RE.search(q):
        return True
    return any(word in q for word in _COMPARE_WORDS)


def is_full_split(text: str) -> bool:
    """是否在要“整份分段清单”。

    先看强信号（全部/每段/各段…）；弱信号（分段用时/上一圈分段…）
    只有在没有指定某一段、也没有比较对象时才算全量请求（审计 T10）。
    """
    q = text or ""
    if any(word in q for word in FULL_SPLIT_WORDS):
        return True
    if not any(word in q for word in FULL_SPLIT_WEAK):
        return False
    return not _asks_single_or_comparison(q)


def is_segment_question(text: str) -> bool:
    q = text or ""
    return any(word in q for word in SEGMENT_WORDS)


def is_corner_question(text: str) -> bool:
    q = text or ""
    return any(word in q for word in CORNER_WORDS)
