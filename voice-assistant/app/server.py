"""语音助手本地服务：把 src/engine.py 包成 HTTP API。

前端就是 UI 定稿稿（app/static/index.html），由浏览器打开。

    python -m app.server                # 按 config.yaml 连真游戏
    python -m app.server --sim          # 模拟遥测，没开游戏也能玩
    python -m app.server --no-browser   # 不自动开浏览器

接口一览（全部 JSON，UTF-8）：
    GET  /api/state                 状态快照（连接/PTT/语音包/遥测/最近问答）
    GET  /api/log?limit=50          问答记录 + 未识别清单
    GET  /api/stats                 本场统计
    GET  /api/knowledge             知识库：意图 / 问法分布 / 字段 / 链路 / 可疑样本
    GET  /api/devices               音频设备清单
    POST /api/ask   {text, play}    解析一句（可选真播）
    POST /api/play  {msgId}         试听某条消息
    POST /api/listen {running}      开/关 PTT 监听
    POST /api/ptt/simulate {seconds} 模拟按住 PTT（真录音真识别）
    POST /api/config {patch}        改设定（写 config.local.yaml）
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib
import socketserver
import sys
import threading
import time
import webbrowser
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote

ROOT = pathlib.Path(__file__).resolve().parents[1]        # voice-assistant/
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src import config as cfg_mod           # noqa: E402
from src.engine import Assistant            # noqa: E402
from src.listener import PttListener, check_input_device, resolve_vk  # noqa: E402
from src.query_class import classify        # noqa: E402

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"


def _version_file() -> pathlib.Path:
    """版本号文件：源码运行在项目根；打包后可能在 exe 旁或 _internal 里。"""
    cands = [cfg_mod.runtime_dir() / "VERSION",
             pathlib.Path(__file__).resolve().parents[1] / "VERSION"]
    for c in cands:
        if c.exists():
            return c
    return cands[-1]


VERSION_FILE = _version_file()


def app_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


class RingLog(io.TextIOBase):
    """既打到控制台，又留最近 300 行给界面看。"""

    def __init__(self, keep: int = 300):
        self.lines: collections.deque[str] = collections.deque(maxlen=keep)

    def write(self, s: str) -> int:
        try:
            sys.stdout.write(s)
        except UnicodeEncodeError:
            # 控制台编码不兼容（中文 Windows 的 GBK 控制台）时也别把程序写崩
            enc = sys.stdout.encoding or "utf-8"
            sys.stdout.write(s.encode(enc, "replace").decode(enc, "replace"))
        except Exception:  # noqa: BLE001
            pass
        for line in s.splitlines():
            if line.strip():
                self.lines.append(line.rstrip())
        return len(s)

    def flush(self) -> None:
        sys.stdout.flush()

    def tail(self, n: int = 60) -> list[str]:
        return list(self.lines)[-n:]


class Service:
    """应用状态都挂在这里；HTTP handler 只是薄薄一层。"""

    def __init__(self, cfg_path: str | None = None, sim: bool = False,
                 sim_advance: bool = False):
        self.lock = threading.RLock()
        self.listener_lock = threading.RLock()
        self.api_token = secrets.token_urlsafe(32)
        self.log = RingLog()
        self.cfg_path = cfg_path
        self.cfg = cfg_mod.load(cfg_path)
        self.sim = sim or self.cfg["telemetry"]["provider"] == "sim"
        self.asst = Assistant(self.cfg, force_sim=sim, sim_advance=sim_advance,
                                  out=self.log)
        self.asr = None
        self.listener: PttListener | None = None
        self.listener_err: str | None = None
        self.started_at = time.time()
        self.last_ask: dict | None = None
        self.quit_requested = False
        self.desktop_mode = False        # 原生窗口模式（app.desktop 里置 True）
        self.qa_log = self.asst.qa_log
        self.miss_log = self.asst.miss_log

    # ---------------------------------------------------------------- 语音输入
    def asr_model(self) -> str:
        return self.cfg.get("asr", {}).get("model", "sense-voice")

    def ensure_asr(self):
        if self.asr is not None:
            return self.asr
        from tools.asr_models import load as load_asr

        with self.lock:
            if self.asr is None:
                loaded = load_asr(self.asr_model(),
                                    num_threads=self.cfg.get("asr", {}).get("num_threads", 4),
                                    models_root=cfg_mod.resolve(self.cfg, self.cfg["asr"]["models_dir"]))
                loaded.warmup()
                self.asr = loaded
        return self.asr

    def start_listener(self) -> dict:
        with self.listener_lock:
            return self._start_listener()

    def _start_listener(self) -> dict:
        if self.listener is not None and self.listener.running:
            return {"running": True}
        err = check_input_device(self.cfg.get("ptt", {}).get("input_device"))
        if err:
            self.listener_err = err
            return {"running": False, "error": err}
        try:
            self.ensure_asr()
        except Exception as exc:  # noqa: BLE001
            self.listener_err = f"ASR 载入失败：{exc}"
            return {"running": False, "error": self.listener_err}

        def on_result(text: str, meta: dict) -> None:
            rec = self.asst.ask(text, source="voice", extra=meta)
            self.last_ask = rec
            self._maybe_ai(rec, text, source="voice")

        def on_analysis(text: str, meta: dict) -> None:
            """分析键：跳过规则层，直接走 AI（安全类仍会被挡）。"""
            from src.query_class import classify
            kind = classify(text, None)
            rec = self.asst.ask_ai(text, kind, None, False,
                                   source="analysis", extra=meta)
            self.last_ask = rec

        ai_key = (self.cfg.get("ai", {}) or {}).get("analysis_key") or {}
        analysis_vk = ai_key.get("vk") if isinstance(ai_key, dict) else None
        self.listener = PttListener(self.asr, self.cfg, on_result,
                                    is_busy=lambda: self.asst.busy, out=self.log,
                                    analysis_vk=analysis_vk,
                                    on_analysis_result=on_analysis)
        self.listener.start()
        self.listener_err = None
        return {"running": True}

    def _maybe_ai(self, rec: dict, text: str, *, source: str) -> dict:
        """规则层答不上、且档位允许时，转 AI 路径。

        规则层答得上时 `ai_eligible` 为 False，压根不会进这里——
        「规则优先」这条不靠调用方自觉，靠 ask() 返回的标志。
        """
        if not rec.get("ai_eligible"):
            return rec
        try:
            from src.query_class import QueryKind
            kind = QueryKind(rec.get("kind") or QueryKind.UNKNOWN.value)
            ai_rec = self.asst.ask_ai(text, kind, rec.get("intent"), False,
                                      source=source, extra=rec)
            # 规则层的拒绝记录已经在 miss_log 里，这里只补 AI 结果供界面看
            self.last_ask = ai_rec
            return ai_rec
        except Exception as exc:       # noqa: BLE001
            self.log.write(f"AI 转交失败：{exc}\n")
            return rec

    def stop_listener(self) -> dict:
        with self.listener_lock:
            if self.listener is not None:
                self.asst.player.stop()
                if not self.listener.stop():
                    return {"running": True, "error": "监听正在结束，请稍后重试"}
                self.listener = None
            return {"running": False}

    def close(self):
        self.quit_requested = True
        self.stop_listener()
        self.asst.close()
        from src.wheel import close_input
        close_input()

    def capture_ptt(self,kind,timeout):
        with self.listener_lock:
            running=bool(self.listener and self.listener.running)
            if running and self.stop_listener().get('running'):
                raise ValueError('监听尚未停止，请稍后重试')
            try:
                if kind=='wheel':
                    from src.wheel import get_input
                    return get_input().capture(timeout)
                if kind!='keyboard':raise ValueError('未知输入类型')
                from src.listener import capture_key
                got=capture_key(timeout)
                if not got:raise ValueError('超时未检测到按键')
                return {**got,'binding':None}
            finally:
                if running:self.start_listener()

    def simulate_ptt(self, seconds: float = 2.0) -> dict:
        """真按住真录音：合成按键事件让监听线程跑一遍完整链路。"""
        if self.listener is None:
            started = self.start_listener()
            if not started.get("running"):
                return started
        if not 0.3 <= seconds <= 10:
            raise ValueError("录音测试时长必须为 0.3–10 秒")
        listener = self.listener
        return listener.record_for(seconds) if listener else {"error": "监听已停止"}

    # ---------------------------------------------------------------- 状态
    def ptt_state(self) -> str:
        if self.asst.busy:
            return "speaking"
        if self.listener is None:
            return "off"
        return self.listener.state

    def state(self) -> dict:
        from src.wheel import binding_label
        snap = self.asst.snapshot()
        info = self.asst.pack_info()
        listener_stats = dict(self.listener.stats) if self.listener else None
        return {
            "now": time.strftime("%H:%M:%S"),
            "uptime_s": round(time.time() - self.started_at),
            "sim": self.sim,
            "desktop": self.desktop_mode,
            "app": {"version": app_version(),
                    "author": "xushengchen from Moondrop-Dox Racing"},
            "telemetry": self.asst.snapshot_fields(snap),
            "ptt": {
                "state": self.ptt_state(),
                "running": bool(self.listener and self.listener.running),
                "error": self.listener_err or (self.listener.error if self.listener else None),
                "vk": f"0x{resolve_vk(self.cfg.get('ptt', {}).get('vk', 0xA3)):02X}",
                "binding": self.cfg['ptt'].get('binding'),
                "binding_label": binding_label(self.cfg['ptt'].get('binding'),resolve_vk(self.cfg['ptt']['vk'])),
                "min_hold_ms": self.cfg.get("ptt", {}).get("min_hold_ms", 250),
                "stats": listener_stats,
                "last": (self.listener.last if self.listener else None),
            },
            "asr": {
                "model": self.asr_model(),
                "loaded": self.asr is not None,
                "load_sec": round(getattr(self.asr, "load_sec", 0.0), 2) if self.asr else None,
            },
            "pack": info,
            "car_control": self.asst.car_control_status(),
            "ai": {**self.asst.ai_status(),
                   "last_text": (self.last_ask or {}).get("ai_text"),
                   "last_source": (self.last_ask or {}).get("ai_source"),
                   "analysis_key_bound": bool(
                       (self.cfg.get("ai", {}) or {}).get("analysis_key"))},
            "last": self.last_ask or (self.asst.history[-1] if self.asst.history else None),
            "config": self.asst.config_view(),
        }

    # ---------------------------------------------------------------- 问答
    def ask(self, text: str, play: bool = False) -> dict:
        with self.lock:
            tr = self.asst.explain(text, strict=False)
            if play:
                rec = self.asst.ask(text)
                self.last_ask = rec
                rec = self._maybe_ai(rec, text, source="keyboard")
                tr["playback"] = rec
                # 实测问题：调试台先跑 explain() 只做意图识别，
                # 而 explain() 不知道改车指令会抢在路由之前被 command_match 截获，
                # 于是「加1.5升油」预览显示 query_fuel，实际执行却是改车加油。
                # 预览与执行必须同源——把实际结果里的分类覆盖回去。
                if rec.get("kind") == "operation":
                    tr.update({
                        "intent": rec.get("intent"),
                        "kind": rec.get("kind"),
                        "cc_action": rec.get("cc_action"),
                        "note": "这句话是改车指令，不经过意图路由",
                    })
            else:
                # 不执行时，也按真实顺序判断一次是否改车，
                # 让「试听」看到的就是会发生的事。
                cc = self.asst.peek_car_command(text)
                if cc is not None:
                    tr.update({"intent": None, "kind": "operation",
                               "cc_action": cc,
                               "note": "这句话是改车指令，不经过意图路由"})
                else:
                    # 普通问答也给出与执行一致的分类，避免预览/执行口径不同
                    tr["kind"] = classify(text, tr.get("intent")).value
                self.last_ask = {"q": text, "trace": tr}
            return tr

    def play(self, msg_id: str) -> dict:
        with self.lock:
            ok = self.asst.play_msgid(msg_id)
            return {"ok": ok, "msgId": msg_id}

    # ---------------------------------------------------------------- 日志与统计
    @staticmethod
    def _tail_jsonl(path: pathlib.Path, n: int) -> list[dict]:
        if not path.exists():
            return []
        with path.open(encoding="utf-8", errors="replace") as stream:
            lines = collections.deque(stream, maxlen=max(1, min(n, 1000)))
        out = []
        for line in lines:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    out.append(item)
            except json.JSONDecodeError:
                continue
        return out

    def log_data(self, limit: int = 80) -> dict:
        qa = self._tail_jsonl(self.qa_log, limit)
        miss = self._tail_jsonl(self.miss_log, limit)
        return {"qa": list(reversed(qa)), "miss": list(reversed(miss)),
                "console": self.log.tail(60)}

    # ------------------------------------------------------------------ AI
    def ai_test(self) -> dict:
        """测试大模型连通性：连通性 + 往返延迟。"""
        if self.asst.ai is None or self.asst.ai.client is None:
            return {"ok": False, "error": "AI 未配置或未启用"}
        return self.asst.ai.client.test_connection()

    def ai_tts_test(self) -> dict:
        """测试语音合成：合成一句短话，报耗时。"""
        if self.asst.ai is None or self.asst.ai.tts is None:
            return {"ok": False, "error": "语音合成未配置"}
        return self.asst.ai.tts.test()

    def ai_review(self) -> dict:
        """赛后复盘：列出本场走过模型的问答。"""
        rows = []
        for rec in getattr(self.asst, "history", []) or []:
            if not rec.get("ai_text"):
                continue
            rows.append({"id": rec.get("review_id"), "q": rec.get("q"),
                         "answer": rec.get("ai_text"),
                         "source": rec.get("ai_source"),
                         "checks": rec.get("ai_checks") or {},
                         "latency_ms": rec.get("ai_latency_ms")})
        return {"rows": rows[-100:]}

    def ai_review_mark(self, entry_id: str, verdict: str) -> dict:
        """复盘标记。`ok` 入学习库，`bad` 降权，`meh` 只记录。"""
        if verdict not in ("ok", "bad", "meh"):
            raise ValueError("verdict 必须是 ok / bad / meh")
        if not entry_id:
            raise ValueError("缺少条目 id")
        store = self.asst.learn_store()
        if store is None:
            return {"ok": False, "error": "学习库不可用"}
        if verdict == "ok":
            record = (self.asst.learn_candidate(entry_id) or {})
            kind = store.add_from_review(record)
            return {"ok": True, "stored_as": kind}
        store.mark(entry_id, ok=(verdict != "bad"))
        return {"ok": True}

    def ai_clear_learn(self) -> dict:
        store = self.asst.learn_store()
        if store is None:
            return {"ok": False, "error": "学习库不可用"}
        store.clear()
        return {"ok": True}

    def export_logs(self) -> dict:
        """把运行日志打包成 zip，方便随 bug 反馈一起提交。"""
        import datetime, platform, sys as _sys, zipfile
        log_dir = self.asst.log_dir
        out_dir = log_dir.parent / "日志导出"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = out_dir / f"LittleNavigator-logs-{stamp}.zip"
        note = out_dir / f"环境-{stamp}.txt"
        note.write_text("\n".join([
            f"导出时间：{datetime.datetime.now().isoformat(timespec='seconds')}",
            f"语音包：{self.asst.pack_info()}",
            # config_view 里的 ai 段走 ai_config_view()，只含 api_key_set 布尔，
            # 不含密钥明文——日志导出会随 bug 反馈外发，必须挡在这里。
            f"配置：{self.asst.config_view()}",
            f"Python：{_sys.version.split()[0]}  {platform.platform()}",
        ]), encoding="utf-8")
        count = 0
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(log_dir.rglob("*")):
                if path.is_file() and path.resolve() != target.resolve():
                    z.write(path, path.relative_to(log_dir))
                    count += 1
            z.write(note, note.name)
        note.unlink(missing_ok=True)
        return {"ok": True, "path": str(target), "size": target.stat().st_size, "files": count}

    def stats(self) -> dict:
        qa = self._tail_jsonl(self.qa_log, 1000)
        miss = self._tail_jsonl(self.miss_log, 1000)
        layers: dict[str, int] = {}
        latency = [r["first_audio_ms"] for r in qa if r.get("ok") and r.get("first_audio_ms") is not None]
        buckets = [0, 0, 0, 0]          # ≤250 / 250–350 / 350–700 / >700
        for v in latency:
            if v <= 250:
                buckets[0] += 1
            elif v <= 350:
                buckets[1] += 1
            elif v <= 700:
                buckets[2] += 1
            else:
                buckets[3] += 1
        for r in qa:
            layers[r.get("layer", "?")] = layers.get(r.get("layer", "?"), 0) + 1
        miss_counter: collections.Counter = collections.Counter()
        for r in miss:
            q = (r.get("q") or "").strip()
            if q:
                miss_counter[q] += 1
        return {
            "total": len(qa), "miss": sum(not r.get("ok", True) for r in qa),
            "avg_first_audio_ms": round(sum(latency) / len(latency), 1) if latency else None,
            "buckets": buckets,
            "layers": layers,
            "top_miss": miss_counter.most_common(12),
        }

    def knowledge(self) -> dict:
        """知识库页面数据：问法库、意图路由与遥测字段的只读视图。

        全部由 src/knowledge.py 现算，不读日志、不调模型、不改配置。
        结果在 knowledge 内部缓存 30 秒；词库文件改动后自动失效。
        """
        from src import knowledge as kb

        data = kb.build()
        data["app_version"] = app_version()
        return data

    # ---------------------------------------------------------------- 设定
    def devices(self) -> dict:
        import sounddevice as sd

        ins, outs = [], []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                ins.append({"index": i, "name": d["name"]})
            if d["max_output_channels"] > 0:
                outs.append({"index": i, "name": d["name"]})
        default = sd.default.device
        from src.wheel import get_input
        wheels=get_input()
        return {"inputs": ins, "outputs": outs,
                "default_input": default[0], "default_output": default[1],
                "wheels":wheels.devices(),"wheel_error":wheels.error}

    def configure(self, patch: dict) -> dict:
        """写 config.local.yaml（覆盖层）并尽力热生效。"""
        allowed = {"intent": {"voice_strict", "l1_keyword"}, "asr": {"model", "num_threads"},
                   "car_control": {"enabled", "verify"},
                   "ai": {"enabled", "mode", "provider", "base_url", "model",
                          "api_key", "timeout_s", "max_tokens",
                          "max_answer_chars", "rate_limit_per_min",
                          "analysis_key", "whitelist_intents", "tts"},
                   "ptt": {"vk", "binding", "min_hold_ms", "max_hold_s", "poll_ms", "input_device"},
                   "playback": {"enabled", "volume", "device", "ducking"},
                   "voice_pack": {"cooldown"}}
        if not isinstance(patch, dict):
            raise ValueError("patch 必须为配置对象")
        for section, values in patch.items():
            if section not in allowed or not isinstance(values, dict) or set(values) - allowed[section]:
                raise ValueError(f"不支持的配置项：{section}")
        for section, key, keys in [("intent", "l1_keyword", {"enabled"}),
                                   ("playback", "ducking", {"enabled", "amount_db", "processes"}),
                                   ("ai", "tts", {"adapter", "base_url", "api_key",
                                                  "model", "voice", "speaker",
                                                  "resource_id", "format",
                                                  "sample_rate", "timeout_s"})]:
            if key in patch.get(section, {}):
                value = patch[section][key]
                if not isinstance(value, dict) or set(value) - keys:
                    raise ValueError(f"不支持的配置项：{section}.{key}")
        candidate = cfg_mod._merge(self.cfg, patch)
        cfg_mod.validate(candidate)
        if "model" in patch.get("asr",{}):
            subdir, names = {
                "sense-voice": ("sense-voice", ["model.int8.onnx","tokens.txt"]),
                "zipformer-ctc": ("zipformer-ctc-zh", ["model.int8.onnx","tokens.txt"]),
                "streaming-zip": ("streaming-zipformer-zh", ["encoder.int8.onnx","decoder.onnx","joiner.int8.onnx","tokens.txt"]),
            }[candidate["asr"]["model"]]
            directory = cfg_mod.resolve(candidate,candidate["asr"]["models_dir"]) / subdir
            if any(not (directory/name).is_file() for name in names):
                raise ValueError(f"该模型尚未安装：{directory}")
        with self.listener_lock:
            return self._configure(patch)

    def _configure(self, patch):
        local = pathlib.Path(self.cfg['_local_path'])
        local.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if local.exists():
            data = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
        data = cfg_mod._merge(data, patch)
        candidate = cfg_mod._merge(self.cfg, patch)
        cfg_mod.validate(candidate)
        was_running = bool(self.listener and self.listener.running)
        restart = "ptt" in patch or "asr" in patch
        if restart and self.stop_listener().get("running"):
            return {"ok": False, "error": "监听尚未结束，配置未保存"}
        tmp = local.with_suffix(".tmp")
        tmp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(local)
        # 热生效
        live = {}
        if "playback" in patch:
            pb = patch["playback"]
            for key, mapped in (("volume", "volume"), ("device", "output_device")):
                if key in pb:
                    live[mapped] = pb[key]
            if "enabled" in pb:
                live["enabled"] = pb["enabled"]
            if "ducking" in pb:
                live["ducking"] = (pb["ducking"] or {}).get("enabled", True)
        # 写回内存配置，state() 立即可见
        self.cfg = cfg_mod.load(self.cfg_path)
        self.asst.cfg = self.cfg
        switched = {}
        with self.asst._operation_lock:
            self.asst.apply_live(live)
            self.asst.picker.cooldown = self.cfg["voice_pack"]["cooldown"]
            duck = self.cfg["playback"]["ducking"]
            self.asst.ducker.amount_db = duck["amount_db"]
            self.asst.ducker.procs = [p.lower() for p in duck["processes"]]
            # 与正在执行的改车互斥，避免热重载关闭其 SDK 连接。
            if "ai" in patch or "car_control" in patch:
                switched = self.asst.reload_switchables(self.cfg)

        needs_restart = []
        if "asr" in patch:
            self.asr = None
        result = self.start_listener() if restart and was_running else None
        if result and result.get("error"):
            return {"ok": True, "saved": str(local), "error": result["error"],
                    "restart_hint": [], "applied": switched}
        return {"ok": True, "saved": str(local), "restart_hint": needs_restart,
                "applied": switched}


# ---------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    service: Service
    server_version = "iracing-voice-assistant/0.1"

    # 静音默认日志（我们有自己的 RingLog）
    def log_message(self, fmt, *args):  # noqa: ANN001
        pass

    # ---- 工具 ----
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > 65536:
            raise ValueError("请求体过大")
        if not n:
            return {}
        result = json.loads(self.rfile.read(n).decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("请求必须为 JSON 对象")
        return result

    def _file(self, path: pathlib.Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png"}.get(path.suffix, "application/octet-stream")
        data = path.read_bytes()
        if path.suffix == ".html":
            data = data.replace(b'<script src="/static/app.js">',
                f'<meta name="assistant-token" content="{self.service.api_token}"><script src="/static/app.js">'.encode())
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        if not self._local_request():
            return
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                self._file(STATIC_DIR / "index.html")
            elif u.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            elif u.path.startswith("/static/"):
                target = (STATIC_DIR / unquote(u.path[len("/static/"):])).resolve()
                if not target.is_relative_to(STATIC_DIR.resolve()):
                    self.send_error(403)
                else:
                    self._file(target)
            elif u.path == "/api/state":
                self._json(self.service.state())
            elif u.path == "/api/log":
                self._json(self.service.log_data(int(q.get("limit", ["80"])[0])))
            elif u.path == "/api/ai/review":
                self._json(self.service.ai_review())
            elif u.path == "/api/stats":
                self._json(self.service.stats())
            elif u.path == "/api/knowledge":
                self._json(self.service.knowledge())
            elif u.path == "/api/devices":
                self._json(self.service.devices())
            elif u.path == '/api/wheels':
                from src.wheel import get_input
                wheel=get_input()
                self._json({'wheels':wheel.devices(),'wheel_error':wheel.error})
            elif u.path == '/api/export-logs':
                self._json(self.service.export_logs())
            else:
                self.send_error(404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        if not self._local_request(mutation=True):
            return
        u = urlparse(self.path)
        try:
            body = self._body()
            if u.path == "/api/ask":
                text = str(body.get("text") or "").strip()
                # 超长和为空是两回事，回报的原因必须对得上——
                # 实测提交 1000 个「啊」时返回「text 为空」，用户会以为软件没收到。
                if not text:
                    self._json({"error": "text 为空"}, 400)
                    return
                if len(text) > 512:
                    self._json({"error": "text 过长（最长 512 字符）"}, 400)
                    return
                self._json(self.service.ask(text, play=bool(body.get("play"))))
            elif u.path == "/api/play":
                self._json(self.service.play(str(body.get("msgId") or "")))
            elif u.path == "/api/listen":
                running = bool(body.get("running"))
                self._json(self.service.start_listener() if running
                           else self.service.stop_listener())
            elif u.path == "/api/ptt/simulate":
                self._json(self.service.simulate_ptt(float(body.get("seconds") or 2.0)))
            elif u.path == "/api/ptt/capture":
                from src.listener import capture_key

                timeout = float(body.get("timeout_s") or 6.0)
                if not 0.1 <= timeout <= 10:
                    raise ValueError("采集超时必须为 0.1–10 秒")
                got = self.service.capture_ptt(body.get('kind','keyboard'),timeout)
                self._json(got or {"error": "超时没等到按键"})
            elif u.path == "/api/quit":
                self.service.quit_requested = True
                self._json({"ok": True, "hint": "窗口即将关闭"})
            elif u.path == "/api/config":
                self._json(self.service.configure(body.get("patch") or {}))
            elif u.path == "/api/ai/test":
                self._json(self.service.ai_test())
            elif u.path == "/api/ai/tts/test":
                self._json(self.service.ai_tts_test())
            elif u.path == "/api/ai/review/mark":
                self._json(self.service.ai_review_mark(
                    str(body.get("id") or ""), body.get("verdict") or ""))
            elif u.path == "/api/ai/learn/clear":
                self._json(self.service.ai_clear_learn())
            else:
                self.send_error(404)
        except (ValueError, TypeError, UnicodeError, KeyError) as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _local_request(self, mutation=False):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        if host not in allowed or (origin and origin != f"http://{host}"):
            self._json({"error": "仅允许当前本机界面访问"}, 403)
            return False
        if mutation and not secrets.compare_digest(self.headers.get("X-Assistant-Token", ""), self.service.api_token):
            self._json({"error": "请求缺少有效的本机界面凭据"}, 403)
            return False
        return True


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--config", default=None)
    ap.add_argument("--sim", action="store_true", help="强制模拟遥测")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-listen", action="store_true", help="启动时不自动开 PTT 监听")
    args = ap.parse_args(argv)
    cfg_mod.fix_console_encoding()
    if args.host not in ("127.0.0.1", "localhost"):
        ap.error("服务仅允许监听本机回环地址")

    svc = Service(cfg_path=args.config, sim=args.sim)
    print("=" * 74)
    print("  iRacing 语音问答助手 · 桌面应用")
    print("=" * 74)
    for line in svc.asst.info_lines():
        print(line)
    if svc.asst.tel_warn:
        print(f"⚠ {svc.asst.tel_warn}")
    print(f"播放后端 = {svc.asst.player.backend}")

    if not args.no_listen:
        r = svc.start_listener()
        print(f"PTT 监听：{'已启动' if r.get('running') else '未启动（' + str(r.get('error')) + '）'}")

    Handler.service = svc
    httpd = Server((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"\n界面：{url}   （Ctrl+C 退出）\n")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    def watch_quit():
        while not svc.quit_requested:
            time.sleep(0.2)
        httpd.shutdown()
    threading.Thread(target=watch_quit,daemon=True,name="quit-watch").start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n收工。")
    finally:
        svc.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
