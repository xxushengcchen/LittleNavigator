"""问答流水线：问题文本 → 意图 → 遥测 → msgId 序列 → 播放。

键盘控制台（src/console.py）和语音入口（src/voice_app.py）共用这一层，
区别只在文本是从哪来的：敲键盘，还是 ASR 听写。

意图解析的顺序：
  1. 受限短语模糊匹配（src/phrase_match.py）—— 对不上就明确拒绝
  2. L1 关键词兜底（可在 config 里关掉）—— 只压在短语库后面，少猜
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import threading
import functools
import collections
from typing import Any

from . import config as cfg_mod
from . import intents, router, telemetry
from .phrase_match import PhraseMatcher
from .player import Player
from .voicepack import VariantPicker, VoicePack
from .ai_pipeline import AiPipeline
from .ack_phrases import AckPicker
from .query_class import QueryKind, classify
from . import car_control, command_match

# 拒绝话术（在 assistant-extra 素材包里，同一音色合成）
REFUSE_HEARD = "AST_REFUSE_HEARD"    # 没听清 / 对不上短语库
REFUSE_CANT = "AST_REFUSE_CANT"      # 听懂了，但遥测答不了
AI_SEE_SCREEN = "AST_AI_SEE_SCREEN"  # AI 答了但没配 TTS：请用户看屏幕
NO_CAR_CONTROL = "AST_NO_CAR_CONTROL"  # 改车未启用 / 这件事做不到
CC_CONFIRM = {"tyre": "AST_CC_TYRE", "fuel": "AST_CC_FUEL",
              "tearoff": "AST_CC_WS", "fast_repair": "AST_CC_FR",
              "clear": "AST_CC_CLEAR", "clear_tires": "AST_CC_CLEAR",
              "clear_fuel": "AST_CC_CLEAR", "clear_ws": "AST_CC_CLEAR",
              "clear_fr": "AST_CC_CLEAR"}
CC_FAIL = "AST_CC_FAIL"


def exclusive(fn):
    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        with self._operation_lock:
            previous = self.busy
            self.busy = True
            try:
                return fn(self, *args, **kwargs)
            finally:
                self.busy = previous
    return wrapped


class Assistant:
    def __init__(self, cfg: dict, *, no_play: bool = False, gap_ms: int = 60,
                 force_sim: bool = False, sim_advance: bool = False,
                 pack_dir: str | None = None, out=sys.stdout):
        self.cfg = cfg
        cfg_mod.fix_console_encoding()
        self.gap_ms = gap_ms
        self.out = out
        self.busy = False                      # 播报中 / 处理中，语音入口据此忽略 PTT
        # Overlay 数据源：听到什么、在做什么、答了什么。写入只做整项赋值，
        # 读取方（app 侧）取 dict 副本即可，不需要额外锁。
        self.live: dict[str, str] = {"heard": "", "state": "idle",
                                     "answer": "", "error": ""}
        self.ack_picker = AckPicker()
        self._operation_lock = threading.RLock()
        vp = cfg["voice_pack"]
        roots = [cfg_mod.resolve(cfg, pack_dir or vp["dir"])]
        self.extra_dir = vp.get("extra_dir")
        if self.extra_dir and not pack_dir:
            roots.append(cfg_mod.resolve(cfg, self.extra_dir))
        if vp.get('telemetry_dir') and not pack_dir:
            roots.append(cfg_mod.resolve(cfg,vp['telemetry_dir']))
        self.pack = VoicePack.load_many(roots)
        self.stats = self.pack.stats()
        self.picker = VariantPicker(self.pack, vp["cooldown"],
                                    cfg_mod.resolve(cfg, vp["state_file"]))
        self.matcher = PhraseMatcher()
        self.tel, self.tel_warn = telemetry.build(cfg, force_sim=force_sim, sim_advance=sim_advance)
        self.derive = telemetry.DerivedTracker(cfg["telemetry"]["ring_buffer"]["keep_laps"])
        from .duck import GameDucker
        from .listener import resolve_device

        pb = cfg.get("playback", {})
        duck_cfg = pb.get("ducking", {}) or {}
        ducker = GameDucker(enabled=duck_cfg.get("enabled", False),
                                processes=duck_cfg.get("processes"),
                                amount_db=duck_cfg.get("amount_db", -12.0),
                                out=out)
        self.ducker = ducker
        self.player = Player(enabled=pb.get("enabled", True) and not no_play,
                             device=resolve_device(pb.get("device")),
                             volume=pb.get("volume", 1.0),
                             ducker=ducker, out=out)
        self.log_dir = cfg_mod.resolve(cfg, cfg["logging"]["dir"])
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.qa_log = cfg_mod.resolve(cfg, cfg["logging"]["qa_log"])
        self.miss_log = cfg_mod.resolve(cfg, cfg["intent"]["miss_log"]["path"])
        self.history = collections.deque(maxlen=500)
        self.sampler = telemetry.TelemetrySampler(self.tel, self.derive,
            cfg["telemetry"]["poll_hz"], cfg["telemetry"].get("thread_priority"))
        # AI 只在显式启用时构造；不启用时下面所有入口都判空，行为与接入前完全一致。
        self.ai = self._build_ai(cfg)
        # 语音改车：默认关闭。未启用时 self.car.enabled 为假，所有入口判空。
        try:
            self.car = car_control.CarController(cfg, out=self.out)
        except Exception as exc:      # noqa: BLE001
            print(f"  ⚠ 改车控制器初始化失败：{exc}", file=self.out)
            self.car = None

    # ------------------------------------------------------------------ AI
    def _build_ai(self, cfg: dict) -> AiPipeline | None:
        """AI 未启用就返回 None，避免构造无用对象与误触发网络。"""
        ai_cfg = (cfg or {}).get("ai") or {}
        if not ai_cfg.get("enabled"):
            return None
        store = None
        learn_dir = cfg.get("_ai_learn_dir")
        if learn_dir:
            try:
                from .ai_learn import LearnStore
                store = LearnStore(learn_dir)
            except Exception:      # noqa: BLE001
                store = None       # 学习库坏了不该拖垮主流程
        try:
            return AiPipeline(cfg, store=store, out=self.out, player=self.player,
                              snapshot_provider=lambda: self.snapshot(),
                              on_live=self._on_ai_answer)
        except Exception as exc:   # noqa: BLE001
            print(f"  ⚠ AI 初始化失败：{exc}", file=self.out)
            return None

    # ------------------------------------------------------------------ live
    def _live(self, **fields: str) -> None:
        """更新 Overlay 数据源。

        只做 dict 整项赋值：Python 里 dict 单个键赋值是原子的，读方
        （app 侧）复制一份再渲染，就不需要为这个展示层加锁。
        """
        live = getattr(self, "live", None)
        if isinstance(live, dict):
            live.update(fields)

    def set_heard(self, text: str) -> None:
        """记下最近一次听到的问题（Overlay 第一行）。"""
        self._live(heard=(text or "").strip())

    def _on_ai_answer(self, text: str) -> None:
        """AI 文案已生成、TTS 之前：先把答案写进 live。

        由 AiPipeline 在真正出声前回调——TTS 是同步阻塞的，等播完再写
        等于晚一整句的时长，Overlay 就看不到「正在说这句」了。
        """
        self._live(state="answer", answer=text or "", error="")

    @property
    def _ai_all_mode(self) -> bool:
        """「全部交给 AI」档是否生效：AI 存在、未熔断、mode=all。"""
        return (self.ai is not None and self.ai.available
                and getattr(self.ai, "mode", None) == "all")

    @property
    def global_stop_local(self) -> bool:
        """「全局交给 AI」时是否停掉本地问答语音（v1.5.2，契约 §2）。

        只对 all 档生效：其它档位里本地规则仍是主路径，停了等于没声音。
        也要求 AI 流水线真的存在——用户把 AI 关了却留着 mode=all 时，
        再停本地播报会让软件彻底沉默。
        """
        if self.ai is None or getattr(self.ai, "mode", None) != "all":
            return False
        ai_cfg = (self.cfg or {}).get("ai") or {}
        return bool(ai_cfg.get("global_stop_local", True))

    @property
    def ai_enabled(self) -> bool:
        return self.ai is not None

    def reload_switchables(self, cfg: dict) -> dict:
        """按新配置重建 AI / 改车控制器，让开关保存后立即生效。

        实测问题：设置页保存开关后，配置落盘正确，但运行态还是旧的——
        界面顶栏显示「已启用」，实际 ai.enabled 仍是旧值；反过来关闭也关不掉。
        根因是这两个对象只在 __init__ 里构造一次。这里提供显式重建入口，
        调用方必须在**没有正在执行的改车指令**时调用。
        """
        before = {"ai": self.ai is not None,
                  "car": bool(self.car and self.car.enabled)}
        self.cfg = cfg

        # AI：未启用时置 None，已启用时重建（模型/密钥可能也改了）
        try:
            self.ai = self._build_ai(cfg)
        except Exception as exc:      # noqa: BLE001
            print(f"  ⚠ AI 重建失败：{exc}", file=self.out)
            self.ai = None

        # 改车：关闭时置 None，开启时重建并重新探测可用性
        if self.car is not None:
            self.car.close()
        try:
            self.car = car_control.CarController(cfg, out=self.out)
        except Exception as exc:      # noqa: BLE001
            print(f"  ⚠ 改车控制器重建失败：{exc}", file=self.out)
            self.car = None

        after = {"ai": self.ai is not None,
                 "car": bool(self.car and self.car.enabled)}
        return {"before": before, "after": after}

    def learn_store(self):
        """学习库实例；未启用或初始化失败时返回 None。"""
        return getattr(self.ai, "store", None) if self.ai is not None else None

    def learn_candidate(self, entry_id: str) -> dict | None:
        """按 id 组装一条「待入库」记录。供复盘标记 ok 时使用。"""
        for rec in reversed(list(self.history)):
            if rec.get("review_id") != entry_id:
                continue
            template = rec.get("ai_template")
            # 只有「模型声明依赖可变数值」且「确实给了模板」才算动态型；
            # 其余按知识型入库。声明了却没给模板的，会被 add_from_review 拒绝。
            dynamic = bool(rec.get("ai_dynamic"))
            return {"question": rec.get("q"), "answer": rec.get("ai_text"),
                    "template": template if isinstance(template, dict) else None,
                    "dynamic": dynamic, "clip_path": rec.get("ai_clip")}
        return None

    def ai_status(self) -> dict:
        if self.ai is None:
            return {"enabled": False, "available": False, "configured": False,
                    "mode": None, "breaker": "closed", "tts": "off"}
        return {"enabled": True, **self.ai.status()}

    def _ai_should_engage(self, kind: QueryKind, text: str,
                          intent: str | None = None,
                          rule_ok: bool = False) -> bool:
        """档位矩阵的引擎侧入口；未启用时一律 False。"""
        if self.ai is None:
            return False
        # 空识别/空输入绝不进模型。
        # 实测：ASR 返回空串时日志先说「太短或为空，拒绝」，随后仍然调了模型，
        # 发出去的请求里「问题：」后面什么都没有，白花钱还播一条无意义回答。
        if not (text or "").strip():
            return False
        if router.asks_lap_gap(text):
            return False
        from .ai_pipeline import should_engage
        return should_engage(kind, self.ai.mode, rule_ok, intent,
                             self.ai.whitelist)

    def _disambiguation_settings(self) -> tuple[bool, float, float]:
        """读 AI 仲裁配置；老配置没有这一段时按默认值工作。"""
        ai = (self.cfg or {}).get("ai") or {}
        d = ai.get("disambiguate") or {}
        try:
            timeout = float(d.get("timeout_s", 1.0))
        except (TypeError, ValueError):
            timeout = 1.0
        try:
            min_conf = float(d.get("min_confidence", 0.6))
        except (TypeError, ValueError):
            min_conf = 0.6
        return bool(d.get("enabled", True)), timeout, min_conf

    @staticmethod
    def _intent_candidates(hit) -> list[dict]:
        """把匹配器已经算好的 top 候选整理成给 AI 的短清单。

        只带意图名、中文标签和命中的示例问法；**不带遥测数值**。
        """
        ranked = list(getattr(hit, "ranked", []) or [])
        if not ranked:
            return []
        try:
            from .knowledge import INTENT_LABELS
        except Exception:  # noqa: BLE001
            INTENT_LABELS = {}
        out: list[dict] = []
        seen: set[str] = set()
        for intent, score, phrase in ranked:
            if intent in seen:
                continue
            seen.add(intent)
            out.append({
                "intent": intent,
                "label": INTENT_LABELS.get(intent, intent),
                "examples": [str(phrase)],
                "score": round(float(score), 3),
            })
            if len(out) >= 4:
                break
        return out

    def _disambiguation_request(self, tr: dict, kind: QueryKind,
                                text: str) -> dict | None:
        """判断这句话是否需要 AI 在候选意图间仲裁。

        触发条件刻意保守：
          · AI 已配置且可用；ai.disambiguate.enabled 为真；
          · 不是安全/操作类，且普通 AI 路径不会接管；
          · 至少有两个候选意图；
          · 未命中（intent=None），或已命中的分数/领先幅度不够高。
        """
        enabled, _timeout, _min_conf = self._disambiguation_settings()
        if not enabled or self.ai is None:
            return None
        if not getattr(self.ai, "available", False):
            return None
        if kind in (QueryKind.SAFETY, QueryKind.OPERATION):
            return None
        # 改车指令必须留在本地确定性匹配里，不能被 AI 仲裁抢走。
        try:
            if self.peek_car_command(text) is not None:
                return None
        except Exception:  # noqa: BLE001
            pass
        if self._ai_should_engage(kind, text, tr.get("intent"), False):
            return None
        if tr.get("layer") == "l1_keyword":
            return None
        hit = tr.get("_hit")
        if hit is None:
            return None
        candidates = self._intent_candidates(hit)
        if len(candidates) < 2:
            return None
        score = float(getattr(hit, "score", 0.0) or 0.0)
        margin = float(getattr(hit, "margin", 0.0) or 0.0)
        if tr.get("intent") is not None and score >= 0.90 and margin >= 0.10:
            return None
        return {
            "candidates": candidates,
            "score": round(score, 3),
            "margin": round(margin, 3),
            "phrase": str(getattr(hit, "phrase", "") or ""),
        }

    def judge_intent(self, text: str, candidates: list[dict]) -> str | None:
        """锁外调用 AI 只做意图选择；返回候选内的 intent，失败/越界返回 None。"""
        enabled, timeout, min_conf = self._disambiguation_settings()
        if not enabled or self.ai is None or not candidates:
            return None
        if not getattr(self.ai, "available", False):
            return None
        try:
            judge = getattr(self.ai, "judge_intent", None)
            if callable(judge):
                result = judge(text, candidates, timeout_s=timeout)
            else:
                client = getattr(self.ai, "client", None)
                result = client.judge_intent(text, candidates,
                                             timeout_s=timeout)
        except Exception as exc:  # noqa: BLE001 - 仲裁失败不拖垮主流程
            try:
                self._log(self.miss_log, {"q": text, "ai_judge_error": str(exc)[:200]})
            except Exception:  # noqa: BLE001
                pass
            return None
        if not isinstance(result, dict):
            return None
        intent = str(result.get("intent") or "")
        valid = {str(c.get("intent")) for c in candidates if isinstance(c, dict)}
        try:
            confidence = float(result.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if intent not in valid or confidence < min_conf:
            return None
        return intent

    @exclusive
    def ask(self, text: str, *, source: str = "keyboard",
            extra: dict[str, Any] | None = None,
            forced_intent: str | None = None,
            allow_disambiguate: bool = True,
            force_refuse: bool = False) -> dict:
        """规则层问答。**不含模型调用**——那是 ask_ai()，它不持锁。

        为什么必须拆开：`@exclusive` 会持 `_operation_lock` 且把 `busy` 置真，
        而 `busy` 会让 PTT 监听丢弃按键。若模型调用留在这里，等模型的
        1~2 秒里麦克风全程是聋的，「新问题取消旧问题」根本触发不了。

        v1.5.5：`forced_intent` 是 Service 在锁外拿到 AI 仲裁结果后回填的
        意图；`allow_disambiguate=False` 用于仲裁失败后走普通拒绝，避免
        再次返回同一个仲裁请求。
        """
        p = lambda s: print(s, file=self.out)  # noqa: E731
        extra = dict(extra or {})
        # 入口先记「听到什么」：后面无论走规则还是 AI，Overlay 第一行都要有。
        self.set_heard(text)
        tr = self._resolve(text, strict=self._strict_for(source))
        if forced_intent:
            tr = dict(tr)
            tr["intent"] = forced_intent
            tr["layer"] = "ai_disambiguate"
            tr["why"] = f"AI 意图仲裁 → {forced_intent}"
        snap = self.snapshot()
        rec: dict = {"q": text, "source": source, **extra}
        kind = classify(text, tr["intent"])

        if force_refuse:
            rec.update({"ok": False, "intent": tr.get("intent"),
                        "kind": kind.value,
                        "reason": "意图接近，但没能判断成可回答的问题"})
            if not self.global_stop_local:
                self._say_refusal(kind)
            self._log(self.miss_log, rec)
            return rec

        # v1.5.5：低置信度/候选接近时先不播，锁外让 Service 问 AI 选意图。
        # 必须是「不会走普通 AI 分析」的情况，否则全量 AI 档已经在等模型回答，
        # 再仲裁一次只会多花一次请求。
        if allow_disambiguate and not forced_intent:
            request = self._disambiguation_request(tr, kind, text)
            if request is not None:
                rec.update({"ok": False, "intent": tr.get("intent"),
                            "kind": kind.value, "ai_disambiguate": request,
                            "reason": "意图接近，等待 AI 判定"})
                self._live(state="thinking", answer="", error="")
                self._log(self.miss_log, rec)
                return rec

        # 会走 AI 的问题立刻进「思考中」：规则层这一段（尤其改车指令）
        # 可能还要几十毫秒，但用户松键后马上的视觉反馈应当是思考中。
        if self._ai_should_engage(kind, text, tr["intent"], False):
            self._live(state="thinking", answer="", error="")

        # 分析问题即使命中某个事实意图，也不能只播一个油量数字就结束。
        # 上层会立即接续 ask_ai；此处不先播一遍「没听清/答不了」。
        if kind is QueryKind.ANALYSIS and self._ai_should_engage(kind, text, tr['intent'], False):
            rec.update({'ok': False, 'intent': tr['intent'], 'layer': tr['layer'],
                        'kind': kind.value, 'ai_eligible': True,
                        'reason': '需要结合遥测进行分析'})
            self._log(self.miss_log, rec)
            return rec

        # 改车指令走确定性匹配，绝不经模型。认不出来才落到下面的拒绝流程。
        #
        # 为什么连 UNKNOWN 也试：分类是按词表做子串匹配的，而「换左前胎」
        # 这种「换 + 角位 + 胎」的结构词表覆盖不到，会落进 UNKNOWN。
        # 真正的把关在 command_match.match()——它认不出来就返回 None，
        # 这里什么都不会发生。
        if kind in (QueryKind.OPERATION, QueryKind.UNKNOWN):
            handled = self._try_car_command(text, rec)
            if handled is not None:
                return handled

        if tr["intent"] is None:
            p(f"  意图：拒绝（{tr['_hit'].reason}）")
            rec.update({"ok": False, "intent": None, "layer": "refuse",
                        "kind": kind.value,
                        "ranked": [list(x) for x in tr["_hit"].ranked[:3]]})
            # 交给引擎上层决定是否走 AI（见 ask_ai）。
            rec["ai_eligible"] = self._ai_should_engage(kind, text, None, False)
            # v1.5.2 双响应修复：这句话要转给 AI，就一个字都不许先播——
            # 否则用户先听到「没听清」，一两秒后再听到 AI 的回答。
            if not rec["ai_eligible"]:
                self._say_refusal(kind)
            self._log(self.miss_log, rec)
            return rec

        rec.update({"intent": tr["intent"], "layer": tr["layer"],
                    "kind": kind.value})
        dec = router.route(tr["intent"], snap, text,
                           segments=self.segments_model())
        p(f"  意图 {tr['intent']}  ← {tr['why']}  ({tr['intent_ms']:.1f} ms)")
        if not dec.ok:
            p(f"  ✗ 答不了：{dec.reason}")
            rec.update({"ok": False, "reason": dec.reason})
            rec["ai_eligible"] = self._ai_should_engage(
                kind, text, tr["intent"], False)
            # 同上：AI eligible 的路径先出声就是双响应。
            if not rec["ai_eligible"]:
                # 全局交给 AI 时静音，只把文案写进 Overlay（安全/操作类
                # 仍走本地，用户得看得见答案）。
                if self.global_stop_local:
                    self._show_text_only(dec.refusal_msg or REFUSE_CANT)
                else:
                    self._say(dec.refusal_msg or REFUSE_CANT)
            self._log(self.miss_log, rec)
            return rec
        p(f"  依据：{dec.reason}")
        # all 档全量接管：规则层答得上也不播——答案只当 AI 失败时的兜底
        # 转交给上层 Service._maybe_ai；否则「全部交给 AI」听到的仍是旧行为。
        if self._ai_all_mode and kind not in (QueryKind.SAFETY,
                                              QueryKind.OPERATION):
            rec.update({"ok": False, "ai_eligible": True,
                        "fallback_plan": list(dec.plan),
                        "fallback_text": dec.reason,
                        "reason": "全量 AI 档：规则答案留作兜底"})
            p("  ↪ 全量 AI 档：规则答案不播，转给模型")
            self._live(state="thinking", answer="", error="")
            self._log(self.miss_log, rec)
            return rec
        return self._play_plan(text, snap, dec, rec, source, extra)


    # ------------------------------------------------------------------ 改车
    def car_control_status(self) -> dict:
        if self.car is None:
            return {"enabled": False, "available": False, "verify": False,
                    "error": "未初始化"}
        return self.car.status()

    def _litres_to_fill(self, rec: dict) -> float | None:
        """算出「加满」还要加多少升。读不到就返回 None，不去猜。"""
        snap = self.snapshot()
        level = getattr(snap, "fuel_level", None)
        pct = getattr(snap, "fuel_pct", None)
        if not isinstance(level, (int, float)) or not isinstance(pct, (int, float)):
            return None
        if pct <= 0.001 or level < 0:
            return None
        capacity = level / pct              # 油箱总容量
        need = capacity - level
        rec["cc_fill"] = {"level": round(level, 2), "pct": round(pct, 3),
                          "capacity": round(capacity, 2),
                          "need": round(need, 2)}
        if need <= car_control.FUEL_MIN_L:
            return None                      # 已经满了，没什么可加
        return command_match.clamp_litres(
            need, car_control.FUEL_MIN_L, car_control.FUEL_MAX_L)

    def peek_car_command(self, text: str) -> dict | None:
        """只解析不执行：这句话会不会被当成改车指令？

        给调试台的「预览」用。原先预览只跑意图识别，看不到改车会在
        路由之前截获，于是预览显示「查油量」、实际执行却是「加油」。
        """
        if not (text or "").strip():
            return None
        try:
            return command_match.match(text)
        except Exception:      # noqa: BLE001
            return None

    def _try_car_command(self, text: str, rec: dict) -> dict | None:
        """尝试把一句话当改车指令执行。返回记录，或 None（不是改车指令）。

        这条路径**完全不经过模型**：能做什么靠确定性词表，做不了就如实说。
        """
        p = lambda s: print(s, file=self.out)  # noqa: E731
        action = command_match.match(text)
        if action is None:
            return None            # 认不出来，交给原来的拒绝流程

        rec.update({"cc_action": dict(action), "kind": "operation",
                    "intent": None})

        if action["kind"] == "unsupported":
            reason = action.get("reason") or "这件事软件做不到"
            p(f"  ✗ 改车不支持：{reason}")
            rec.update({"ok": False, "reason": reason})
            self._say_with_fallback(action.get("say") or NO_CAR_CONTROL,
                                    force=True)
            self._log(self.miss_log, rec)
            return rec

        if self.car is None or not self.car.enabled:
            p("  ✗ 改车未启用")
            rec.update({"ok": False, "reason": "改车功能未启用"})
            self._say_with_fallback(NO_CAR_CONTROL, force=True)
            self._log(self.miss_log, rec)
            return rec

        if not self.car.available:
            p("  ✗ 改车不可用（未连接游戏 / 未装 pyirsdk）")
            rec.update({"ok": False, "reason": "未连接游戏，改车指令没有发出"})
            self._say_with_fallback(NO_CAR_CONTROL, force=True)
            self._log(self.miss_log, rec)
            return rec

        if action["kind"] == "fuel":
            if action.get("full"):
                # 「加满」要现算：容量 = 当前油量 / 当前油量百分比。
                # 算不出来就如实拒绝，绝不退化成「沿用现有量」。
                litres = self._litres_to_fill(rec)
                if litres is None:
                    p("  ✗ 加满：读不到油箱容量")
                    rec.update({"ok": False,
                                "reason": "读不到油箱容量，加满请在黑框里设"})
                    self._say_with_fallback(NO_CAR_CONTROL, force=True)
                    self._log(self.miss_log, rec)
                    return rec
                action["litres"] = litres
            rec["cc_action"] = dict(action)

        result = self.car.apply(action)
        rec.update({"cc_sent": result.get("sent"),
                    "cc_error": result.get("error")})
        if not result.get("ok"):
            p(f"  ✗ 改车发送失败：{result.get('error')}")
            rec.update({"ok": False, "reason": result.get("error")})
            self._say_with_fallback(CC_FAIL, force=True)
            self._log(self.miss_log, rec)
            return rec

        verified = None
        if self.car.verify:
            verified = self.car.verify_applied(
                action, lambda: self.snapshot().pit_sv_flags,
                read_fuel=lambda: self.snapshot().pit_sv_fuel)
            rec["cc_verified"] = verified
            p(f"  改车回读：{verified}")

        # 启用确认后，取消和设置都必须确实读到目标位；未知不算成功。
        applied = verified is None or verified.get("verified") is True
        if not applied:
            rec.update({"ok": False, "reason": verified.get("reason")})
            self._say_with_fallback(CC_FAIL, force=True)
            self._log(self.miss_log, rec)
            return rec

        rec.update({"ok": True, "kind": "operation",
                    "reason": "已执行改车指令",
                    "cc_applied": applied})
        msg_id = CC_CONFIRM.get(action["kind"], CC_FAIL)
        p(f"  ✓ 改车：{action}")
        self._say_with_fallback(msg_id, force=True)
        self._log(self.qa_log, rec)
        return rec

    @staticmethod
    def _refusal_for(kind) -> str:
        """按分类选拒绝话术。三类的原因不同，说给用户听的话也必须不同。

        OPERATION 尤其不能复用「没听清」——软件听得很清楚，是做不到。
        """
        if kind is QueryKind.OPERATION:
            return NO_CAR_CONTROL
        if kind is QueryKind.SAFETY:
            return REFUSE_CANT
        return REFUSE_HEARD

    def _say_with_fallback(self, preferred: str, *, force: bool = False) -> None:
        """播一句话术，素材缺失时逐级降级。

        降级链存在的原因：新加的素材（AST_NO_CAR_CONTROL / AST_CC_*）在用户
        重建语音包之前不在包里。直接播会走 `_say()` 的静默跳过——那比播一句
        措辞不完美的话更糟：用户按完 PTT 什么都没听到，会以为软件坏了。

        `force=True` 给改车反馈用：全局交给 AI 时「换胎成功」这类操作确认
        仍然要出声——它不是问答，是动作结果。
        """
        if not force and self.global_stop_local:
            # 全局交给 AI：本地问答话术一律不出声，但「只显示文字」不能变成
            # 「什么都不显示」——把这句话的文案写进 Overlay（安全/操作类
            # 仍走本地，用户得看得见答案）。
            self._show_text_only(preferred)
            return
        for msg_id in (preferred, REFUSE_CANT, REFUSE_HEARD):
            if self.pack.has(msg_id):
                self._say(msg_id)
                return
        # 三个都没有：至少别让用户面对完全的沉默
        print("  ⚠ 话术素材全部缺失，本次无声", file=self.out)

    def _show_text_only(self, preferred: str) -> None:
        """按 `_say_with_fallback` 的降级链取文案，写进 live 但不播。

        只用于「全局交给 AI」把本地语音停掉的时候：话术本身仍是有意义的
        用户反馈（比如「软件不碰车」），不该因为不出声就整个消失。
        """
        for msg_id in (preferred, REFUSE_CANT, REFUSE_HEARD):
            texts = self.pack.texts(msg_id) if self.pack.has(msg_id) else []
            if texts:
                self._live(state="answer", answer=texts[0], error="")
                return

    def _say_refusal(self, kind) -> None:
        """按分类播拒绝话术。"""
        self._say_with_fallback(self._refusal_for(kind))

    def _strict_for(self, source: str) -> bool:
        """语音路径默认严格：只认短语库。打字场景可以走 L1 兜底。"""
        return (source == "voice"
                and self.cfg["intent"].get("voice_strict", True))

    def _play_plan(self, text: str, snap, dec, rec: dict, source: str,
                   extra: dict) -> dict:
        """把路由给出的 msgId 序列播出来。规则层的老路径，记录/日志保持原样。"""
        p = lambda s: print(s, file=self.out)  # noqa: E731
        if not self.play_decision(dec, rec):
            p(f"  ✗ 规则答案播放失败：{rec.get('reason')}")
            self._log(self.miss_log, rec)
            return rec
        self._log(self.qa_log, rec)
        return rec

    def play_decision(self, dec, rec: dict) -> bool:
        """播一个路由决策（``plan`` 的薄包装）。

        抽出来是为了让「规则答案」与「AI 失败回退」共用同一条播放实现：
        两处各写一遍，迟早会在拼装检查或 live 更新上分叉。
        """
        return self.play_plan(getattr(dec, "plan", None) or [], rec)

    def play_plan(self, plan, rec: dict) -> bool:
        """按 msgId 序列播报，返回是否播成功；失败原因写进 ``rec['reason']``。

        调用方决定记哪本账（规则路径记 miss、AI 回退记 qa），这里只负责
        拼装、抽变体、播报和 live 更新。

        全局交给 AI（`ai.mode=all` + `global_stop_local`）时只做「拼装 + 写
        live」，不调播放器：契约要求规则答案与 AI 失败回退都不出声，
        但文字仍要出现在 Overlay 与历史里。此时 rec 里记 `silent=True`，
        便于复盘区分「没播」与「播失败」。
        """
        p = lambda s: print(s, file=self.out)  # noqa: E731
        silent = self.global_stop_local
        if not plan:
            # 空计划不是「播了一句空话」，是调用方逻辑出错；明确判失败。
            rec.update({"ok": False, "reason": "没有可播的片段"})
            return False
        chosen: list[tuple[str, object]] = []
        for mid in plan:
            if not self.pack.has(mid):
                p(f"  ✗ 拼装失败：语音包里没有 {mid}")
                rec.update({"ok": False, "reason": f"拼装缺件 {mid}"})
                return False
            try:
                chosen.append((mid, self._pick_existing(mid)))
            except FileNotFoundError as exc:
                p(f"  ✗ 拼装失败：{exc}")
                rec.update({"ok": False, "reason": f"拼装缺件 {mid}"})
                return False
        try:
            self.picker.save()
        except OSError as exc:
            p(f"  ⚠ 冷却记录保存失败：{exc}")

        assembled = "".join(v.text for _, v in chosen)  # type: ignore[attr-defined]
        rec.update({"plan": list(plan), "text": assembled,
                    "segments": len(chosen), "ok": True})
        # 播报前先写 live：与 AI 路径同一时序约定（答案先出现，声音随其后）。
        self._live(state="answer", answer=assembled, error="")
        if silent:
            rec["silent"] = True
            p("  🔇 全局交给 AI：本地答案只显示文字，不播语音")
            return True
        try:
            played = 0.0
            first_ms: float | None = None

            def on_start():
                nonlocal first_ms
                first_ms = 0.0

            played = self.player.play_many(
                [self.pack.path_of(v) for _, v in chosen], self.gap_ms, on_start)
            rec["first_audio_ms"] = first_ms
            rec["play_s"] = round(played, 3)
        except Exception as exc:  # noqa: BLE001
            p(f"  ✗ 播放失败：{exc}")
            rec.update({"ok": False, "reason": f"播放失败：{exc}"})
        return bool(rec.get("ok"))

    def ask_ai(self, text: str, kind: QueryKind, intent: str | None,
               rule_ok: bool, *, source: str = "voice",
               extra: dict[str, Any] | None = None) -> dict:
        """走 AI 路径。**不持 `_operation_lock`**，因此等待模型期间麦克风不聋。

        `busy` 在等待前置假：用户按 PTT 会被监听接住，新问题由
        LlmClient 的单飞闸门排队，不会两个请求同时在路上。
        """
        p = lambda s: print(s, file=self.out)  # noqa: E731
        rec: dict = {"q": text, "source": source, **(extra or {}),
                     "intent": intent, "kind": kind.value}
        if self.ai is None:
            rec.update({"ok": False, "reason": "AI 未启用"})
            return rec

        # 空文本不进模型（与 _ai_should_engage 同一条底线，这里再兜一层，
        # 防止调用方绕过矩阵直接调 ask_ai）。
        if not (text or "").strip():
            p("  ✗ 识别文本为空，不请求模型")
            rec.update({"ok": False, "reason": "没听到内容，请按住 PTT 重说一次"})
            self._say_with_fallback(REFUSE_HEARD)
            self._live(state="error", answer="",
                       error="没听到内容，请按住 PTT 重说一次")
            self._log(self.miss_log, rec)
            return rec

        # Overlay：等模型的全程都是「思考中」，等待语音播放期间也一样。
        self._live(state="thinking", answer="", error="")
        # 先播等待语音再请求：等待语音不计入 AI 总预算（预算从 handle 内部计时）。
        self._play_ack()
        self.busy = False          # 让 PTT 能进来（关键：不占用麦克风）
        try:
            snap = self.snapshot()
            laps = list(getattr(self.derive, "lap_records", []) or [])
            result = self.ai.handle(text, snap, kind, laps,
                                    rule_ok=rule_ok, intent=intent)
        except Exception as exc:   # noqa: BLE001
            p(f"  ✗ AI 处理异常：{exc}")
            rec.update({"ok": False, "reason": f"AI 处理异常：{exc}"})
            self._live(state="error", answer="",
                       error=f"AI 处理异常：{exc}")
            self._log(self.miss_log, rec)
            return rec

        import uuid
        rec.update({"ai_source": result.source, "ai_text": result.text,
                    "ai_checks": result.checks, "ok": result.ok,
                    "ai_latency_ms": result.latency_ms,
                    "ai_dynamic": bool(result.dynamic),
                    "ai_template": result.template,
                    "ai_evidence": result.evidence,
                    "review_id": uuid.uuid4().hex[:12]})
        if not result.ok:
            # 规则答得上却被转交给 AI 的那条路径：AI 失败不能静默，
            # 回退播规则答案（fallback_plan 由 engine.ask 在 all 档写入）。
            fallback = rec.get("fallback_plan")
            if fallback:
                p(f"  ↪ AI 未答上来（{result.reason}），回退规则答案")
                if self.play_plan(fallback, rec):
                    rec.update({"ok": True, "ai_fallback": True,
                                "ai_reason": result.reason,
                                "reason": "AI 失败，已回退规则答案"})
                    self._log(self.qa_log, rec)
                    return rec
            rec["reason"] = result.reason
            self._live(state="error", answer="", error=result.reason)
            p(f"  ✗ AI 未给出可用回答：{result.reason}")
            # 素材可能还没合成（Task 17）。有就播，没有就只留文字。
            # 全局交给 AI 时这条提示也不出声：用户要的是「只走 AI」，
            # 失败时再甩一句本地话术就回到了他要摆脱的双响应。
            if (not self.global_stop_local and result.checks.get("tts") != "ok"
                    and self.pack.has(AI_SEE_SCREEN)):
                self._say(AI_SEE_SCREEN)
            self._log(self.miss_log, rec)
            return rec
        # 真实链路上流水线已在 TTS 前回调 _on_ai_answer；这里再写一次是幂等的，
        # 也让「直接替换 handle 返回文字」的调用方拿到一致的 live 状态。
        self._live(state="answer", answer=result.text or "", error="")
        p(f"  ✎ AI（{result.source}）{result.latency_ms}ms：{result.text}")
        if (not self.global_stop_local and result.checks.get("tts") != "ok"
                and self.pack.has(AI_SEE_SCREEN)):
            self._say(AI_SEE_SCREEN)
        self._log(self.qa_log, rec)
        return rec

    def _play_ack(self) -> str | None:
        """真正请求模型前同步播一句等待语音，返回播出的 msgId（没播为 None）。

        等待语音是装饰层：未启用、素材缺失、播放失败都静默跳过，
        绝不能让主流程因为「没播出一句等待」而失败。
        """
        if self.ai is None or not self.ai.available:
            return None
        ack_cfg = (self.cfg.get("ai") or {}).get("ack") or {}
        if not ack_cfg.get("enabled", True):
            return None
        # 只在语音包真有的素材里抽：T3 可能还没重建包，抽到缺失的等于没播。
        mid = self.ack_picker.pick(self.pack.has)
        if mid is None:
            return None
        try:
            v = self._pick_existing(mid)
        except Exception as exc:      # noqa: BLE001
            print(f"  ⚠ 等待语音素材不可用：{exc}", file=self.out)
            return None
        print(f"  ⏳ 等待语音 {mid}「{v.text}」", file=self.out)
        self._live(state="thinking")
        try:
            self.player.play(self.pack.path_of(v))
        except Exception as exc:      # noqa: BLE001
            print(f"  ⚠ 等待语音播放失败：{exc}", file=self.out)
            return None
        return mid

    # ------------------------------------------------------------------
    def pack_info(self) -> dict:
        return {"version": self.pack.version, "dir": str(self.pack.root),
                "msgIds": self.stats["msgIds"], "clips": self.stats["clips"],
                "rate": self.pack.sample_rate(), "extra_dir": self.extra_dir}

    def config_view(self) -> dict:
        """给界面看的配置子集（不含任何密钥）。"""
        c = self.cfg
        return {
            "intent": {
                "l1_keyword": bool(c["intent"]["l1_keyword"]["enabled"]),
                "voice_strict": bool(c["intent"].get("voice_strict", True)),
                "library_size": len(self.matcher.phrases),
                "accept": self.matcher.accept,
                "margin": self.matcher.margin,
            },
            "asr": dict(c.get("asr", {})),
            "ptt": {k: v for k, v in c.get("ptt", {}).items()},
            "playback": {
                "enabled": bool(c["playback"]["enabled"]),
                "device": c["playback"].get("device"),
                "volume": c["playback"].get("volume", 1.0),
                "ducking": dict(c["playback"].get("ducking", {})),
            },
            "telemetry": {"provider": c["telemetry"]["provider"]},
            "voice_pack": {"cooldown": c["voice_pack"]["cooldown"],
                           "gap_ms": self.gap_ms},
            "ai": self.ai_config_view(),
            # 实测遗漏：界面读 st.config.car_control 却拿不到，于是永远显示
            # 「关/未启用」，用户再保存一次就把正确的 true 覆盖成 false。
            # 这里回的是**运行态**（self.car），配置与实际不一致时以实际为准。
            "car_control": self.car_config_view(),
            # Overlay 配置由 src 层负责默认值/校验，app 侧只消费这份值。
            "overlay": dict(c.get("overlay") or {}),
        }

    def car_config_view(self) -> dict:
        """改车配置的对外视图。不使用密钥，可安全回显。"""
        cf = (self.cfg or {}).get("car_control") or {}
        live = self.car
        return {
            "enabled": bool(live.enabled) if live is not None
                       else bool(cf.get("enabled", False)),
            "verify": bool(cf.get("verify", True)),
            "available": bool(live.available) if live is not None else False,
            "configured": live is not None,
            "error": (live.status().get("error") if live is not None else None),
        }

    def ai_config_view(self) -> dict:
        """AI 配置的对外视图：**只有布尔，没有密钥明文**。

        这是密钥隔离的唯一出口——state() 与 export_logs() 都走这里，
        不要在别处直接读 cfg["ai"]，否则以后加字段很容易漏掉脱敏。
        """
        from .llm_engine import thinking_supported

        ai = (self.cfg or {}).get("ai") or {}
        tts = ai.get("tts") or {}
        return {
            "enabled": bool(ai.get("enabled")),
            "mode": ai.get("mode"),
            # 全局停止本地播报（v1.5.2）：界面开关回填用。
            "global_stop_local": bool(ai.get("global_stop_local", True)),
            # 思考模式：off/low + 当前服务商是否保证支持（界面提示用）。
            "thinking_mode": ai.get("thinking_mode") or "off",
            "thinking_supported": thinking_supported(
                ai.get("base_url") or "", ai.get("model") or ""),
            "provider": ai.get("provider"),
            "base_url": ai.get("base_url") or "",
            "model": ai.get("model") or "",
            "api_key_set": bool(ai.get("api_key")),
            "timeout_s": ai.get("timeout_s"),
            "max_tokens": ai.get("max_tokens"),
            "max_answer_chars": ai.get("max_answer_chars"),
            "whitelist_intents": list(ai.get("whitelist_intents") or []),
            "analysis_key_bound": bool(ai.get("analysis_key")),
            # 等待语音开关（Overlay/设置页读取；默认开）。
            "ack_enabled": bool((ai.get("ack") or {}).get("enabled", True)),
            # v1.5.5 AI 意图仲裁：只读展示，不暴露 key。
            "disambiguate_enabled": self._disambiguation_settings()[0],
            "disambiguate_timeout_s": self._disambiguation_settings()[1],
            "disambiguate_min_confidence": self._disambiguation_settings()[2],
            "tts": {
                "adapter": tts.get("adapter") or "off",
                "base_url": tts.get("base_url") or "",
                "model": tts.get("model") or "",
                "voice": tts.get("voice") or "",
                "speaker": tts.get("speaker") or "",
                "api_key_set": bool(tts.get("api_key")),
            },
        }

    def apply_live(self, patch: dict) -> None:
        """把界面上改的配置立刻生效（能改的先改，不能的等重启）。"""
        if "volume" in patch:
            self.player.volume = float(patch["volume"])
        if "output_device" in patch:
            from .listener import resolve_device

            self.player.device = resolve_device(patch["output_device"])
        if "enabled" in patch:
            self.player.enabled = bool(patch["enabled"])
        if "ducking" in patch and self.ducker is not None:
            on = bool(patch["ducking"])
            if not on:
                self.ducker.restore()
            self.ducker.enabled = on

    # ------------------------------------------------------------------
    def _resolve(self, text: str, strict: bool) -> dict:
        """意图解析的完整轨迹（调试台与正常问答共用）。"""
        t0 = time.perf_counter()
        hit = self.matcher.match(text)
        layer = "phrase"
        intent = hit.intent
        why = hit.reason
        l1 = None
        if intent is None and not strict and self.cfg["intent"]["l1_keyword"]["enabled"]:
            m = intents.match(text)
            forced = intents.disambiguate(text)
            if forced:
                m = intents.Match(intent=forced, confidence=0.9, why=f"消歧规则 → {forced}")
            if m:
                layer, intent, why = "l1_keyword", m.intent, m.why
                l1 = {"hit": True, "intent": m.intent, "why": m.why}
            else:
                l1 = {"hit": False}
        return {
            "text": text,
            "_hit": hit,
            "strict": strict,
            "phrase": {
                "intent": hit.intent,
                "score": round(hit.score, 3),
                "phrase": hit.phrase,
                "margin": round(hit.margin, 3),
                "reason": hit.reason,
                "ranked": [[i, round(s, 3), p] for i, s, p in hit.ranked[:4]],
                "accept": self.matcher.accept,
                "min_margin": self.matcher.margin,
            },
            "l1": l1,
            "intent": intent,
            "layer": layer if intent else None,
            "why": why,
            "intent_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def explain(self, text: str, *, strict: bool = True) -> dict:
        """只解析不播放：返回轨迹（含路由与将要播的片段）。"""
        tr = self._resolve(text, strict=strict)
        snap = self.snapshot()
        dec = None
        if tr["intent"]:
            dec = router.route(tr["intent"], snap, text,
                               segments=self.segments_model())
        plan = []
        if dec and dec.ok:
            for mid in dec.plan:
                if self.pack.has(mid):
                    v = self.picker.preview(mid)
                    plan.append({"msgId": mid, "text": v.text,
                                 "file": pathlib.Path(self.pack.path_of(v)).name,
                                 "variants": len(self.pack.variants[mid])})
                else:
                    plan.append({"msgId": mid, "text": None, "file": None,
                                 "variants": 0, "missing": True})
        tr["decision"] = {"ok": bool(dec and dec.ok),
                          "reason": dec.reason if dec else "未进入路由",
                          "plan": [m for m in (dec.plan if dec else [])]}
        unavailable=[p['msgId'] for p in plan if p.get('missing') or not any(self.pack.path_of(v).is_file() for v in self.pack.variants.get(p['msgId'],[]))]
        if unavailable:
            tr['decision']['ok']=False
            tr['decision']['reason']='对应音频素材不完整：'+', '.join(unavailable)
        tr["plan"] = plan
        tr["snapshot"] = self.snapshot_fields(snap)
        tr.pop("_hit", None)
        return tr

    @staticmethod
    def snapshot_fields(s) -> dict:
        """给界面用的遥测快照（扁平化）。"""
        return {
            "connected": bool(s.connected), "source": s.source,
            "air_temp": s.air_temp, "track_temp": s.track_temp,
            "track_wetness": s.track_wetness, "skies": s.skies,
            "precip": s.precip, "declared_wet": s.declared_wet,
            "fuel_level": s.fuel_level, "fuel_per_lap": s.fuel_per_lap,
            "laps_of_fuel": s.laps_of_fuel,
            "lap": s.lap, "laps_remain": s.laps_remain,
            "lap_last": s.lap_last, "lap_best": s.lap_best,
            "position": s.position, "field_size": s.field_size,
            "incident_count": s.incident_count, "flags": s.flags,
            "gap_ahead_s": s.gap_ahead_s, "gap_behind_s": s.gap_behind_s,
            "gap_behind_trend": s.gap_behind_trend,
            'class_position':s.class_position,'speed_kph':s.speed_kph,'lap_current':s.lap_current,
            'relative_humidity':s.relative_humidity,'water_temp':s.water_temp,'oil_temp':s.oil_temp,
            'rpm':s.rpm,'gear':s.gear,'voltage':s.voltage,
            'pits_open':s.pits_open,'gap_estimated':s.gap_estimated,'tyre_history':s.tyre_history,
            'pit_sv_flags':s.pit_sv_flags,'pit_sv_fuel':s.pit_sv_fuel,
            'fast_repairs_available':s.fast_repairs_available,
            'on_pit_road':s.on_pit_road,'in_pit_stall':s.in_pit_stall,
            "time_remain_s": s.time_remain_s,
        }

    # ------------------------------------------------------------------
    def info_lines(self) -> list[str]:
        vp = self.cfg["voice_pack"]
        st = self.stats
        lines = [f"语音包 v{self.pack.version}  {self.pack.root}"]
        if self.extra_dir:
            lines.append(f"   + 助手素材  {self.extra_dir}")
        lines.append(f"  msgId {st['msgIds']} 个 / 音频 {st['clips']} 条 / 平均 {st['avg_variants']} 变体")
        lines.append(f"短语库 {len(self.matcher.phrases)} 条；L1 关键词"
                     f"{'开' if self.cfg['intent']['l1_keyword']['enabled'] else '关'}")
        return lines

    def segments_model(self):
        """分段模型（T1 的 `DerivedTracker.segments`）；没接入时返回 None。

        赛段（v1.5.4）的本地回答与 AI 上下文都从这里取同一份数据源：
        本地经 `router.route(..., segments=...)`，AI 靠 `snapshot()` 挂引用。
        """
        return getattr(getattr(self, "derive", None), "segments", None)

    def snapshot(self):
        """读当前遥测快照，并附上分段模型的只读引用。

        为什么挂在快照上：AI 上下文的 `race_context.build` 只收到 Snapshot，
        不挂上去就得改 AI 流水线的调用签名（本次刻意没动那条链路）。
        挂的是对象引用而非数据拷贝；dataclass 的字段与相等性都不受影响。
        """
        snap = self.sampler.read()
        segments = self.segments_model()
        if segments is not None:
            try:
                snap.segments = segments
            except Exception:      # noqa: BLE001 - 展示层增强，不挡主流程
                pass
        return snap

    def close(self):
        self.player.stop()
        self.sampler.close()
        if self.car is not None:
            self.car.close()


    # ------------------------------------------------------------------
    def _pick_existing(self, mid: str):
        """抽一条变体；万一抽到文件缺失的，换一条真能播的。

        正常情况一次就中；这是防「包不完整」的兜底，不让一个问题
        因为随机抽到坏文件就整个答不出来。
        """
        v = self.picker.pick(mid, save=False)
        if self.pack.path_of(v).exists():
            return v
        for cand in self.pack.variants.get(mid, []):
            if self.pack.path_of(cand).exists():
                return cand
        raise FileNotFoundError(f"{mid} 的所有变体文件都不在磁盘上")

    @exclusive
    def _say(self, mid: str) -> None:
        """播一条固定话术（拒绝提示之类），播不了就静默跳过。"""
        if not self.pack.has(mid):
            return
        try:
            v = self._pick_existing(mid)
        except FileNotFoundError:
            print(f"  ⚠ {mid} 没有可播的文件，跳过", file=self.out)
            return
        print(f"  ↩ 「{v.text}」", file=self.out)
        try:
            self.player.play(self.pack.path_of(v))
        except Exception:  # noqa: BLE001
            pass

    @exclusive
    def play_msgid(self, mid: str) -> bool:
        if not self.pack.has(mid):
            return False
        try:
            v = self._pick_existing(mid)
            print(f"  → {mid}  「{v.text}」", file=self.out)
            self.player.play(self.pack.path_of(v))
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {exc}", file=self.out)
            return False
        return True

    def _log(self, path: pathlib.Path, rec: dict) -> None:
        rec = dict(rec)
        rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.history.append(rec)
        if path == self.miss_log:
            self._write_log(self.qa_log, rec)
            if not self.cfg["intent"]["miss_log"].get("enabled", True):
                return
        self._write_log(path, rec)

    @staticmethod
    def _write_log(path, rec):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass
