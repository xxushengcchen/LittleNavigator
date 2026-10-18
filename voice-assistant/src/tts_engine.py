"""语音合成：把模型生成的文字念出来。

可选层，默认关闭。未配置时行为与「只出文字」完全一致——文字照常显示，
不发声。这一点很重要：TTS 失败不该让用户失去答案。

两种适配器：
  · doubao   火山引擎豆包 v3 单向流式，NDJSON 分段返回 base64 音频
  · openai   OpenAI 兼容 `POST {base_url}/audio/speech`，直接返回二进制

为什么不 import `scripts/tts_doubao.py`：那个目录在 PyInstaller 打包产物
里不存在（spec 只收 `src/` 与 `app/`）。复制核心解析逻辑，代价是两处维护，
换来打包后能真的跑起来。
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from . import ai_constants as const

_DOUBAO_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
_DOUBAO_DONE = 20000000          # 豆包流结束码

_ADAPTERS = ("doubao", "openai", "off")

# 低于这个长度的音频当作失败：多数是服务返回了空壳或错误页
_MIN_AUDIO_BYTES = 512


class TtsError(RuntimeError):
    """语音合成失败。调用方据此降级为「只显示文字」。"""


def _post(url: str, body: dict, headers: dict, timeout: float) -> bytes:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_doubao(raw: bytes) -> bytes:
    """把 NDJSON 流里所有 base64 音频段拼起来。"""
    audio = bytearray()
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = obj.get("code")
        if code == _DOUBAO_DONE:
            break
        if code not in (0, None):
            raise TtsError(f"code={code} {obj.get('message')}")
        chunk = obj.get("data")
        if chunk:
            try:
                audio += base64.b64decode(chunk)
            except (ValueError, TypeError) as exc:
                raise TtsError(f"音频分段解码失败：{exc}") from exc
    return bytes(audio)


def fix_streaming_wav(audio: bytes) -> bytes:
    """修正流式 WAV 的占位长度字段。

    实测豆包返回的 WAV 头里 RIFF 与 data 的长度都是 0xFFFFFFFF（流式占位）。
    Python 的 `wave` 会照单全收，于是 3 万字节的音频被解析成 37 小时——
    `player.py` 按声明长度读会撑爆内存，或播出噪音。

    这里把两个长度字段改成真实值。不是 WAV 就原样返回，不猜。
    """
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    out = bytearray(audio)
    # RIFF 块长度 = 总长 - 8
    if int.from_bytes(out[4:8], "little") == 0xFFFFFFFF:
        out[4:8] = (len(out) - 8).to_bytes(4, "little")
    # 找 data chunk，修正其长度
    pos = 12
    while pos + 8 <= len(out):
        cid = bytes(out[pos:pos + 4])
        size = int.from_bytes(out[pos + 4:pos + 8], "little")
        if cid == b"data":
            if size == 0xFFFFFFFF or size > len(out) - pos - 8:
                out[pos + 4:pos + 8] = (len(out) - pos - 8).to_bytes(4, "little")
            break
        if size == 0xFFFFFFFF or size <= 0:
            break              # 结构异常，不再往下找
        pos += 8 + size + (size & 1)
    return bytes(out)


class TtsEngine:
    """一个可复用的合成器。构造时就校验适配器与凭据，避免调用时才发现没配。"""

    def __init__(self, adapter: str, config: dict):
        if adapter not in _ADAPTERS:
            raise ValueError(f"未知的 TTS 适配器：{adapter}")
        self.adapter = adapter
        self.config = dict(config or {})
        # 合成预算比 LLM 短：它在 LLM 之后跑，总预算 3.5s 里只剩 1.5s
        self.timeout_s = const.clamp(
            self.config.get("timeout_s", const.TTS_DEADLINE_HARD_MAX),
            const.TTS_DEADLINE_HARD_MAX, "ai.tts.timeout_s")

    # ------------------------------------------------------------------
    def _require(self, *keys: str) -> None:
        missing = [k for k in keys if not self.config.get(k)]
        if missing:
            raise TtsError(f"TTS 缺少配置：{', '.join(missing)}")

    def _synth_doubao(self, text: str) -> bytes:
        self._require("api_key", "speaker")
        audio_params: dict = {
            "format": self.config.get("format", "wav"),
            "sample_rate": self.config.get("sample_rate", 16000),
        }
        if self.config.get("speech_rate") is not None:
            audio_params["speech_rate"] = self.config["speech_rate"]
        req_params: dict = {
            "text": text,
            "speaker": self.config["speaker"],
            "audio_params": audio_params,
        }
        if self.config.get("instruct"):
            req_params["context_texts"] = [self.config["instruct"]]
        body = {"user": {"uid": "little-navigator"}, "req_params": req_params}
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self.config["api_key"],
            "X-Api-Resource-Id": self.config.get("resource_id", "seed-tts-2.0"),
        }
        try:
            raw = _post(_DOUBAO_ENDPOINT, body, headers, self.timeout_s)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:      # noqa: BLE001
                pass
            raise TtsError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TtsError(f"请求失败：{exc}") from exc
        return _parse_doubao(raw)

    def _synth_openai(self, text: str) -> bytes:
        self._require("base_url", "api_key")
        base = self.config["base_url"].rstrip("/")
        url = base if base.endswith("/audio/speech") else base + "/audio/speech"
        body = {
            "model": self.config.get("model") or "tts-1",
            "input": text,
            "voice": self.config.get("voice") or "alloy",
            "response_format": self.config.get("response_format") or "wav",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['api_key']}",
        }
        try:
            return _post(url, body, headers, self.timeout_s)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:      # noqa: BLE001
                pass
            raise TtsError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TtsError(f"请求失败：{exc}") from exc

    # ------------------------------------------------------------------
    def synth(self, text: str) -> bytes:
        """合成一段文字，返回音频字节。任何失败都抛 TtsError。"""
        if self.adapter == "off":
            raise TtsError("语音合成未启用")
        if not (text or "").strip():
            raise TtsError("文本为空")
        audio = (self._synth_doubao(text) if self.adapter == "doubao"
                 else self._synth_openai(text))
        if len(audio) < _MIN_AUDIO_BYTES:
            raise TtsError(f"音频过短（{len(audio)} 字节），判定为失败")
        return fix_streaming_wav(audio)

    def test(self) -> dict:
        """界面「测试合成」：合成一句短话，报告耗时与结果。"""
        import time

        started = time.monotonic()
        try:
            audio = self.synth("测试语音合成。")
            return {"ok": True, "bytes": len(audio),
                    "latency_ms": round((time.monotonic() - started) * 1000)}
        except Exception as exc:       # noqa: BLE001
            return {"ok": False,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "error": str(exc)[:200]}
