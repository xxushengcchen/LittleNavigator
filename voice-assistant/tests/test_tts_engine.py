import base64
import json
import unittest
from unittest.mock import patch

from src.tts_engine import TtsEngine, TtsError, _parse_doubao


class AdapterSelectTests(unittest.TestCase):
    def test_off_raises_on_synth(self):
        e = TtsEngine("off", {})
        with self.assertRaises(TtsError):
            e.synth("测试")

    def test_unknown_adapter_raises(self):
        with self.assertRaises(ValueError):
            TtsEngine("nope", {})

    def test_missing_credentials_raises(self):
        e = TtsEngine("openai", {"base_url": "http://x"})
        with self.assertRaises(TtsError):
            e.synth("测试")

    def test_deadline_is_clamped(self):
        from src import ai_constants as const
        e = TtsEngine("openai", {"timeout_s": 99})
        # 用常量断言：v1.5.1 把 TTS 硬上限从 1.5s 放宽到 2.5s。
        self.assertEqual(e.timeout_s, const.TTS_DEADLINE_HARD_MAX)

    def test_smaller_deadline_kept(self):
        e = TtsEngine("openai", {"timeout_s": 0.9})
        self.assertEqual(e.timeout_s, 0.9)


class EmptyTextTests(unittest.TestCase):
    def test_blank_text_raises(self):
        e = TtsEngine("openai", {"base_url": "http://x", "api_key": "k"})
        with self.assertRaises(TtsError):
            e.synth("   ")


class ShortAudioTests(unittest.TestCase):
    def test_tiny_response_is_treated_as_failure(self):
        e = TtsEngine("openai", {"base_url": "http://x", "api_key": "k"})
        with patch("src.tts_engine._post", return_value=b"tiny"):
            with self.assertRaises(TtsError):
                e.synth("测试")

    def test_reasonable_audio_passes(self):
        e = TtsEngine("openai", {"base_url": "http://x", "api_key": "k"})
        with patch("src.tts_engine._post", return_value=b"x" * 2048):
            self.assertEqual(len(e.synth("测试")), 2048)


class DoubaoParseTests(unittest.TestCase):
    def _line(self, code, data=None, message=None):
        obj = {"code": code}
        if data is not None:
            obj["data"] = base64.b64encode(data).decode()
        if message is not None:
            obj["message"] = message
        return json.dumps(obj).encode()

    def test_concatenates_chunks_until_done(self):
        raw = b"\n".join([
            self._line(0, b"AAA"),
            self._line(0, b"BBB"),
            self._line(20000000),
        ])
        self.assertEqual(_parse_doubao(raw), b"AAABBB")

    def test_error_code_raises(self):
        raw = self._line(55000000, message="resource mismatch")
        with self.assertRaises(TtsError):
            _parse_doubao(raw)

    def test_ignores_blank_lines_and_garbage(self):
        raw = b"\n\nnot json\n" + self._line(0, b"X") + b"\n"
        self.assertEqual(_parse_doubao(raw), b"X")

    def test_empty_stream_returns_empty(self):
        self.assertEqual(_parse_doubao(b""), b"")


class DoubaoSynthTests(unittest.TestCase):
    def test_requires_speaker(self):
        e = TtsEngine("doubao", {"api_key": "k"})
        with self.assertRaises(TtsError):
            e.synth("测试")

    def test_sends_resource_header(self):
        captured = {}

        def fake_post(url, body, headers, timeout):
            captured.update({"url": url, "headers": headers, "body": body})
            return json.dumps({"code": 0,
                               "data": base64.b64encode(b"z" * 1024).decode()}).encode()

        e = TtsEngine("doubao", {"api_key": "k", "speaker": "zh_female",
                                 "resource_id": "seed-tts-2.0"})
        with patch("src.tts_engine._post", side_effect=fake_post):
            e.synth("你好")
        self.assertEqual(captured["headers"]["X-Api-Key"], "k")
        self.assertEqual(captured["headers"]["X-Api-Resource-Id"], "seed-tts-2.0")
        self.assertEqual(captured["body"]["req_params"]["speaker"], "zh_female")


class OpenAiSynthTests(unittest.TestCase):
    def test_endpoint_is_built_once(self):
        captured = {}

        def fake_post(url, body, headers, timeout):
            captured["url"] = url
            return b"y" * 1024

        e = TtsEngine("openai", {"base_url": "http://x/v1", "api_key": "k"})
        with patch("src.tts_engine._post", side_effect=fake_post):
            e.synth("你好")
        self.assertEqual(captured["url"], "http://x/v1/audio/speech")

    def test_explicit_speech_endpoint_is_kept(self):
        captured = {}

        def fake_post(url, body, headers, timeout):
            captured["url"] = url
            return b"y" * 1024

        e = TtsEngine("openai", {"base_url": "http://x/v1/audio/speech",
                                 "api_key": "k"})
        with patch("src.tts_engine._post", side_effect=fake_post):
            e.synth("你好")
        self.assertEqual(captured["url"], "http://x/v1/audio/speech")


class TestButtonTests(unittest.TestCase):
    def test_success_reports_bytes_and_latency(self):
        e = TtsEngine("openai", {"base_url": "http://x", "api_key": "k"})
        with patch("src.tts_engine._post", return_value=b"z" * 2048):
            out = e.test()
        self.assertTrue(out["ok"])
        self.assertEqual(out["bytes"], 2048)
        self.assertIn("latency_ms", out)

    def test_failure_is_reported_not_raised(self):
        e = TtsEngine("openai", {"base_url": "http://x", "api_key": "k"})
        with patch("src.tts_engine._post", side_effect=OSError("boom")):
            out = e.test()
        self.assertFalse(out["ok"])
        self.assertIn("error", out)


if __name__ == '__main__':
    unittest.main()


class StreamingWavFixTests(unittest.TestCase):
    """豆包返回的 WAV 长度字段是 0xFFFFFFFF 占位（流式），必须修。

    实测不修的话：3 万字节的音频会被 wave 解析成 37 小时，
    player.py 按声明长度读会撑爆内存或播出噪音。
    """

    def _wav_with_placeholder(self, pcm: bytes, rate: int = 16000) -> bytes:
        import struct
        hdr = bytearray()
        hdr += b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
        hdr += b"fmt " + struct.pack("<I", 16) + struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
        hdr += b"data" + struct.pack("<I", 0xFFFFFFFF)
        return bytes(hdr) + pcm

    def test_placeholder_is_corrected(self):
        import io
        import wave
        from src.tts_engine import fix_streaming_wav
        pcm = b"\x00\x10" * 16000          # 1 秒
        fixed = fix_streaming_wav(self._wav_with_placeholder(pcm))
        with wave.open(io.BytesIO(fixed), "rb") as w:
            self.assertEqual(w.getnframes(), 16000)
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 1.0, places=2)

    def test_riff_size_is_corrected(self):
        from src.tts_engine import fix_streaming_wav
        pcm = b"\x00\x10" * 100
        fixed = fix_streaming_wav(self._wav_with_placeholder(pcm))
        import struct
        self.assertEqual(struct.unpack("<I", fixed[4:8])[0], len(fixed) - 8)

    def test_valid_wav_is_untouched(self):
        import io
        import wave
        from src.tts_engine import fix_streaming_wav
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\x00\x10" * 1000)
        original = buf.getvalue()
        self.assertEqual(fix_streaming_wav(original), original)

    def test_non_wav_is_untouched(self):
        from src.tts_engine import fix_streaming_wav
        mp3ish = b"ID3\x03\x00" + b"\x00" * 100
        self.assertEqual(fix_streaming_wav(mp3ish), mp3ish)

    def test_short_blob_is_untouched(self):
        from src.tts_engine import fix_streaming_wav
        self.assertEqual(fix_streaming_wav(b"RIFF"), b"RIFF")

    def test_synth_returns_corrected_audio(self):
        import base64
        import io
        import json
        import wave
        from src.tts_engine import TtsEngine
        pcm = b"\x00\x10" * 16000
        wav = self._wav_with_placeholder(pcm)
        line = json.dumps({"code": 0,
                           "data": base64.b64encode(wav).decode()}).encode()
        e = TtsEngine("doubao", {"api_key": "k", "speaker": "s"})
        with patch("src.tts_engine._post", return_value=line):
            audio = e.synth("测试")
        with wave.open(io.BytesIO(audio), "rb") as w:
            self.assertEqual(w.getnframes(), 16000)
