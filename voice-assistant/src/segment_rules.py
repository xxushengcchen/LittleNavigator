"""分段问题的硬边界（由底层统一执行，不交给模型自由发挥）。

用户明确要求：
1. 分段相关的问题全部交给 AI 回答；
2. 任何时候都不能把全部分段成绩念出来；
3. 问全部分段成绩时，用 voice pack 里 AST_REFUSE_LATER（“专心开车…”）的内容。

`is_full_split` 在 engine 入口、router 本地兜底各拦一次，模型也拿不到这类问题；
`is_segment_question` 只负责把普通分段问题推进 AI 分析路径。
"""
from __future__ import annotations

# 明确要“全部/各段/每段/分段成绩”的说法：一律不回答数据。
FULL_SPLIT_WORDS: tuple[str, ...] = (
    "全部分段", "所有分段", "全部赛段", "所有赛段",
    "每一段", "每段", "各段", "全段", "所有段",
    "上一圈分段", "上圈分段", "刚才那圈分段", "完整分段", "分段列表",
    "分段成绩", "分段用时", "赛段用时", "分段圈速",
    "全部圈速", "所有圈速",
)

# 分段相关问题：送 AI 分析，而不是走本地“逐段报数”。
SEGMENT_WORDS: tuple[str, ...] = (
    "分段", "分断", "赛段", "赛断", "哪一段", "哪段", "哪一断", "哪断",
    "段速", "段用时", "丢时间", "丢了时间", "掉时间",
)

CORNER_WORDS: tuple[str, ...] = ("弯", "彎")


def is_full_split(text: str) -> bool:
    q = text or ""
    return any(word in q for word in FULL_SPLIT_WORDS)


def is_segment_question(text: str) -> bool:
    q = text or ""
    return any(word in q for word in SEGMENT_WORDS)


def is_corner_question(text: str) -> bool:
    q = text or ""
    return any(word in q for word in CORNER_WORDS)
