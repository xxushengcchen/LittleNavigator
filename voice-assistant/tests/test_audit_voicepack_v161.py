"""R11 regression tests: voice pack paths must not escape the pack directory."""
from __future__ import annotations

import pathlib
import tempfile
import unittest

from src.voicepack import VoicePack, _inside_pack

HEADER = "spmsg02\n"


class PackPathTests(unittest.TestCase):
    def test_windows_absolute_and_rooted_paths_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for bad in ("C:foo.wav", "C:\\foo.wav", "\\foo.wav",
                        "\\\\server\\share\\x.wav", "..\\x.wav", "../x.wav",
                        "sub/../../x.wav"):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        _inside_pack(root, bad)

    def test_legal_nested_path_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "sub").mkdir()
            resolved = _inside_pack(root, "sub/a.wav")
            self.assertEqual(resolved, (root / "sub" / "a.wav").resolve())

    def test_load_rejects_escaping_spmsg_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "spmsg.txt").write_text(
                HEADER + "BAD,..\\..\\windows\\system32\\x.wav,坏文件\n",
                encoding="utf-8")
            with self.assertRaises(ValueError):
                VoicePack.load(root)

    def test_load_accepts_normal_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "spmsg.txt").write_text(
                HEADER + "OK,ok.wav,正常\n", encoding="utf-8")
            pack = VoicePack.load(root)
            self.assertTrue(pack.has("OK"))
            self.assertEqual(pack.path_of(pack.variants["OK"][0]),
                             (root / "ok.wav").resolve())

    def test_device_namespace_paths_rejected(self):
        slash = chr(92)
        with tempfile.TemporaryDirectory() as tmp:
            for marker in ('?', '.'):
                path = slash * 2 + marker + slash + 'C:' + slash + 'escape.wav'
                with self.subTest(path=path), self.assertRaises(ValueError):
                    _inside_pack(pathlib.Path(tmp), path)

    def test_path_of_rechecks_a_forged_variant(self):
        from src.voicepack import Variant
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            pack = VoicePack(root)
            with self.assertRaises(ValueError):
                pack.path_of(Variant('../escape.wav', 'bad', root))

    def test_null_sentinels_remain_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            rows = ['spmsg02', 'SKIP_FILE,NULL,unused', 'SKIP_TEXT,skip.wav,NULL',
                    'OK,ok.wav,valid']
            (root / 'spmsg.txt').write_text(chr(10).join(rows), encoding='utf-8-sig')
            pack = VoicePack.load(root)
            self.assertEqual(set(pack.variants), {'OK'})

    def test_resolved_junction_cannot_escape_pack(self):
        import os
        if os.name != 'nt':
            self.skipTest('Native junction test requires Windows')
        import _winapi
        from src.voicepack import Variant
        with tempfile.TemporaryDirectory() as tmp:
            parent = pathlib.Path(tmp)
            root, outside = parent / 'pack', parent / 'outside'
            root.mkdir()
            outside.mkdir()
            (root / 'sub').mkdir()
            outlink, inlink = root / 'outlink', root / 'inlink'
            try:
                _winapi.CreateJunction(str(outside), str(outlink))
                _winapi.CreateJunction(str(root / 'sub'), str(inlink))
            except OSError as exc:
                self.skipTest('Junction capability unavailable: ' + str(exc))
            with self.assertRaises(ValueError):
                _inside_pack(root, 'outlink/escape.wav')
            with self.assertRaises(ValueError):
                VoicePack(root).path_of(Variant('outlink/escape.wav', 'bad', root))
            self.assertEqual(_inside_pack(root, 'inlink/ok.wav'),
                             (root / 'sub' / 'ok.wav').resolve())


if __name__ == "__main__":
    unittest.main()
