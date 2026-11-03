"""R06 regression: listener/engine import without sounddevice (subprocess based).

Reloading modules that pull in numpy is not reliable in-process, so each case
runs in a fresh interpreter where ``sounddevice`` is blocked.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

VOICE = pathlib.Path(__file__).resolve().parents[1]


def run(code: str) -> subprocess.CompletedProcess:
    prelude = ("import sys\n"
               "sys.modules['sounddevice'] = None\n")
    return subprocess.run([sys.executable, "-B", "-c", prelude + code],
                          cwd=str(VOICE), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


class MissingPortAudioTests(unittest.TestCase):
    def test_listener_imports_and_reports_structured_error(self):
        result = run(
            "import src.listener as L\n"
            "print('import-ok', hex(L.resolve_vk('SPACE')))\n"
            "try:\n"
            "    L._sounddevice()\n"
            "except L.AudioBackendError as exc:\n"
            "    print('structured', 'PortAudio' in str(exc))\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("import-ok 0x20", result.stdout)
        self.assertIn("structured True", result.stdout)

    def test_engine_imports_without_audio_backend(self):
        result = run(
            "import src.engine as E\n"
            "print('engine-ok', hasattr(E, 'Assistant'))\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("engine-ok True", result.stdout)

    def test_player_falls_back_to_winsound_when_sounddevice_missing(self):
        result = run(
            "import src.player as P\n"
            "p = P.Player(enabled=True)\n"
            "print('backend', p.backend)\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("backend", result.stdout)


if __name__ == "__main__":
    unittest.main()
