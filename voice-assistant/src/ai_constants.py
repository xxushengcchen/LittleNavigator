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


def _reject_non_finite(value: int | float, name: str) -> None:
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

    ``int`` 单独短路返回，**不能**交给 ``math.isfinite``：Python 的 int 是任意
    精度整数，天然有限（不存在 nan/inf），而 ``math.isfinite(10**400)`` 会先把
    它转成 float，抛 ``OverflowError: int too large to convert to float``——一个
    「超大但完全合法的 token 上限」就此从「被钳到 60」退化成崩溃。
    ``bool`` 是 ``int`` 的子类，走同一条短路，同样安全。
    """
    if isinstance(value, int):
        return
    if math.isfinite(value):
        return
    if math.isnan(value):
        raise ValueError(f"配置 {name}={value!r} 是 NaN，不是合法数值")
    if value < 0:  # 只有 -inf 会落进来：nan 已在上面的分支抛错，+inf 不小于 0
        raise ValueError(f"配置 {name}={value!r} 是负无穷，不是合法数值")
    # 到这里只剩 +inf：交给调用方的 `value > hard_max` 分支钳到 hard_max 并告警。
    return


def clamp(value: float, hard_max: float, name: str) -> float:
    """把浮点配置压到硬上限以内；越界时记一条 warning 说明是哪个配置项。

    参数注解 ``float`` 不必改成 ``int | float``：按 PEP 484 的数值塔，
    ``int`` / ``bool`` 在类型检查层面就是合法的 ``float`` 实参。真正要修的是
    **返回**侧：``clamp(True, 2.0, ...)`` 原来会把 ``True`` 原样漏出去，而
    ``bool`` 只是 ``int`` 的子类、不是 ``float`` 的子类，于是调用方拿到一个
    类型上不成立的值。这里只对 ``bool`` 做一次 ``float()``，不无差别地转换
    所有输入——``float(10**400)`` 会抛 ``OverflowError``，那正是本次要修掉的
    那类回归，不能换个地方又请回来。
    """
    _reject_non_finite(value, name)
    if value > hard_max:
        logger.warning("配置 %s=%s 超过硬上限 %s，已钳制", name, value, hard_max)
        return hard_max
    return float(value) if isinstance(value, bool) else value


def clamp_int(value: int | float, hard_max: int, name: str) -> int | float:
    """整数版钳制，用于 token 与字符数这类「整数量」配置。

    参数注解是 ``int | float`` 而非 ``int``：类型注解在运行时挡不住
    ``clamp_int(float('nan'), ...)``（``isinstance(nan, int)`` 为 False，
    解释器不会拦），而配置从 YAML 读出来本就可能是浮点或非有限值，
    入口必须真的接得住 ``float``。

    返回类型如实写成 ``int | float``：正常配置传 int、拿回 int；有限的 float
    原样返回而不截断（截断会悄悄改掉调用方语义），只有 nan/±inf 会被拒绝或
    钳成整数硬上限。``bool`` 归一成 ``int``——``True`` 传进来当 token 上限虽
    不报错，但语义是「1 个 token」，返回 ``True`` 只会让下游日志更难读。
    """
    _reject_non_finite(value, name)
    if value > hard_max:
        logger.warning("配置 %s=%s 超过硬上限 %s，已钳制", name, value, hard_max)
        return hard_max
    return int(value) if isinstance(value, bool) else value
