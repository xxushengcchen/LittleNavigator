"""v1.6.2 运行期批次2（R01/R03/R04/R05/R07/R08/R09/R10/R12/R14/R15）。

原则：不接真实硬件/话筒/方向盘/托盘，不发声、不调模型；并发语义一律用
``threading.Event`` 精确排时序，不靠 sleep 猜竞态。
"""
from __future__ import annotations

import io
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from src.car_control import CarControlError, CarController
from src.engine import Assistant
from src.listener import PttListener
from src.player import Player
from src import voice_app


# ---------------------------------------------------------------- helpers
def engine_cfg(root: pathlib.Path, ai_enabled: bool = False) -> dict:
    return {
        "voice_pack": {"dir": str(root / "pack"), "extra_dir": None,
                       "telemetry_dir": None, "cooldown": 0,
                       "state_file": str(root / "cooldown.json")},
        "telemetry": {"provider": "sim", "poll_hz": 1,
                      "ring_buffer": {"keep_laps": 5}},
        "intent": {"l1_keyword": {"enabled": False}, "voice_strict": True,
                   "miss_log": {"enabled": False,
                                "path": str(root / "miss.jsonl")}},
        "playback": {"enabled": False, "volume": 1.0},
        "logging": {"dir": str(root / "logs"),
                    "qa_log": str(root / "logs" / "qa.jsonl")},
        "asr": {"model": "sense-voice", "num_threads": 1, "models_dir": "m"},
        "ptt": {"vk": 0xA3, "poll_ms": 10, "min_hold_ms": 250, "max_hold_s": 8},
        "ai": {"enabled": ai_enabled, "mode": "full", "base_url": "http://x",
               "api_key": "k", "model": "m", "timeout_s": 2.0,
               "max_tokens": 300, "max_answer_chars": 60,
               "rate_limit_per_min": 6, "whitelist_intents": [],
               "analysis_key": None, "tts": {"adapter": "off"}},
        "_root": str(root), "_local_path": str(root / "l.json"),
        "_ai_learn_dir": str(root / "ai_learn"),
    }


def empty_pack() -> MagicMock:
    pack = MagicMock()
    pack.variants = {}
    pack.has.return_value = False
    pack.texts.return_value = []
    pack.stats.return_value = {"msgIds": 0, "clips": 0,
                               "avg_variants": 0}
    return pack


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()

    def assistant(self, ai_enabled=False, pack=None, **over):
        cfg = engine_cfg(self.root, ai_enabled)
        cfg["ai"].update(over)
        with patch("src.engine.VoicePack.load_many",
                   return_value=pack or empty_pack()):
            return Assistant(cfg, no_play=True, out=io.StringIO())


def listener_cfg(**ptt) -> dict:
    base = {"ptt": {"vk": 0xA3, "poll_ms": 10, "min_hold_ms": 250,
                    "max_hold_s": 8}}
    base["ptt"].update(ptt)
    return base


class FakeAsr:
    def __init__(self, texts=None):
        self.texts = list(texts or ["问题一", "问题二", "问题三"])
        self.key = "fake"
        self.calls = 0

    def transcribe(self, audio, sr):
        self.calls += 1
        idx = min(self.calls - 1, len(self.texts) - 1)
        return SimpleNamespace(text=self.texts[idx])


# ---------------------------------------------------------------- R01
class R01ListenerDoesNotBlockPollingTests(unittest.TestCase):
    """AI 等待期间轮询线程不能被回调占住，否则第二次按松直接丢失。"""

    def test_second_press_release_is_delivered_while_first_callback_blocks(self):
        gate = threading.Event()
        first_entered = threading.Event()
        second_done = threading.Event()
        got: list[str] = []

        def on_result(text, meta):
            got.append(text)
            if len(got) == 1:
                first_entered.set()
                gate.wait(5)
            else:
                second_done.set()

        asr = FakeAsr(["问题一", "问题二"])
        listener = PttListener(asr, listener_cfg(),
                               on_result, out=io.StringIO())
        key = {"down": False, "polls": 0, "records": 0,
               "released_polls": 0}

        def fake_key_down(vk):
            key["polls"] += 1
            if not key["down"]:
                key["released_polls"] += 1
            return key["down"]

        audio = np.full(8000, 0.1, dtype=np.float32)

        def fake_record(released):
            key["records"] += 1
            return audio

        with patch("src.listener._key_down", side_effect=fake_key_down), \
             patch.object(listener, "_record_while", side_effect=fake_record):
            listener.start()
            try:
                key["down"] = True
                self.assertTrue(first_entered.wait(2), "第一次录音没有触发回调")
                # 第一次回调仍阻塞；此刻完成第二次完整按松。
                baseline = key["released_polls"]
                key["down"] = False
                # 等轮询线程真的看到「松键」，再按下第二次；否则等于没松开。
                deadline = time.monotonic() + 2
                while (key["released_polls"] <= baseline
                       and time.monotonic() < deadline):
                    pass
                self.assertGreater(key["released_polls"], baseline,
                                   "轮询线程没有看到第一次松键")
                key["down"] = True
                time.sleep(0.05)          # 让轮询线程至少看到一次按下
                key["down"] = False
                deadline = time.monotonic() + 2
                while key["records"] < 2 and time.monotonic() < deadline:
                    pass
                self.assertGreaterEqual(
                    key["records"], 2,
                    "回调阻塞期间轮询停止：第二次按松被整段丢失")
                gate.set()
                self.assertTrue(second_done.wait(3),
                                "第二次提问排在第一次之后却没有被处理")
                self.assertEqual(got, ["问题一", "问题二"])
            finally:
                key["down"] = False
                gate.set()
                listener.stop()


# ---------------------------------------------------------------- R03
class R03OverlayStaleAnswerTests(TmpCase):
    def test_refusal_does_not_keep_previous_answer(self):
        a = self.assistant(ai_enabled=False)
        a.matcher = MagicMock()
        a.matcher.match.return_value = MagicMock(
            intent=None, score=0.1, phrase="", margin=0.0,
            reason="未命中", ranked=[])
        a.live.update({"state": "answer", "answer": "上一题：油量20L",
                       "error": ""})
        rec = a.ask("今天天气不错", source="keyboard")
        self.assertFalse(rec["ok"])
        self.assertNotEqual(a.live["answer"], "上一题：油量20L")
        self.assertNotEqual(a.live["state"], "thinking")

    def test_fixed_feedback_updates_live_answer(self):
        tmp_wav = self.root / "x.wav"
        tmp_wav.write_bytes(b"RIFF")
        pack = empty_pack()
        variant = SimpleNamespace(text="换胎已设置")
        pack.has.side_effect = lambda mid: True
        pack.variants = {"AST_CC_TYRE": [variant]}
        pack.texts.return_value = ["换胎已设置"]
        pack.path_of.return_value = tmp_wav
        a = self.assistant(ai_enabled=False, pack=pack)
        a.player.play = MagicMock(return_value=0.0)
        a.live.update({"state": "answer", "answer": "上一题：油量20L"})
        a._say("AST_CC_TYRE")
        self.assertEqual(a.live["answer"], "换胎已设置")


# ---------------------------------------------------------------- R04
class R04AnalysisKeyHotUpdateTests(TmpCase):
    def test_running_listener_picks_up_new_analysis_key(self):
        ana_entered = threading.Event()
        ptt_called: list[str] = []

        def on_ptt(text, meta):
            ptt_called.append(text)

        def on_analysis(text, meta):
            ana_entered.set()

        listener = PttListener(FakeAsr(["分析问题"]), listener_cfg(),
                               on_ptt, out=io.StringIO(),
                               analysis_vk=None,
                               on_analysis_result=on_analysis)
        self.assertTrue(hasattr(listener, "apply_analysis_key"),
                        "运行中的监听器没有热更新分析键的入口")
        key = {"down": False}

        def fake_key_down(vk):
            return key["down"] and vk == 0x5B

        with patch("src.listener._key_down", side_effect=fake_key_down), \
             patch.object(listener, "_record_while",
                          return_value=np.full(8000, 0.1, dtype=np.float32)):
            listener.start()
            try:
                listener.apply_analysis_key(0x5B)
                self.assertEqual(listener.analysis_vk, 0x5B)
                key["down"] = True
                self.assertTrue(ana_entered.wait(2),
                                "热更新后的分析键没有生效")
            finally:
                key["down"] = False
                listener.stop()
        self.assertEqual(ptt_called, [])

    def test_same_key_as_ptt_is_still_rejected_on_hot_update(self):
        listener = PttListener(FakeAsr(), listener_cfg(), lambda *a: None,
                               out=io.StringIO(), analysis_vk=None)
        listener.apply_analysis_key(0xA3)
        self.assertIsNone(listener.analysis_vk)


# ---------------------------------------------------------------- R05
class R05DeadWheelBackendTests(unittest.TestCase):
    def test_dead_sdl_backend_marks_stop_and_singleton_is_rebuilt(self):
        from src import wheel as wheel_mod

        class FakeSdl(types.ModuleType):
            SDL_INIT_JOYSTICK = 0x200
            SDL_IGNORE = 0

            def SDL_SetHint(self, *a):
                pass

            def SDL_InitSubSystem(self, flags):
                raise RuntimeError("joystick init failed")

        old = wheel_mod._instance
        wheel_mod._instance = None
        try:
            with patch.dict(sys.modules, {"sdl2": FakeSdl("sdl2")}):
                first = wheel_mod.get_input()
                self.assertIn("joystick init failed", first.error or "")
                self.assertTrue(first._stop.is_set(),
                                "后端线程已死却没有置停止位，单例永远卡死")
                second = wheel_mod.get_input()
                self.assertIsNot(first, second,
                                 "死亡单例没有被重建，停开无法恢复")
                second.close()
        finally:
            wheel_mod.close_input()
            wheel_mod._instance = old

    def test_listener_reports_dead_wheel_instead_of_running_blind(self):
        class DeadWheel:
            error = "方向盘输入不可用：假故障"

            def pressed(self, binding):
                return False

        with patch("src.wheel.get_input", return_value=DeadWheel()):
            listener = PttListener(
                FakeAsr(), listener_cfg(binding={"guid": "g", "button": 0}),
                lambda *a: None, out=io.StringIO())
        with patch("src.listener._key_down", return_value=False):
            listener.start()
            listener._thread.join(1.0)
        self.assertFalse(listener.running)
        self.assertIn("假故障", listener.error or "")


# ---------------------------------------------------------------- R07
class R07CliAiFollowupTests(unittest.TestCase):
    def _fake_asst(self, first_rec):
        calls = {"ask": [], "ask_ai": [], "judge": []}

        class FakeAsst:
            def __init__(self):
                self.tel_warn = None
                self.tel = object()
                self.player = SimpleNamespace(backend="dry-run")
                self.qa_log = "qa.jsonl"

            def info_lines(self):
                return []

            def close(self):
                pass

            def ask(self, text, **kw):
                calls["ask"].append((text, kw))
                if kw.get("forced_intent"):
                    return {"ok": True, "intent": kw["forced_intent"],
                            "kind": "known"}
                return dict(first_rec)

            def judge_intent(self, text, candidates):
                calls["judge"].append((text, candidates))
                return "query_fuel"

            def ask_ai(self, text, kind, intent, rule_ok, **kw):
                calls["ask_ai"].append((text, kw.get("source")))
                return {"ok": True, "reason": "AI"}

        return FakeAsst(), calls

    def _run_wav(self, asst, wav="x.wav"):
        cfg = {"telemetry": {"provider": "sim"}, "ptt": {},
               "asr": {"models_dir": "m"},
               "ai": {"analysis_key": {"vk": 0x5B}}}
        args = SimpleNamespace(sim=False, no_play=False, pack=None,
                               config=None, model=None, device=None,
                               list_devices=False, selftest=0.0,
                               wav=wav)
        fake_asr = SimpleNamespace(load_sec=0.01, label="fake",
                                   warmup=lambda: None,
                                   transcribe=lambda x, sr: SimpleNamespace(
                                       text="油还够吗", decode_sec=0.01))
        captured = {}

        class FakeListener:
            stats = {"segments": 0, "ignored_busy": 0,
                     "too_short": 0, "errors": 0}

            def __init__(self, *a, **kw):
                captured.update(kw)

            def start(self):
                pass

            def stop(self):
                pass

        with patch.object(voice_app.cfg_mod, "load", return_value=cfg), \
             patch("tools.asr_models.load", return_value=fake_asr), \
             patch("tools.audio_io.read_wav",
                   return_value=(np.zeros(16000, dtype=np.float32), 16000)), \
             patch.object(voice_app, "PttListener", FakeListener), \
             patch("src.voice_app.time.sleep",
                   side_effect=KeyboardInterrupt), \
             patch("src.listener.check_input_device", return_value=None), \
             patch("builtins.print"):
            voice_app._run_with_assistant(asst, cfg, args, "sense-voice", 1)
        return captured

    def test_ai_eligible_wav_question_continues_to_ask_ai(self):
        asst, calls = self._fake_asst(
            {"ok": False, "ai_eligible": True, "intent": None,
             "kind": "analysis", "reason": "需要分析"})
        self._run_wav(asst)
        self.assertEqual(len(calls["ask_ai"]), 1,
                         "CLI 丢弃了 ai_eligible，all 档会无声结束")

    def test_disambiguation_request_is_judged_and_reasked(self):
        asst, calls = self._fake_asst(
            {"ok": False, "ai_disambiguate": {"candidates": [{"intent": "a"}]},
             "kind": "known", "intent": None})
        self._run_wav(asst)
        self.assertEqual(len(calls["judge"]), 1)
        self.assertTrue(any(kw.get("forced_intent") == "query_fuel"
                            for _, kw in calls["ask"]),
                        "仲裁结果没有回填给规则层")

    def test_cli_listener_receives_analysis_key(self):
        asst, _ = self._fake_asst({"ok": True, "kind": "known"})
        captured = self._run_wav(asst, wav=None)
        self.assertEqual(captured.get("analysis_vk"), 0x5B,
                         "CLI listener 没拿到分析键，分析提问走成普通 PTT")


# ---------------------------------------------------------------- R08
class R08CarControlCloseRaceTests(unittest.TestCase):
    def test_close_cannot_slip_between_available_and_send(self):
        class FakeSdk:
            def __init__(self):
                self.is_initialized = True
                self.is_connected = True
                self.closed = False
                self.events: list[str] = []
                self.entered = threading.Event()
                self.gate = threading.Event()

            def pit_command(self, mode, var=0):
                self.entered.set()
                self.gate.wait(5)
                self.events.append("send")

            def shutdown(self):
                self.closed = True
                self.events.append("close")

        fake = FakeSdk()
        ctrl = CarController({"car_control": {"enabled": True,
                                              "verify": False}},
                             sdk=fake, out=io.StringIO())
        ctrl._owns_sdk = True          # 模拟自有连接，便于观察 shutdown 时序
        holder = {}

        def send():
            holder["rec"] = ctrl.apply({"kind": "clear"})

        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        self.assertTrue(fake.entered.wait(2))
        closer_done = threading.Event()

        def close():
            ctrl.close()
            closer_done.set()

        closer = threading.Thread(target=close, daemon=True)
        closer.start()
        closed_while_sending = closer_done.wait(0.5)
        fake.gate.set()
        sender.join(3)
        closer.join(3)
        self.assertFalse(
            closed_while_sending,
            "close 在发送窗口内完成：available 与 pit_command 不是同一个临界区")
        self.assertEqual(fake.events, ["send", "close"],
                         "关闭后仍然往 SDK 发了一条命令")

    def test_send_after_close_is_refused(self):
        class FakeSdk:
            is_initialized = True
            is_connected = True

            def pit_command(self, mode, var=0):
                raise AssertionError("关闭后不允许再发命令")

            def shutdown(self):
                pass

        ctrl = CarController({"car_control": {"enabled": True,
                                              "verify": False}},
                             sdk=FakeSdk(), out=io.StringIO())
        ctrl.close()
        with self.assertRaises(CarControlError):
            ctrl._send(0)


# ---------------------------------------------------------------- R09
class R09RecordOverrideSourceTests(unittest.TestCase):
    def test_record_for_uses_ptt_callback_even_after_analysis_key_use(self):
        got: list[str] = []
        listener = PttListener(
            FakeAsr(["界面测试"]), listener_cfg(),
            lambda text, meta: got.append("ptt"),
            out=io.StringIO(), analysis_vk=0x5B,
            on_analysis_result=lambda text, meta: got.append("analysis"))
        listener.pending_source = "analysis"      # 上一次分析键留下的状态
        with patch.object(listener, "_record_while",
                          return_value=np.full(8000, 0.1, dtype=np.float32)), \
             patch("src.listener._trim_silence", side_effect=lambda x, sr: x):
            result = listener.record_for(0.5)
        self.assertNotIn("error", result)
        self.assertEqual(got, ["ptt"],
                         "界面测试录音继承了上次分析键来源")


# ---------------------------------------------------------------- R10
class R10HeldAnalysisKeyOnStartTests(unittest.TestCase):
    def test_analysis_key_held_before_start_waits_for_release(self):
        recorded = threading.Event()
        listener = PttListener(FakeAsr(), listener_cfg(), lambda *a: None,
                               out=io.StringIO(), analysis_vk=0x5B)
        key = {"analysis": True, "released_polls": 0}

        def fake_key_down(vk):
            if vk == 0x5B:
                if not key["analysis"]:
                    key["released_polls"] += 1
                return key["analysis"]
            return False

        with patch("src.listener._key_down", side_effect=fake_key_down), \
             patch.object(listener, "_record_while",
                          side_effect=lambda released: (
                              recorded.set(),
                              np.full(8000, 0.1, dtype=np.float32))[1]):
            listener.start()
            try:
                self.assertFalse(recorded.wait(0.4),
                                 "启动时已按住的分析键被当成一次新提问")
                key["analysis"] = False
                deadline = time.monotonic() + 2
                while key["released_polls"] == 0 and time.monotonic() < deadline:
                    pass
                self.assertGreaterEqual(key["released_polls"], 1)
                key["analysis"] = True
                self.assertTrue(recorded.wait(2),
                                "松开后的一次真实按下仍应触发录音")
            finally:
                key["analysis"] = False
                listener.stop()


# ---------------------------------------------------------------- R12
class R12WinsoundFallbackTests(unittest.TestCase):
    def _wav(self, path: pathlib.Path):
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 160)

    def test_fallback_honours_gap_and_on_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            p1, p2 = root / "a.wav", root / "b.wav"
            self._wav(p1)
            self._wav(p2)

            class FakeWinsound:
                SND_FILENAME = 1
                SND_PURGE = 2

                def __init__(self):
                    self.played: list[str] = []

                def PlaySound(self, path, flags):
                    if path is not None:
                        self.played.append(path)

            player = Player(enabled=True, out=io.StringIO())
            fake_ws = FakeWinsound()
            player._sd = None
            player._winsound = fake_ws
            started = threading.Event()
            with patch("src.player.time.sleep") as sleep:
                player.play_many([p1, p2], gap_ms=60,
                                 on_start=started.set)
            self.assertTrue(started.is_set(),
                            "winsound 回退没有回调 on_start，first_audio_ms 仍为空")
            self.assertEqual(len(fake_ws.played), 2)
            sleep.assert_called_once_with(0.06)


# ---------------------------------------------------------------- R14
class R14SelftestDeviceAndFormatTests(unittest.TestCase):
    def test_selftest_uses_configured_device_and_native_format(self):
        cfg = {"telemetry": {"provider": "sim"},
               "asr": {"model": "sense-voice", "num_threads": 1,
                       "models_dir": "m"},
               "ptt": {"input_device": "chosen-device"},
               "ai": {}}
        rec_calls = {}
        asr = SimpleNamespace(load_sec=0.01, label="fake",
                              warmup=lambda: None,
                              transcribe=lambda x, sr: SimpleNamespace(
                                  text="好", decode_sec=0.01))
        probe_calls = []

        class FakeSd:
            def query_devices(self, *a, **k):
                return "devices"

            def rec(self, frames, samplerate, channels, dtype, device):
                rec_calls.update(frames=frames, samplerate=samplerate,
                                 channels=channels, device=device)
                return np.zeros((frames, channels), dtype=np.float32)

            @staticmethod
            def wait():
                pass

        with patch.object(voice_app.cfg_mod, "load", return_value=cfg), \
             patch("src.listener.check_input_device",
                   side_effect=lambda d: probe_calls.append(d)), \
             patch("src.listener.input_format",
                   return_value=(48000, 1)), \
             patch("tools.asr_models.load", return_value=asr), \
             patch.dict(sys.modules, {"sounddevice": FakeSd()}), \
             patch("builtins.print"):
            rc = voice_app.main(["--selftest", "0.1"])
        self.assertEqual(rc, 0)
        self.assertEqual(probe_calls, ["chosen-device"],
                         "自检探测用了默认麦而不是配置里的设备")
        self.assertEqual(rec_calls.get("device"), "chosen-device")
        self.assertEqual(rec_calls.get("samplerate"), 48000,
                         "自检录音没有沿用探测到的原生采样率")
        self.assertEqual(rec_calls.get("channels"), 1)


# ---------------------------------------------------------------- R15
class R15TrayLateStartTests(unittest.TestCase):
    def test_timed_out_tray_thread_is_abandoned_cleanly(self):
        from app import desktop

        addref_calls = {"n": 0}
        gate = threading.Event()
        app_run = MagicMock()

        class FakeClr(types.ModuleType):
            def AddReference(self, name):
                addref_calls["n"] += 1
                if addref_calls["n"] == 2:       # _run_ui 里的第一次
                    gate.wait(5)

        class FakeNetThread:
            def __init__(self, target):
                self.target = target
                self.IsBackground = False
                self.IsAlive = False
                self._t = None

            def SetApartmentState(self, *a):
                pass

            def Start(self):
                self._t = threading.Thread(target=self._body, daemon=True)
                self._t.start()

            def _body(self):
                self.IsAlive = True
                self.target()
                self.IsAlive = False

            def Join(self, ms):
                if self._t is not None:
                    self._t.join(ms / 1000.0)

        threading_mod = types.ModuleType("System.Threading")
        threading_mod.ApartmentState = SimpleNamespace(STA=1)
        threading_mod.Thread = FakeNetThread
        threading_mod.ThreadStart = lambda fn: fn
        system_mod = types.ModuleType("System")
        system_mod.Threading = threading_mod

        def fake_forms():
            mod = types.ModuleType("System.Windows.Forms")
            mod.Application = SimpleNamespace(Run=app_run,
                                              ExitThread=lambda: None)
            for name in ("ContextMenuStrip", "NotifyIcon", "Timer",
                         "ToolStripMenuItem", "ToolTipIcon"):
                setattr(mod, name, MagicMock())
            return mod

        def fake_drawing():
            mod = types.ModuleType("System.Drawing")
            mod.Bitmap = MagicMock()
            icon = MagicMock()
            icon.FromHandle = MagicMock(return_value=MagicMock())
            icon.ExtractAssociatedIcon = MagicMock(return_value=MagicMock())
            mod.Icon = icon
            return mod

        with patch.dict(sys.modules, {
                "clr": FakeClr("clr"),
                "System": system_mod,
                "System.Threading": threading_mod,
                "System.Windows.Forms": fake_forms(),
                "System.Drawing": fake_drawing(),
        }):
            tray = desktop.TrayIcon(None, lambda: None, log=lambda *a: None)
            started = tray.start(timeout=0.05)
            self.assertFalse(started)
            gate.set()
            if tray._thread is not None:
                tray._thread.Join(2000)
            tray.stop(timeout=1)
        self.assertEqual(app_run.call_count, 0,
                         "启动已判超时，晚到的托盘线程仍在建图标/进消息循环")
        self.assertFalse(tray.running)


if __name__ == "__main__":
    unittest.main()
