import io
import unittest
from unittest.mock import MagicMock, patch

from src.listener import PttListener


def cfg(**ptt):
    base = {"ptt": {"vk": 0xA3, "poll_ms": 10, "min_hold_ms": 250,
                    "max_hold_s": 8}}
    base["ptt"].update(ptt)
    return base


def listener(**kw):
    return PttListener(MagicMock(), cfg(), lambda *a: None,
                       out=io.StringIO(), **kw)


class AnalysisKeyConstructionTests(unittest.TestCase):
    def test_analysis_key_defaults_to_none(self):
        l = listener()
        self.assertIsNone(l.analysis_vk)

    def test_analysis_vk_is_stored(self):
        l = listener(analysis_vk=0xA5)
        self.assertEqual(l.analysis_vk, 0xA5)

    def test_same_key_as_ptt_is_refused(self):
        # 同一个键绑两用会让每次提问都变成分析请求，必须挡掉
        l = listener(analysis_vk=0xA3)
        self.assertIsNone(l.analysis_vk)

    def test_analysis_callback_defaults_to_on_result(self):
        l = listener(analysis_vk=0xA5)
        self.assertIs(l.on_analysis_result, l.on_result)

    def test_explicit_callback_is_kept(self):
        cb = lambda *a: None       # noqa: E731
        l = listener(analysis_vk=0xA5, on_analysis_result=cb)
        self.assertIs(l.on_analysis_result, cb)

    def test_pending_source_starts_as_ptt(self):
        self.assertEqual(listener().pending_source, "ptt")

    def test_named_vk_is_resolved(self):
        l = listener(analysis_vk="MOUSE5")
        self.assertIsNotNone(l.analysis_vk)


class AnalysisDownTests(unittest.TestCase):
    def test_analysis_down_false_when_unbound(self):
        l = listener()
        self.assertFalse(l._analysis_down())

    def test_analysis_down_uses_key_state(self):
        l = listener(analysis_vk=0xA5)
        with patch("src.listener._key_down", return_value=True):
            self.assertTrue(l._analysis_down())
        with patch("src.listener._key_down", return_value=False):
            self.assertFalse(l._analysis_down())


class ProcessCallbackTests(unittest.TestCase):
    def _make(self, source: str):
        got: list[dict] = []
        ptt_cb = lambda text, meta: got.append({"who": "ptt", **meta})   # noqa: E731
        ana_cb = lambda text, meta: got.append({"who": "analysis", **meta})  # noqa: E731
        l = PttListener(MagicMock(), cfg(), ptt_cb, out=io.StringIO(),
                        analysis_vk=0xA5, on_analysis_result=ana_cb)
        l.asr = MagicMock()
        l.asr.transcribe.return_value = MagicMock(text="测试问题")
        l.asr.key = "fake"
        l.pending_source = source
        return l, got

    def _run_process(self, l, got):
        import numpy as np
        audio = np.zeros(int(0.5 * l.sr), dtype="float32") + 0.1
        with patch("src.listener._trim_silence", side_effect=lambda x, sr: x):
            l._process(audio)
        return got

    def test_ptt_source_hits_on_result(self):
        l, got = self._make("ptt")
        self._run_process(l, got)
        self.assertEqual(got[0]["who"], "ptt")

    def test_analysis_source_hits_analysis_callback(self):
        l, got = self._make("analysis")
        self._run_process(l, got)
        self.assertEqual(got[0]["who"], "analysis")

    def test_meta_carries_source(self):
        l, got = self._make("analysis")
        self._run_process(l, got)
        self.assertEqual(got[0]["source"], "analysis")


if __name__ == '__main__':
    unittest.main()
