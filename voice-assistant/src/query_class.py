"""问题分类预检：决定一句话该不该交给大模型。

为什么需要独立于意图识别的分类：
    旗舰问句「刚才那个弯为什么慢了0.3秒」在短语库阶段就没命中
    （最高分 0.47 < 0.66 阈值），`intent` 为 None 时根本不进 router.route()。
    如果档位规则建立在 intent 上，最想要的那类问题永远够不到模型。
    所以这里只看纯文本，`intent` 只用来区分 KNOWN 与 UNKNOWN。

为什么要区分 SAFETY 与 OPERATION：
    这两类都不该交给模型，但**原因不同**，说给用户听的话也不同。

    SAFETY   = 红线。「解释处罚」「问比赛外的事」——能做，但按规范不做。
               用户听到「这项遥测里没有，我不乱编」是合理的。

    OPERATION = 能力边界。「帮我调尾翼」「设置成软胎」——软件**根本做不到**。
               本项目的 telemetry 层是纯只读的（见 telemetry.py 首行），
               没有任何 iRacing 广播写接口。所以正确的回答是
               「软件不碰车，黑框里自己设」，而不是「我没听清」。

    把它们混成一类，用户问「设置成软胎」会听到「蛤？没听清楚，再说一遍」——
    软件听得很清楚，是做不到。这是两回事。

判定顺序固定为 SAFETY → OPERATION → ANALYSIS → KNOWN → UNKNOWN，**不能调换**：
    安全词与分析词会重叠（「为什么我被罚了」同时含「为什么」和「被罚」），
    若先判 ANALYSIS，用户问处罚原因就会被送去调模型——越过了「不解释处罚」
    这条红线。宁可错杀成安全类，也不放过一次越界。
"""
from __future__ import annotations

from enum import Enum

from .phrase_match import normalize


class QueryKind(str, Enum):
    """一句话的去向。

    继承 `str` 让成员天然等于自己的字符串值（`QueryKind.SAFETY == "safety"` 为真），
    下游拿它当分支键或直接写进 JSON 都安全。但 `str()` / f-string 的印法在不同
    Python 版本的 enum 实现里有差异，往日志里写请显式用 `.value`。
    """

    SAFETY = "safety"          # 解释处罚/规则、遥测范围外 —— 红线，拒绝
    OPERATION = "operation"    # 要求软件操作车辆 —— 能力边界，软件做不到
    ANALYSIS = "analysis"      # 为什么、分析、评价 —— 最该交给模型的一类
    KNOWN = "known"            # 短语库命中，且是规则层能直接答的事实型问题
    UNKNOWN = "unknown"        # 以上都不是


# 红线：解释处罚/规则，或问到遥测范围外的东西。能做，但按规范不做。
SAFETY_WORDS: tuple[str, ...] = (
    "处罚", "被罚", "罚我", "怎么判", "凭什么罚",
    # 遥测范围之外
    "几点", "几号", "星期几", "库存", "备用胎", "备用轮",
)

# 能力边界：要求软件代做某件事。软件是只读的，做不到。
#
# 刻意不收「怎么开」：它既可能指「这个弯怎么开」（分析），也可能指
# 「怎么开雨刷」（操作），歧义太大。归 UNKNOWN —— 不转模型，也不误杀成拒绝。
# 同理不收裸「帮我」：「帮我分析一下」是分析，「帮我调一下尾翼」才是操作，
# 所以只收带动作的「帮我开 / 帮我关 / 帮我调 / 帮我把」。
OPERATION_WORDS: tuple[str, ...] = (
    # 代劳操作车辆（口语里「调一下」「关掉」常不带工具名）
    "替我", "帮我开", "帮我关", "帮我把", "帮我调", "帮我加",
    "调高", "调低", "调到", "改成", "设置为", "设置成",
    "关掉", "把方向盘", "回正",
    # 具体驾驶输入建议：软件没有赛道几何数据，模型也只能编。
    # 注意不能只收「几档」——「现在几档」「ABS 几档」是合法遥测查询，
    # 所以只收「该几档」「几档过」这种要建议的说法。
    "该几档", "几档过", "该怎么过", "走哪条线",
)

# 需要跨数据推理、规则层给不出答案的说法。
# 「慢在哪 / 稳定吗 / 有没有进步」会被短语库当成事实型命中（query_laptime），
# 但规则层其实答不出所以然——拉进分析路径才是它们该去的地方。
ANALYSIS_WORDS: tuple[str, ...] = (
    "为什么", "什么原因", "分析", "评价", "怎么看",
    "哪里慢", "慢在哪", "稳定吗", "有没有进步", "该怎么跑", "策略",
)

# 「弱」操作词：单独出现时不足以判操作。中文里它们也做「调取/查看」讲
# （「帮我调一下上一圈的圈速」），或者出现在纯粹的分析句里
# （「我调整了刹车之后为什么推头」——那是最该交给模型的一类）。
# 所以它们排在 ANALYSIS 之后：有分析意图时优先当分析，没有时才当操作请求。
WEAK_OPERATION_WORDS: tuple[str, ...] = (
    "调整", "调一下", "关一下", "打开", "关了",
)


def classify(text: str, intent: str | None) -> QueryKind:
    """把一句话归到五类之一。

    只依赖文本与可选的意图名，不读遥测、不调外部服务，因此可以在路由之前跑。
    """
    q = normalize(text)

    # 1) 红线优先：词表重叠时以安全为准（见模块 docstring）
    if any(word in q for word in SAFETY_WORDS):
        return QueryKind.SAFETY

    # 2) 能力边界：明确的代做/驾驶输入请求
    if any(word in q for word in OPERATION_WORDS):
        return QueryKind.OPERATION

    has_analysis = any(word in q for word in ANALYSIS_WORDS)

    # 3) 分析类先于**弱**操作词：避免把「我调整了刹车之后为什么推头」
    #    这种最该交给模型的问题当成操作请求拒掉。强操作词已在第 2 步截获。
    if has_analysis:
        return QueryKind.ANALYSIS

    # 4) 没有分析意图，弱操作词才按操作请求处理
    if any(word in q for word in WEAK_OPERATION_WORDS):
        return QueryKind.OPERATION

    # 5) 短语库已命中且不是分析型 → 事实型，规则层能答就答
    #    判的是「有没有意图」：`None` 与空串都按无意图处理。现有调用方传来的是
    #    `PhraseMatcher.Hit.intent`（None 或非空字符串），空串只可能来自坏调用，
    #    把它当 KNOWN 会凭空造出一个规则层并没有命中的事实型问题。
    if intent:
        return QueryKind.KNOWN

    # 6) 什么都没识别出来
    return QueryKind.UNKNOWN
