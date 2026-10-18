"""AI 编排层：决定一个问题该不该走模型，以及走完整条链。

这是把前九个模块缝起来的地方，也是档位规则唯一落地的地方。

为什么档位矩阵单独抽成 `should_engage` 纯函数：
    它是安全边界的形状。放在大函数里用一堆 if 判断，改动时没人能一眼看懂
    「什么情况下会调模型」。抽成纯函数后，它可以被穷举测试，也便于在
    引擎里复用同一份判断。
"""
from __future__ import annotations

import json
import pathlib
import time

from . import ai_constants as const
from . import answer_checks, race_context
from .ai_learn import LearnStore, fill_template
from .llm_engine import AdaptiveBreaker, LlmClient, LlmError
from .query_class import QueryKind
from .tts_engine import TtsEngine, TtsError


class AiResult:
    """一次 AI 处理的结果。字段刻意扁平，便于直接塞进日志与界面状态。"""

    def __init__(self, ok: bool = False, text: str = "", source: str = "refused",
                 reason: str = "", checks: dict | None = None,
                 latency_ms: int = 0, dynamic: bool = False,
                 template: dict | None = None):
        self.ok = ok
        self.text = text
        self.source = source          # "learn" | "model" | "refused"
        self.reason = reason
        self.checks = checks or {}
        self.latency_ms = latency_ms
        # 复盘入库用：动态型必须带合法模板，否则只能按知识型处理
        self.dynamic = dynamic
        self.template = template

    def as_dict(self) -> dict:
        return {"ok": self.ok, "text": self.text, "source": self.source,
                "reason": self.reason, "checks": self.checks,
                "latency_ms": self.latency_ms,
                "dynamic": self.dynamic, "template": self.template}


def should_engage(kind: QueryKind, mode: str, rule_ok: bool,
                  intent: str | None = None,
                  whitelist: list[str] | None = None) -> bool:
    """档位矩阵：这句话该不该交给模型。

    安全类任何档位都不放行——这是整个功能的前提，不是可配置项。

    | 分类     | full | analysis_key | whitelist |
    |----------|------|--------------|-----------|
    | SAFETY   |  否  |      否      |    否     |
    | ANALYSIS |  是  |      是      |    是     |
    | KNOWN(ok)|  否  |      否      |    否     |
    | KNOWN(x) |  是  |      否      | 仅白名单  |
    | UNKNOWN  |  是  |      否      |    否     |

    analysis_key 档位下「按住分析键」是用户显式动作，此时 ANALYSIS 仍放行；
    KNOWN/UNKNOWN 不放行——没听懂就照实说没听懂，比花 3 秒猜更好。
    """
    if kind is QueryKind.SAFETY:
        return False

    if kind is QueryKind.ANALYSIS:
        return True

    if kind is QueryKind.KNOWN:
        if rule_ok:
            return False
        if mode == "full":
            return True
        if mode == "whitelist":
            return intent in (whitelist or [])
        return False          # analysis_key

    # UNKNOWN
    return mode == "full"


class AiPipeline:
    """把分类、学习库、模型、校验、TTS 串成一条链。

    时序约束（来自 spec 第六节）：
      · 学习库命中就不调模型（便宜且数值由模板重算）
      · 模型调用受 LlmClient 的硬截止与单飞约束
      · 三道闸任一不过即降级为「答不了」，不播半句话
    """

    def __init__(self, cfg: dict, store: LearnStore | None = None, out=None):
        import sys

        self.cfg = cfg
        self.out = out or sys.stdout
        ai = (cfg or {}).get("ai", {}) or {}
        self.mode = ai.get("mode") or "whitelist"
        self.whitelist = list(ai.get("whitelist_intents") or [])
        self.max_answer_chars = const.clamp_int(
            ai.get("max_answer_chars", const.MAX_ANSWER_CHARS_HARD),
            const.MAX_ANSWER_CHARS_HARD, "ai.max_answer_chars")
        self.max_input_tokens = const.MAX_INPUT_TOKENS_HARD
        self.breaker = AdaptiveBreaker()
        self.store = store

        self.client: LlmClient | None = None
        if ai.get("base_url") and ai.get("api_key"):
            self.client = LlmClient(
                ai.get("base_url", ""), ai.get("api_key", ""),
                ai.get("model", ""),
                timeout_s=ai.get("timeout_s", const.LLM_DEADLINE_HARD_MAX),
                max_tokens=ai.get("max_tokens", const.MAX_OUTPUT_TOKENS_HARD))

        self.tts: TtsEngine | None = None
        tts_cfg = ai.get("tts") or {}
        adapter = tts_cfg.get("adapter") or "off"
        if adapter != "off":
            try:
                self.tts = TtsEngine(adapter, tts_cfg)
            except ValueError:
                self.tts = None

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """有没有条件走模型。界面据此显示「未配置」。"""
        return self.client is not None and not self.breaker.should_skip()

    def status(self) -> dict:
        return {"available": self.available,
                "configured": self.client is not None,
                "breaker": self.breaker.state,
                "mode": self.mode,
                "tts": "on" if self.tts else "off"}

    # ------------------------------------------------------------------
    def handle(self, question: str, snap, kind: QueryKind, laps: list[dict],
               rule_ok: bool = False, intent: str | None = None) -> AiResult:
        """走完整条链。任何失败都返回 ok=False，不抛异常给引擎。"""
        started = time.monotonic()

        def done(result: AiResult) -> AiResult:
            result.latency_ms = round((time.monotonic() - started) * 1000)
            self._log(question, kind, result)
            return result

        if not should_engage(kind, self.mode, rule_ok, intent, self.whitelist):
            return done(AiResult(reason="当前档位不接管这类问题"))

        # 1) 学习库优先：模板能用当下数值重算，比固定答案可靠
        learned = self._from_learn(question, snap, laps, kind)
        if learned is not None:
            return done(learned)

        # 2) 模型
        if self.client is None:
            return done(AiResult(reason="AI 未配置"))
        if self.breaker.should_skip():
            return done(AiResult(reason="模型连续超时，已暂停"))

        ctx = race_context.build(snap, laps, kind, question)
        ctx = race_context.trim(ctx, self.max_input_tokens)

        try:
            payload = self.client.ask(ctx, question)
        except LlmError as exc:
            timed_out = "timeout" in str(exc).lower() or "超时" in str(exc)
            self.breaker.record(self.client.last_latency_ms or 0, timed_out)
            return done(AiResult(reason=f"模型调用失败：{exc}"))

        self.breaker.record(self.client.last_latency_ms or 0, False)

        if payload.get("refused"):
            return done(AiResult(reason=payload.get("reason") or "模型拒绝回答"))

        text = (payload.get("answer") or "").strip()
        if not text:
            return done(AiResult(reason="模型没有给出回答"))

        ok, checks = answer_checks.run_all(payload, ctx, question,
                                           self.max_answer_chars)
        if not ok:
            return done(AiResult(reason="校验未通过：" + checks.get("reasons", ""),
                                 checks=checks))

        template = payload.get("template")
        result = AiResult(
            ok=True, text=text, source="model", checks=checks,
            dynamic=bool(payload.get("dynamic")) and isinstance(template, dict),
            template=template if isinstance(template, dict) else None)
        self._maybe_speak(text, result)
        return done(result)

    # ------------------------------------------------------------------
    def _from_learn(self, question: str, snap, laps: list[dict],
                    kind: QueryKind) -> AiResult | None:
        if self.store is None:
            return None
        hit = self.store.match(question)
        if hit is None:
            return None
        kind_name, row = hit
        if kind_name == "knowledge":
            return AiResult(ok=True, text=row.get("a", ""), source="learn")
        ctx = race_context.build(snap, laps, kind, question)
        filled = fill_template(row.get("template") or {}, ctx)
        if filled is None:
            return None           # 填不出来就退回模型，不要播半句
        return AiResult(ok=True, text=filled, source="learn")

    def _maybe_speak(self, text: str, result: AiResult) -> None:
        """有 TTS 就合成；失败只记一笔，文字照常返回。

        刻意捕获 `Exception` 而不只是 `TtsError`：TTS 是可选装饰，
        它出任何问题（编码错误、依赖缺失、适配器内部 bug）都绝不能让
        已经通过校验的文字答案丢掉。
        """
        if self.tts is None:
            return
        try:
            self.tts.synth(text)
            result.checks = {**result.checks, "tts": "ok"}
        except Exception as exc:       # noqa: BLE001
            result.checks = {**result.checks, "tts": f"fail: {exc}"[:120]}

    def _log(self, question: str, kind: QueryKind, result: AiResult) -> None:
        try:
            print(f"  AI [{kind.value}] {result.source} "
                  f"{result.latency_ms}ms {result.text or result.reason}",
                  file=self.out)
        except Exception:      # noqa: BLE001
            pass
