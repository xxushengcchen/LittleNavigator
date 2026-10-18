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
from .query_class import QueryKind, classify
from . import car_control, command_match

# 拒绝话术（在 assistant-extra 素材包里，同一音色合成）
REFUSE_HEARD = "AST_REFUSE_HEARD"    # 没听清 / 对不上短语库
REFUSE_CANT = "AST_REFUSE_CANT"      # 听懂了，但遥测答不了
AI_SEE_SCREEN = "AST_AI_SEE_SCREEN"  # AI 答了但没配 TTS：请用户看屏幕
NO_CAR_CONTROL = "AST_NO_CAR_CONTROL"  # 改车未启用 / 这件事做不到
CC_CONFIRM = {"tyre": "AST_CC_TYRE", "fuel": "AST_CC_FUEL",
              "tearoff": "AST_CC_WS", "fast_repair": "AST_CC_FR",
              "clear": "AST_CC_CLEAR", "clear_tires": "AST_CC_CLEAR"}
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
            return AiPipeline(cfg, store=store, out=self.out)
        except Exception as exc:   # noqa: BLE001
            print(f"  ⚠ AI 初始化失败：{exc}", file=self.out)
            return None

    @property
    def ai_enabled(self) -> bool:
        return self.ai is not None

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
        from .ai_pipeline import should_engage
        return should_engage(kind, self.ai.mode, rule_ok, intent,
                             self.ai.whitelist)

    @exclusive
    def ask(self, text: str, *, source: str = "keyboard",
            extra: dict[str, Any] | None = None) -> dict:
        """规则层问答。**不含模型调用**——那是 ask_ai()，它不持锁。

        为什么必须拆开：`@exclusive` 会持 `_operation_lock` 且把 `busy` 置真，
        而 `busy` 会让 PTT 监听丢弃按键。若模型调用留在这里，等模型的
        1~2 秒里麦克风全程是聋的，「新问题取消旧问题」根本触发不了。
        """
        p = lambda s: print(s, file=self.out)  # noqa: E731
        extra = dict(extra or {})
        tr = self._resolve(text, strict=self._strict_for(source))
        snap = self.snapshot()
        rec: dict = {"q": text, "source": source, **extra}
        kind = classify(text, tr["intent"])

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
            # 交给引擎上层决定是否走 AI（见 ask_ai）
            rec["ai_eligible"] = self._ai_should_engage(kind, text, None, False)
            self._say_refusal(kind)
            self._log(self.miss_log, rec)
            return rec

        rec.update({"intent": tr["intent"], "layer": tr["layer"],
                    "kind": kind.value})
        dec = router.route(tr["intent"], snap, text)
        p(f"  意图 {tr['intent']}  ← {tr['why']}  ({tr['intent_ms']:.1f} ms)")
        if not dec.ok:
            p(f"  ✗ 答不了：{dec.reason}")
            rec.update({"ok": False, "reason": dec.reason})
            rec["ai_eligible"] = self._ai_should_engage(
                kind, text, tr["intent"], False)
            self._say(dec.refusal_msg or REFUSE_CANT)
            self._log(self.miss_log, rec)
            return rec
        p(f"  依据：{dec.reason}")
        return self._play_plan(text, snap, dec, rec, source, extra)


    # ------------------------------------------------------------------ 改车
    def car_control_status(self) -> dict:
        if self.car is None:
            return {"enabled": False, "available": False, "verify": False,
                    "error": "未初始化"}
        return self.car.status()

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
            self._say_with_fallback(NO_CAR_CONTROL)
            self._log(self.miss_log, rec)
            return rec

        if self.car is None or not self.car.enabled:
            p("  ✗ 改车未启用")
            rec.update({"ok": False, "reason": "改车功能未启用"})
            self._say_with_fallback(NO_CAR_CONTROL)
            self._log(self.miss_log, rec)
            return rec

        if not self.car.available:
            p("  ✗ 改车不可用（未连接游戏 / 未装 pyirsdk）")
            rec.update({"ok": False, "reason": "未连接游戏，改车指令没有发出"})
            self._say_with_fallback(NO_CAR_CONTROL)
            self._log(self.miss_log, rec)
            return rec

        if action["kind"] == "fuel":
            litres = command_match.clamp_litres(
                action.get("litres"), car_control.FUEL_MIN_L,
                car_control.FUEL_MAX_L)
            action["litres"] = litres
            rec["cc_action"] = dict(action)

        result = self.car.apply(action)
        rec.update({"cc_sent": result.get("sent"),
                    "cc_error": result.get("error")})
        if not result.get("ok"):
            p(f"  ✗ 改车发送失败：{result.get('error')}")
            rec.update({"ok": False, "reason": result.get("error")})
            self._say_with_fallback(CC_FAIL)
            self._log(self.miss_log, rec)
            return rec

        verified = None
        if self.car.verify:
            verified = self.car.verify_applied(
                action, lambda: self.snapshot().pit_sv_flags)
            rec["cc_verified"] = verified
            p(f"  改车回读：{verified}")

        # 只有回读确认过才算真的成功；没验证能力的动作（clear 类）按发送成功算。
        applied = verified is None or verified.get("verified") is not False
        if not applied:
            rec.update({"ok": False, "reason": verified.get("reason")})
            self._say_with_fallback(CC_FAIL)
            self._log(self.miss_log, rec)
            return rec

        rec.update({"ok": True, "kind": "operation",
                    "reason": "已执行改车指令",
                    "cc_applied": applied})
        msg_id = CC_CONFIRM.get(action["kind"], CC_FAIL)
        p(f"  ✓ 改车：{action}")
        self._say_with_fallback(msg_id)
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

    def _say_with_fallback(self, preferred: str) -> None:
        """播一句话术，素材缺失时逐级降级。

        降级链存在的原因：新加的素材（AST_NO_CAR_CONTROL / AST_CC_*）在用户
        重建语音包之前不在包里。直接播会走 `_say()` 的静默跳过——那比播一句
        措辞不完美的话更糟：用户按完 PTT 什么都没听到，会以为软件坏了。
        """
        for msg_id in (preferred, REFUSE_CANT, REFUSE_HEARD):
            if self.pack.has(msg_id):
                self._say(msg_id)
                return
        # 三个都没有：至少别让用户面对完全的沉默
        print("  ⚠ 话术素材全部缺失，本次无声", file=self.out)

    def _say_refusal(self, kind) -> None:
        """按分类播拒绝话术。"""
        self._say_with_fallback(self._refusal_for(kind))

    def _strict_for(self, source: str) -> bool:
        """语音路径默认严格：只认短语库。打字场景可以走 L1 兜底。"""
        return (source == "voice"
                and self.cfg["intent"].get("voice_strict", True))

    def _play_plan(self, text: str, snap, dec, rec: dict, source: str,
                   extra: dict) -> dict:
        """把路由给出的 msgId 序列播出来。规则层的老路径，逻辑保持原样。"""
        p = lambda s: print(s, file=self.out)  # noqa: E731
        chosen: list[tuple[str, object]] = []
        for mid in dec.plan:
            if not self.pack.has(mid):
                p(f"  ✗ 拼装失败：语音包里没有 {mid}")
                rec.update({"ok": False, "reason": f"拼装缺件 {mid}"})
                self._log(self.miss_log, rec)
                return rec
            try:
                chosen.append((mid, self._pick_existing(mid)))
            except FileNotFoundError as exc:
                p(f"  ✗ 拼装失败：{exc}")
                rec.update({"ok": False, "reason": f"拼装缺件 {mid}"})
                self._log(self.miss_log, rec)
                return rec
        try:
            self.picker.save()
        except OSError as exc:
            p(f"  ⚠ 冷却记录保存失败：{exc}")

        assembled = "".join(v.text for _, v in chosen)  # type: ignore[attr-defined]
        rec.update({"plan": dec.plan, "text": assembled,
                    "segments": len(chosen), "ok": True})
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
        self._log(self.qa_log, rec)
        return rec

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

        self.busy = False          # 让 PTT 能进来（关键：不占用麦克风）
        try:
            snap = self.snapshot()
            laps = list(getattr(self.derive, "lap_records", []) or [])
            result = self.ai.handle(text, snap, kind, laps,
                                    rule_ok=rule_ok, intent=intent)
        except Exception as exc:   # noqa: BLE001
            p(f"  ✗ AI 处理异常：{exc}")
            rec.update({"ok": False, "reason": f"AI 处理异常：{exc}"})
            self._log(self.miss_log, rec)
            return rec

        import uuid
        rec.update({"ai_source": result.source, "ai_text": result.text,
                    "ai_checks": result.checks, "ok": result.ok,
                    "ai_latency_ms": result.latency_ms,
                    "ai_dynamic": bool(result.dynamic),
                    "ai_template": result.template,
                    "review_id": uuid.uuid4().hex[:12]})
        if not result.ok:
            rec["reason"] = result.reason
            p(f"  ✗ AI 未给出可用回答：{result.reason}")
            # 素材可能还没合成（Task 17）。有就播，没有就只留文字。
            if self.pack.has(AI_SEE_SCREEN):
                self._say(AI_SEE_SCREEN)
            self._log(self.miss_log, rec)
            return rec
        p(f"  ✎ AI（{result.source}）{result.latency_ms}ms：{result.text}")
        if self.pack.has(AI_SEE_SCREEN):
            self._say(AI_SEE_SCREEN)
        self._log(self.qa_log, rec)
        return rec

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
        }

    def ai_config_view(self) -> dict:
        """AI 配置的对外视图：**只有布尔，没有密钥明文**。

        这是密钥隔离的唯一出口——state() 与 export_logs() 都走这里，
        不要在别处直接读 cfg["ai"]，否则以后加字段很容易漏掉脱敏。
        """
        ai = (self.cfg or {}).get("ai") or {}
        tts = ai.get("tts") or {}
        return {
            "enabled": bool(ai.get("enabled")),
            "mode": ai.get("mode"),
            "provider": ai.get("provider"),
            "base_url": ai.get("base_url") or "",
            "model": ai.get("model") or "",
            "api_key_set": bool(ai.get("api_key")),
            "timeout_s": ai.get("timeout_s"),
            "max_tokens": ai.get("max_tokens"),
            "max_answer_chars": ai.get("max_answer_chars"),
            "whitelist_intents": list(ai.get("whitelist_intents") or []),
            "analysis_key_bound": bool(ai.get("analysis_key")),
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
            dec = router.route(tr["intent"], snap, text)
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

    def snapshot(self):
        return self.sampler.read()

    def close(self):
        self.player.stop()
        self.sampler.close()


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
