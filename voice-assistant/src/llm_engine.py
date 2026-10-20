"""OpenAI 兼容的模型调用层：用户自填 endpoint / key / model。

三条设计约束：

1. **不绑定厂商**。按 OpenAI Chat Completions 协议发请求，用户填自己的
   `base_url` / `api_key` / `model`。国内主流厂商与本地推理大多兼容这一格式。
2. **硬截止**。比赛中 3 秒出不了答案等于没用，所以到点主动放弃，不赌
   「可能马上就回来了」。超时值从 `ai_constants` 钳制，配置只能收紧。
3. **单飞**。同一时刻只允许一个请求在路上；新问题会取消旧请求，
   避免后答的话盖住前一句。

零新依赖：用标准库 `urllib.request`，不引入 httpx/openai。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from . import ai_constants as const


class LlmError(RuntimeError):
    """模型调用失败的统一异常。调用方据此降级，不区分底层原因。"""


# ---------------------------------------------------------------- 思考开关（v1.5.2）
# 严格按契约只对「认得出的服务商」下发参数：认不出来就什么都不加，也不报错。
# 认错的代价很实在——往不认识的 OpenAI 兼容服务塞一个它不认的字段，
# 有的服务直接 400，用户只会看到「模型调用失败」，查不出原因。
_THINKING_MODES = ("off", "low")
_OPENAI_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


def thinking_provider(base_url: str, model: str) -> str:
    """认出思考参数该按哪家协议下发：deepseek / openai / unknown。

    判定只看两个线索，不做网络探测：
      · base_url 里带服务商名（deepseek / openai）
      · model 名带服务商前缀（deepseek… / o1 o3 o4 gpt-5…）
    两家都像时按契约顺序取 DeepSeek（先判）。
    """
    base = (base_url or "").strip().lower()
    name = (model or "").strip().lower()
    if "deepseek" in base or name.startswith("deepseek"):
        return "deepseek"
    if "openai" in base or any(name.startswith(p) for p in _OPENAI_MODEL_PREFIXES):
        return "openai"
    return "unknown"


def thinking_supported(base_url: str, model: str) -> bool:
    """界面提示用：这家服务商能不能保证听懂思考开关。"""
    return thinking_provider(base_url, model) != "unknown"


def thinking_params(base_url: str, model: str, mode: str) -> dict:
    """按服务商与档位生成请求体附加参数。

    | 服务商   | off                          | low                                    |
    |----------|------------------------------|----------------------------------------|
    | DeepSeek | {"thinking": {"type": "disabled"}} | {"thinking": {"type": "enabled"}, "reasoning_effort": "low"} |
    | OpenAI   | （不加）                      | {"reasoning_effort": "low"}            |
    | 其他     | （不加）                      | （不加）                                |

    off 时 DeepSeek 必须显式关：deepseek-reasoner 之类的模型默认就思考，
    「不传」等于没关，延迟原样还在。
    """
    mode = mode if mode in _THINKING_MODES else "off"
    provider = thinking_provider(base_url, model)
    if provider == "deepseek":
        if mode == "off":
            return {"thinking": {"type": "disabled"}}
        return {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    if provider == "openai":
        # OpenAI 的 off 就是「什么都不传」：非推理模型本来不思考，
        # 推理模型也没有通用的「关掉」字段，硬塞只可能被 400。
        return {"reasoning_effort": "low"} if mode == "low" else {}
    # 未知服务商：契约要求不加参数、不报错，界面另行提示「不保证支持」。
    return {}


def _post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    """一次性 POST 并解析 JSON。抽成模块级函数便于测试替换。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"响应不是合法 JSON：{raw[:200]}") from exc


class AdaptiveBreaker:
    """连续超时达到阈值就打开熔断，避免用户接了个死服务还一直等。

    刻意不用 p90 之类的滑窗判据：10 次里 2 次慢就关太敏感，
    比赛中有一次网络抖动很常见。只认「连续」超时。
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._consecutive_timeouts = 0
        self._state = "closed"

    @property
    def state(self) -> str:
        return self._state

    def record(self, latency_ms: float, timed_out: bool) -> None:
        if timed_out:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= self.threshold:
                self._state = "open"
        else:
            self._consecutive_timeouts = 0
            self._state = "closed"

    def should_skip(self) -> bool:
        return self._state == "open"

    def reset_half_open(self) -> None:
        """半开：放行一次试探。成功会经 `record` 自动回到 closed。"""
        self._state = "closed"
        self._consecutive_timeouts = self.threshold - 1


class LlmClient:
    """一个可复用的客户端。线程安全：内部用锁保护单飞与取消标志。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout_s: float = 2.0, max_tokens: int = 300,
                 thinking_mode: str = "off"):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        # 配置层已校验；这里再兜一层：坏值按「关闭」处理，绝不因此拒绝发请求。
        self.thinking_mode = (thinking_mode if thinking_mode in _THINKING_MODES
                              else "off")
        # 配置只能收紧：用户写 99 也会被钳到 2.0
        self.timeout_s = const.clamp(timeout_s, const.LLM_DEADLINE_HARD_MAX,
                                     "ai.timeout_s")
        self.max_tokens = const.clamp_int(max_tokens,
                                          const.MAX_OUTPUT_TOKENS_HARD,
                                          "ai.max_tokens")
        self._lock = threading.Lock()
        # 单飞闸门：同一时刻只有一个 ask() 能发请求。见 ask() 的说明。
        self._gate = threading.Lock()
        self.last_latency_ms: float | None = None

    # ------------------------------------------------------------------
    def _endpoint(self) -> str:
        base = self.base_url
        if not base:
            raise LlmError("未配置 base_url")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _payload(self, ctx: dict, question: str) -> dict:
        system = (
            "你是 iRacing 比赛工程师。只能基于用户提供的遥测数据回答。"
            "数据里没有的，明确说「现在读不到」，绝不推测。"
            "回答简短口语化，不超过 60 字，不解释推理过程，直接给结论。"
            "不要提供调校或操作指令，不要解释处罚原因。"
            "数字必须原样引用上下文里给出的值，不要自己计算或四舍五入；"
            "并且必须使用阿拉伯数字。"
            "\n\n圈速与时长优先引用上下文里的 *_spoken 字段"
            "（如 lap_last_spoken、lap_best_spoken、time_remain_spoken）："
            "它已经是口语化的「分秒」说法，直接原样使用，不要自己把秒换算成分秒，"
            "也不要改写其中的数字。只有没有对应 *_spoken 字段时，才引用原始数值字段。"
            "\n\n若答案依赖会变化的数值（油量、圈数、胎温、差距等），"
            "必须在 template 里给出可复用的条件模板，让下次能换新数值重算："
            '{"params": ["字段名..."], '
            '"rules": [{"when": "字段 >= 字段 * 1.15", "say": "含 {字段} 的句子"}, '
            '{"when": "true", "say": "兜底句"}]}。'
            "when 只允许『字段 比较符 数字或字段』、可选再乘一个数字，"
            "或字符串 true；必须有 true 兜底分支；"
            "say 里的 { } 占位符只能来自 params。"
            "若答案不依赖任何会变化的数值（纯知识），template 给 null 并把 "
            "dynamic 设为 false。"
            '\n\n只输出 JSON：{"answer": "...", "confidence": 0.0-1.0, '
            '"refused": false, "data_used": ["字段名", ...], '
            '"dynamic": true 或 false, "template": 模板或 null}'
        )
        user = "遥测上下文：\n" + json.dumps(ctx, ensure_ascii=False) + \
               "\n\n问题：" + (question or "")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        # 思考开关按服务商下发（契约 §3）：认不出来就原样不加，不报错。
        body.update(thinking_params(self.base_url, self.model,
                                    self.thinking_mode))
        return body

    def ask(self, ctx: dict, question: str) -> dict:
        """发一次请求并返回解析后的 JSON。失败一律抛 LlmError。

        单飞用互斥实现：第二个请求**等**第一个结束（或超时）再发，而不是
        并发跑。原因是 `urlopen` 一旦阻塞，取消标志对它无效——并发只会让两个
        请求同时在路上、互相覆盖 `_inflight`，实测会出现「第二个问题拿到第一个
        的答案」。宁可让新问题等一个已经受限的 ≤2s 窗口，也不能张冠李戴。
        """
        if not self.api_key:
            raise LlmError("未配置 api_key")

        acquired = self._gate.acquire(timeout=self.timeout_s)
        if not acquired:
            raise LlmError("上一个请求还没结束")
        started = time.monotonic()
        try:
            body = self._payload(ctx, question)
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            try:
                data = _post_json(self._endpoint(), body, headers, self.timeout_s)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:200]
                except Exception:      # noqa: BLE001
                    pass
                raise LlmError(f"HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise LlmError(f"请求失败：{exc}") from exc

            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LlmError(f"响应结构不符：{str(data)[:200]}") from exc

            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LlmError(f"模型输出不是合法 JSON：{content[:200]}") from exc
            if not isinstance(payload, dict):
                raise LlmError("模型输出不是 JSON 对象")
            return payload
        finally:
            self.last_latency_ms = (time.monotonic() - started) * 1000
            self._gate.release()

    # ------------------------------------------------------------------
    def test_connection(self) -> dict:
        """界面「测试连接」：报连通性、往返延迟、是否支持 JSON。

        刻意复用最短请求：它测的是「用户接的这东西到底能不能用」，
        不是模型质量。
        """
        started = time.monotonic()
        try:
            body = {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            _post_json(self._endpoint(), body, headers, self.timeout_s)
            return {"ok": True, "latency_ms": round((time.monotonic() - started) * 1000)}
        except Exception as exc:       # noqa: BLE001
            return {"ok": False, "latency_ms": round((time.monotonic() - started) * 1000),
                    "error": str(exc)[:200]}
