"""Q05/Q17-Q19: real tool entrypoints, with devices and ASR replaced at boundaries."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

VA = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()


def load_tool(name):
    spec = importlib.util.spec_from_file_location(f"audit_tool_{name}", VA / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def matcher_stub():
    module = types.ModuleType("src.phrase_match")
    phrases = [types.SimpleNamespace(intent=intent, text=text, norm=text,
                                    chars=text, syls=text, pystr=text)
               for intent, text in (("query_fuel", "甲问法"), ("query_weather", "乙问法"))]

    class Matcher:
        accept = 0.8
        margin = 0.1
        _canonical_candidates = phrases

        def __init__(self, *args):
            self.phrases = phrases

        def match(self, text):
            intent = next((p.intent for p in phrases if p.text == text), None)
            return types.SimpleNamespace(intent=intent, score=1.0 if intent else 0.0, reason="fixture")

        def rank(self, text):
            return []

    module.PhraseMatcher = Matcher
    module.normalize = lambda text: text
    module._sim = lambda *args: 1.0
    return module


def forbidden(*args, **kwargs):
    raise AssertionError("Real audio, device or ASR access is forbidden in these tests")


def audio_stubs():
    sd = types.ModuleType("sounddevice")
    sd.query_devices = lambda: [{"name": "fixture microphone", "max_input_channels": 1}]
    sd.rec = lambda *args, **kwargs: np.zeros((16000, 1), dtype=np.float32)
    sd.wait = lambda: None
    sd.play = sd.InputStream = forbidden
    listener = types.ModuleType("src.listener")
    listener.resolve_device = forbidden
    asr = types.ModuleType("tools.asr_models")
    asr.load = forbidden
    audio = types.ModuleType("tools.audio_io")
    audio.read_wav = forbidden
    return {"sounddevice": sd, "src.listener": listener, "tools.asr_models": asr,
            "tools.audio_io": audio, "src.phrase_match": matcher_stub()}


def worker(case, directory=None):
    """Subprocess fixtures never import a real model/device implementation."""
    if case == "library":
        with patch.dict(sys.modules, {"src.phrase_match": matcher_stub()}):
            tool = load_tool("check_library")
            with patch.object(sys, "argv", ["check_library.py"]):
                return tool.main()
    if case == "bench":
        import hashlib
        root = Path(directory)
        audio_dir = root / "audio"
        audio_dir.mkdir()
        manifest = [dict(file="sample.wav", phrase="fixture", intent="query_fuel",
                         neg=False, voice="fixture", rate=0)]
        (audio_dir / "manifest.jsonl").write_text(json.dumps(manifest[0]) + "\n", encoding="utf-8")
        asr_module = types.ModuleType("tools.asr_models")

        class FakeAsr:
            label = "fixture"
            load_sec = 0.01

            def warmup(self):
                pass

            def transcribe(self, audio, sr):
                return types.SimpleNamespace(text=hashlib.sha256(audio.tobytes()).hexdigest(),
                                             decode_sec=0.01, audio_sec=len(audio) / sr, chunks=1)

        asr_module.REGISTRY = {"fixture": FakeAsr}
        asr_module.SAMPLE_RATE = 16000
        asr_module.load = lambda *args, **kwargs: FakeAsr()
        audio_module = types.ModuleType("tools.audio_io")
        audio_module.read_wav = lambda path: (np.linspace(-0.5, 0.5, 160, dtype=np.float32), 16000)
        with patch.dict(sys.modules, {"src.phrase_match": matcher_stub(),
                                     "tools.asr_models": asr_module, "tools.audio_io": audio_module}):
            tool = load_tool("bench_asr")
            tool.AUDIO_DIR = audio_dir
            tool.OUT_DIR = root / "report"
            with patch.object(sys, "argv", ["bench_asr.py", "--models", "fixture"]):
                return tool.main()
    raise AssertionError(case)


class DevToolsFollowupTests(unittest.TestCase):
    def run_worker(self, case, *, encoding="utf-8", hash_seed="1", directory=None):
        env = {**os.environ, "PYTHONIOENCODING": encoding, "PYTHONHASHSEED": hash_seed,
               "PYTHONDONTWRITEBYTECODE": "1"}
        command = [sys.executable, "-B", str(SELF), "--worker", case]
        if directory is not None:
            command.append(str(directory))
        return subprocess.run(command, cwd=VA, env=env, capture_output=True, timeout=30)

    def test_library_reaches_final_result_in_ascii_and_gbk(self):
        for encoding in ("ascii:strict", "gbk:strict"):
            with self.subTest(encoding=encoding):
                result = self.run_worker("library", encoding=encoding)
                self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", "replace"))
                self.assertNotIn(b"UnicodeEncodeError", result.stderr)
                self.assertIn(b"[5]", result.stdout)

    def test_wheel_selftest_creates_missing_report_parent(self):
        state = {"pressed": False, "closed": False, "detached": False}
        wheel = types.SimpleNamespace(error=None,
            devices=lambda: [{"instance": 7, "guid": "fixture", "name": "fixture wheel", "buttons": 128}],
            capture=lambda *args: {"binding": {"button": 127}},
            pressed=lambda binding: state["pressed"])
        wheel_module = types.ModuleType("src.wheel")
        wheel_module.get_input = lambda: wheel
        wheel_module.close_input = lambda: state.update(closed=True)
        sdl = types.ModuleType("sdl2")
        sdl.SDL_JOYSTICK_TYPE_WHEEL = 1
        sdl.SDL_LockJoysticks = sdl.SDL_UnlockJoysticks = lambda: None
        sdl.SDL_JoystickAttachVirtual = lambda *args: 0
        sdl.SDL_JoystickOpen = lambda index: 1
        sdl.SDL_JoystickInstanceID = lambda handle: 7
        sdl.SDL_JoystickSetVirtualButton = lambda handle, button, value: (state.update(pressed=bool(value)) or 0)
        sdl.SDL_JoystickClose = lambda handle: None
        sdl.SDL_NumJoysticks = lambda: 1
        sdl.SDL_JoystickGetDeviceInstanceID = lambda index: 7
        sdl.SDL_JoystickDetachVirtual = lambda index: state.update(detached=True)
        with patch.dict(sys.modules, {"src.wheel": wheel_module, "sdl2": sdl}), tempfile.TemporaryDirectory() as tmp:
            tool = load_tool("wheel_selftest")
            tool.ROOT = Path(tmp) / "source" / "voice-assistant"
            report = tool.ROOT.parent / "audit" / "wheel-virtual-selftest.json"
            self.assertFalse(report.parent.exists())
            with contextlib.redirect_stdout(io.StringIO()):
                tool.main()
            self.assertTrue(report.is_file())
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(result["capture"])
            self.assertEqual(result["captured_display_button"], 128)
            self.assertTrue(state["closed"])
            self.assertTrue(state["detached"])

    def test_probe_without_wav_runs_with_stub_devices_only(self):
        with patch.dict(sys.modules, audio_stubs()):
            tool = load_tool("mic_loop_test")
            with patch.object(sys, "argv", ["mic_loop_test.py", "--probe"]), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(tool.main(), 0)
            self.assertIn("fixture microphone", output.getvalue())

    def test_probe_preserves_legacy_wav_argument(self):
        with patch.dict(sys.modules, audio_stubs()):
            tool = load_tool("mic_loop_test")
            with patch.object(sys, "argv", ["mic_loop_test.py", "unused.wav", "--probe"]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tool.main(), 0)

    def test_non_probe_still_requires_wav_before_touching_devices(self):
        with patch.dict(sys.modules, audio_stubs()):
            tool = load_tool("mic_loop_test")
            with patch.object(sys, "argv", ["mic_loop_test.py"]), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                tool.main()
            self.assertEqual(exc.exception.code, 2)

    def test_missing_wav_exits_two_with_explicit_strict_ascii_stderr(self):
        raw = io.BytesIO()
        with patch.dict(sys.modules, audio_stubs()), io.TextIOWrapper(raw, encoding="ascii", errors="strict") as stderr:
            tool = load_tool("mic_loop_test")
            with patch.object(sys, "argv", ["mic_loop_test.py"]), patch.object(sys, "stderr", stderr), self.assertRaises(SystemExit) as exc:
                tool.main()
            self.assertEqual(exc.exception.code, 2)
            stderr.flush()
            self.assertIn(b"WAV is required unless --probe is used", raw.getvalue())

    def bench_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = []
            for seed in ("1", "777"):
                path = Path(tmp) / seed
                path.mkdir()
                result = self.run_worker("bench", hash_seed=seed, directory=path)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
                records = [json.loads(line) for line in (path / "report" / "records-fixture.jsonl").read_text(encoding="utf-8").splitlines()]
                summary = json.loads((path / "report" / "summary.json").read_text(encoding="utf-8"))
                reports.append((records, summary))
            return reports

    def test_noisy_inputs_are_identical_across_python_hash_seeds(self):
        first, second = self.bench_reports()
        self.assertEqual([r["text"] for r in first[0]], [r["text"] for r in second[0]])

    def test_bench_reports_record_exact_seeds_and_algorithm(self):
        first, _ = self.bench_reports()
        records, summary = first
        self.assertTrue(all("noise_seed" in r for r in records), "report must record the actual augmentation seed")
        self.assertEqual([r["noise_seed"] for r in records], [0, 1752091661, 1752091661])
        self.assertEqual(summary["fixture"]["noise_seed_algorithm"], "sha256-utf8-filename-first32-mask31")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--worker":
        raise SystemExit(worker(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    unittest.main()
