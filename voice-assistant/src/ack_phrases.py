"""等待语音（ack）选择器：AI 思考时先播一句短话，避免干等无声。

为什么单独成模块：
    抽取策略（洗牌袋）是可以被穷举测试的纯逻辑。放进 engine 里会被
    「Assistant 构造很重」拖累，测试没法简洁地表达「一轮内不重复」这件事。

msgId 来源：``content/assistant-extra.json`` 里的 AST_ACK_01..AST_ACK_20，
    由 TTS 脚本合成进 assistant-extra 语音包。素材缺失时 ``pick`` 返回
    ``None``，调用方静默跳过——等待语音是装饰，不能因为缺素材就报错。
"""
from __future__ import annotations

import random

# 一轮的完整素材集合。顺序只影响洗牌前的初始袋，不影响随机性。
ACK_MSG_IDS: tuple[str, ...] = tuple(f"AST_ACK_{i:02d}" for i in range(1, 21))


class AckPicker:
    """洗牌袋抽取：一轮内不重复，抽完自动重洗（跨轮重置）。

    为什么不用纯随机：20 句里纯随机很容易连着抽到同一句，用户会以为
    软件卡住了在重复播；洗牌袋保证一轮内每句只出现一次，长会话里
    20 句抽完自动开新一轮，不会缺素材。
    """

    def __init__(self, msg_ids=None, rng: random.Random | None = None):
        self.msg_ids = tuple(msg_ids or ACK_MSG_IDS)
        self._rng = rng or random.Random()
        self._bag: list[str] = []

    def reset(self) -> None:
        """跨轮重置：丢掉当前袋子，下一轮重新洗牌。"""
        self._bag = []

    def pick(self, available=None) -> str | None:
        """抽一条 msgId；没有可用素材时返回 None。

        ``available`` 是过滤函数（如 ``voice_pack.has``）：素材可能还没
        重建/换包，抽到缺失的等于没播，所以只在真能播的条目里抽。
        """
        pool = [m for m in self.msg_ids if available is None or available(m)]
        if not pool:
            return None
        # 先清掉袋子里本轮已不可用的条目（素材可能刚被删/换包）：
        # 留着它们会抽到缺失的 msgId，播放端静默跳过，等于这一轮少播一句。
        self._bag = [m for m in self._bag if m in pool]
        if not self._bag:
            self._bag = list(pool)
            self._rng.shuffle(self._bag)
        return self._bag.pop()
