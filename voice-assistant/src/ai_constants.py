"""AI 比赛工程师的硬上限常量与配置钳制。

用户配置可以调松，但不能越过这里的硬上限：延迟、token、回答长度一旦
被调过头，会直接吃掉「能不能在下一个弯前说出口」这条底线，所以钳制必须
发生在读配置之后、使用之前，且钳制要留痕（warning），方便事后倒查是谁配错的。

配置里的非有限值（NaN、+inf、-inf）单独处理：它们能骗过 `value > hard_max`
这种比较（NaN 与任何数比较都是 False），一路穿透到 `urlopen(timeout=nan)`
或 `max_tokens` 才炸，所以必须在钳制入口就拦下来。
"""
from __future__ import annotations

import logging
import math

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


def _reject_non_finite(value: float, name: str) -> None:
    """拦掉配置里不该出现的非有限值；`+inf` 放行，交给调用方的上限比较去钳。

    三种值语义不同，所以不统一处理：

    - ``nan``：任何比较都为 False，是最危险的一种——它会静默穿透钳制，
      最后在 ``urlopen(timeout=nan)`` / ``max_tokens=nan`` 处抛出难以归因的
      ``ValueError``。YAML 支持 ``.nan``，配置写坏了就该在这里炸出来，
      而不是静默换成硬上限（静默兜底会掩盖上游的配置生成 bug）。
    - ``-inf``：负的无穷延迟、负的无穷 token 上限没有任何合理解释，
      只可能是配置写错，钳成某个具体负数同样是掩盖问题。
    - ``+inf``：语义上就是「大到没有意义」，钳到 ``hard_max`` 正是钳制的本意，
      不丢信息，也不必报错；这里直接放行，让它走正常的超上限分支并留 warning。
    """
    if math.isfinite(value):
        return
    if math.isnan(value):
        raise ValueError(f"配置 {name}={value!r} 是 NaN，不是合法数值")
    if value < 0:  # 只剩 -inf 能走到这里（+inf 已由上面的分支分类）
        raise ValueError(f"配置 {name}={value!r} 是负无穷，不是合法数值")
    # +inf：交给调用方的 `value > hard_max` 分支钳到 hard_max 并告警。


def clamp(value: float, hard_max: float, name: str) -> float:
    """把浮点配置压到硬上限以内；越界时记一条 warning 说明是哪个配置项。"""
    _reject_non_finite(value, name)
    if value > hard_max:
        logger.warning("配置 %s=%s 超过硬上限 %s，已钳制", name, value, hard_max)
        return hard_max
    return value


def clamp_int(value: int, hard_max: int, name: str) -> int:
    """整数版钳制，用于 token 与字符数这类「整数量」配置。

    类型注解挡不住 ``clamp_int(float('nan'), ...)``（``isinstance(nan, int)``
    为 False，解释器不会拦），所以同样先过非有限值检查。
    """
    _reject_non_finite(value, name)
    if value > hard_max:
        logger.warning("配置 %s=%s 超过硬上限 %s，已钳制", name, value, hard_max)
        return hard_max
    return value
