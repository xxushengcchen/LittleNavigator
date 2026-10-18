"""AI 比赛工程师的硬上限常量与配置钳制。

用户配置可以调松，但不能越过这里的硬上限：延迟、token、回答长度一旦
被调过头，会直接吃掉「能不能在下一个弯前说出口」这条底线，所以钳制必须
发生在读配置之后、使用之前，且钳制要留痕（warning），方便事后倒查是谁配错的。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 三道延迟是同一笔预算的拆分：LLM 规划 + TTS 出声 必须落在总上限之内。
LLM_DEADLINE_HARD_MAX = 2.0
TTS_DEADLINE_HARD_MAX = 1.5
AI_TOTAL_DEADLINE_HARD_MAX = 3.5

# token 与字符数上限共同压制「模型开始长篇大论」的成本与延迟。
MAX_INPUT_TOKENS_HARD = 1500
MAX_OUTPUT_TOKENS_HARD = 300

# 司机在弯里只听得进一句话，超过就该截断而不是继续读。
MAX_ANSWER_CHARS_HARD = 60

# 同一时刻只允许一次 AI 请求在路上：并发既浪费配额，也会让后答的话覆盖前一句。
SINGLE_INFLIGHT = True


def clamp(value: float, hard_max: float, name: str) -> float:
    """把浮点配置压到硬上限以内；越界时记一条 warning 说明是哪个配置项。"""
    if value > hard_max:
        logger.warning("配置 %s=%s 超过硬上限 %s，已钳制", name, value, hard_max)
        return hard_max
    return value


def clamp_int(value: int, hard_max: int, name: str) -> int:
    """整数版钳制，用于 token 与字符数这类「整数量」配置。"""
    if value > hard_max:
        logger.warning("配置 %s=%s 超过硬上限 %s，已钳制", name, value, hard_max)
        return hard_max
    return value
