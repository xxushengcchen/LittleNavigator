"""Round-6 follow-ups: R01 generation guard, F06 save guard, audio spec, A10 hypothetical."""
from __future__ import annotations

import queue
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "voice-assistant"))


class R01GenerationTests(unittest.TestCase):
    """评审发现：stop 后旧 worker 卡在回调里，start 后会出现两个消费者。"""

    def _listener(self):
        from src.listener import PttListener
        listener = PttListener.__new__(PttListener)
        listener._work_q = queue.Queue()
        listener._work_gen = 2
        listener._stop = __import__("threading").Event()
        listener.state = "idle"
        listener.error = None
        listener.stats = {"errors": 0}
        listener.out = __import__("io").StringIO()
        processed = []
        listener._process = lambda audio, source: processed.append((audio, source))
        return listener, processed

    def test_stale_worker_exits_without_consuming_queue(self):
        listener, processed = self._listener()
        listener._work_q.put(([1, 2, 3], "ptt"))
        listener._work_loop(1)                    # 旧代次
        self.assertEqual(processed, [])
        self.assertEqual(listener.state, "idle")

    def test_current_worker_processes_then_sets_idle(self):
        listener, processed = self._listener()
        listener._work_q.put(([1, 2, 3], "ptt"))
        listener._work_q.put(None)
        listener._work_loop(2)
        self.assertEqual(processed, [([1, 2, 3], "ptt")])
        self.assertEqual(listener.state, "idle")


class R01StartInvalidatesOldWorkerTests(unittest.TestCase):
    def test_start_bumps_generation(self):
        from src import listener as listener_mod
        source = Path(listener_mod.__file__).read_text(encoding="utf-8")
        self.assertIn("self._work_gen = getattr(self, \"_work_gen\", 0) + 1", source)
        self.assertIn("self._work_loop, args=(gen,)", source)


class F06SavedUrlGuardTests(unittest.TestCase):
    def test_models_button_blocks_unsaved_url(self):
        js = (ROOT / "voice-assistant" / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("committedUrl", js)
        self.assertIn("地址还没保存", js)


class AudioSpecTests(unittest.TestCase):
    def test_audit_defaults_to_shipped_24k(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "audit_offline", ROOT / "voice-assistant" / "tools" / "audit_offline.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.AUDIO_RATE, 24000)
        self.assertEqual(mod.format_errors(24000, 2, 1), [])
        self.assertTrue(mod.format_errors(16000, 2, 1))
        self.assertEqual(mod.format_errors(16000, 2, 1, mod.SLIM_RATE), [])

    def test_development_doc_matches_shipped_spec(self):
        doc = (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")
        self.assertIn("当前发行 24 kHz", doc)
        self.assertIn("--expect-rate 16000", doc)


class A10HypotheticalTests(unittest.TestCase):
    def test_hypothetical_numbers_are_treated_as_premises(self):
        from src.answer_checks import check_numbers
        result = check_numbers(
            "82.0 秒不够进 Q2",
            {"lap_last": 83.5, "lap_best": 83.1},
            "如果我能跑 82.0 秒能进 Q2 吗")
        self.assertIsNone(result)


class TtsCacheFingerprintTests(unittest.TestCase):
    def test_doubao_tts_uses_content_fingerprint(self):
        src = (ROOT / "scripts" / "tts_doubao.py").read_text(encoding="utf-8")
        self.assertIn(".fp", src)
        self.assertIn("job_fp", src)
        self.assertIn("fp_of", src)


if __name__ == "__main__":
    unittest.main()
