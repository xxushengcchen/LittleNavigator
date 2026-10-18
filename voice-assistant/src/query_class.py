"""问题分类预检：决定一句话该不该交给大模型。

为什么需要独立于意图识别的分类：
    实测旗舰问句「刚才那个弯为什么慢了0.3秒」在短语库阶段就没命中
    （最高分 0.47 < 0.66 阈值），`intent` 为 None 时根本不进 router.route()。
    如果档位规则建立在 intent 上，最想要的那类问题永远够不到模型。

判定顺序固定为 SAFETY → ANALYSIS → KNOWN → UNKNOWN，**不能调换**：
    安全词与分析词会重叠（「为什么我被罚了」同时含「为什么」和「被罚」），
    若先判 ANALYSIS，用户问处罚原因就会被送去调模型——越过了「不解释处罚」
    这条红线。宁可错杀成安全类，也不放过一次越界。
"""
from __future__ import annotations

from enum import Enum

from .phrase_match import normalize


class QueryKind(str, Enum):
    """一句话的去向。继承 str 便于写日志与序列化。"""

    SAFETY = "safety"      # 操作车辆 / 解释处罚 / 范围外 —— 任何档位都不转模型
    ANALYSIS = "analysis"  # 为什么、分析、评价 —— 最该交给模型的一类
    KNOWN = "known"        # 短语库命中且是事实型问题
    UNKNOWN = "unknown"    # 以上都不是


# 需要「软件去做某件事」或「解释规则/处罚」的说法。
# 刻意不收「怎么开」：它既可能指赛道怎么跑（分析），也可能指操作（安全），
# 歧义太大，归 UNKNOWN 更稳——不转模型，也不误杀成拒绝。
SAFETY_WORDS: tuple[str, ...] = (
    # 解释处罚 / 规则
    "处罚", "被罚", "罚我", "怎么判", "凭什么罚",
    # 操作车辆。口语变体要覆盖到「调」这个动作本身：
    # 实测「能不能帮我调一下尾翼」会被「调高/调低/调到」漏掉。
    # 刻意不收「能不能帮我」这种泛化说法——「能不能帮我看看还有多少油」
    # 是查询而不是操作，收进来会把它误杀成安全拒绝。
    "替我", "帮我开", "帮我关", "帮我把", "帮我调",
    "调高", "调低", "调到", "调一下", "调整", "改成", "设置为",
    "关掉", "关掉tc", "关掉abs",
    # 遥测范围之外
    "几点", "几号", "星期几", "库存", "备用胎", "备用轮",
)

# 需要跨数据推理、规则层给不出答案的说法。
# 包含「慢在哪 / 稳定吗 / 有没有进步」这类会被短语库误命中成事实型、
# 但实际答不出所以然的问题——把它们拉进分析路径。
ANALYSIS_WORDS: tuple[str, ...] = (
    "为什么", "什么原因", "分析", "评价", "怎么看",
    "哪里慢", "慢在哪", "稳定吗", "有没有进步", "该怎么跑", "策略",
)


def classify(text: str, intent: str | None) -> QueryKind:
    """把一句话归到四类之一。

    只依赖文本与可选的意图名，不读遥测、不调外部服务，因此可以在路由之前跑。
    """
    q = normalize(text)

    # 1) 安全类优先：词表重叠时以安全为准（见模块 docstring）
    if any(word in q for word in SAFETY_WORDS):
        return QueryKind.SAFETY

    # 2) 分析类：不要求命中意图，这正是修掉旗舰用例不可达的关键
    if any(word in q for word in ANALYSIS_WORDS):
        return QueryKind.ANALYSIS

    # 3) 命中短语库且不是分析型 → 事实型，规则层能答就答
    if intent:
        return QueryKind.KNOWN

    # 4) 什么都没识别出来
    return QueryKind.UNKNOWN
