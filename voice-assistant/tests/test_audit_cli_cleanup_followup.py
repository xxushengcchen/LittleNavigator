"""R13 regression: every CLI exit path closes Assistant exactly once."""
from __future__ import annotations

import unittest
from unittest import mock

from src import console, voice_app


class FakeAssistant:
    def __init__(self, *, boom=None, close_boom=None):
        self.tel_warn = None
        self.tel = object()
        self.player = mock.Mock(backend="dry-run")
        self.history = []
        self.qa_log = "qa.jsonl"
        self.closed = 0
        self.boom = boom
        self.close_boom = close_boom

    def info_lines(self):
        if self.boom == "init":
            raise RuntimeError("boom during init")
        return []

    def ask(self, *a, **k):
        if self.boom == "ask":
            raise RuntimeError("boom during ask")
        return None

    def snapshot(self):
        return mock.Mock()

    def stats(self):
        return {}

    def close(self):
        self.closed += 1
        if self.close_boom is not None:
            raise self.close_boom


def _run_console(argv, fake):
    with mock.patch.object(console, "Assistant", return_value=fake), \
         mock.patch.object(console.cfg_mod, "load", return_value={"telemetry": {"provider": "sim"},
                                                                "ptt": {}}), \
         mock.patch("builtins.print"):
        return console.main(argv)


class ConsoleCleanupTests(unittest.TestCase):
    def test_once_path_closes_exactly_once(self):
        fake = FakeAssistant()
        self.assertEqual(_run_console(["--once", "油还够吗"], fake), 0)
        self.assertEqual(fake.closed, 1)

    def test_exception_in_ask_still_closes(self):
        fake = FakeAssistant(boom="ask")
        with self.assertRaises(RuntimeError):
            _run_console(["--once", "油还够吗"], fake)
        self.assertEqual(fake.closed, 1, "异常路径也必须 close")

    def test_close_failure_does_not_mask_original_error(self):
        fake = FakeAssistant(boom="ask", close_boom=ValueError("close broke"))
        with self.assertRaises(RuntimeError):
            _run_console(["--once", "油还够吗"], fake)
        self.assertEqual(fake.closed, 1)


class VoiceAppCleanupTests(unittest.TestCase):
    def test_failure_after_construction_closes_assistant(self):
        fake = FakeAssistant(boom="init")
        with mock.patch.object(voice_app, "Assistant", return_value=fake), \
             mock.patch.object(voice_app.cfg_mod, "load",
                               return_value={"telemetry": {"provider": "sim"},
                                             "ptt": {}, "asr": {}}), \
             mock.patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                voice_app.main([])
        self.assertEqual(fake.closed, 1, "构造之后任何早退/异常都必须 close")


if __name__ == "__main__":
    unittest.main()
