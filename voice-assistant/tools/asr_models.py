"""三个候选本地 ASR 模型的统一封装（模型对比测试用）。

候选：
  · sense-voice     SenseVoice int8，非流式，中英日韩粤，带 ITN
  · zipformer-ctc   Zipformer CTC 中文 int8，非流式（aishell 1.74%）
  · streaming-zip   流式 Zipformer 中文 int8，模拟边录边解码

统一口径：transcribe() 的 decode_sec 记的是「音频全部喂完后 → 拿到最终文本」
的耗时，也就是 PTT 松键到出字的延迟。
"""
from __future__ import annotations

import time
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Type

import numpy as np
import sherpa_onnx

from .audio_io import resample

SAMPLE_RATE = 16000


def _models_root() -> Path:
    """模型目录：打包后 = exe 旁边的 models/；源码运行时 = 项目根的 models/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "models"
    return Path(__file__).resolve().parents[2] / "models"


MODELS_ROOT = _models_root()


@dataclass
class AsrResult:
    text: str
    decode_sec: float          # 松键 → 出字
    audio_sec: float
    chunks: int = 1            # 流式模型喂了多少个 chunk


class BaseAsr:
    key = "base"
    label = "base"
    streaming = False

    def __init__(self, num_threads: int = 4, models_root=None):
        self.num_threads = num_threads
        self.models_root = Path(models_root) if models_root else MODELS_ROOT
        t0 = time.perf_counter()
        self._build()
        self.load_sec = time.perf_counter() - t0

    # -- 子类实现 ---------------------------------------------------------
    def _build(self) -> None:
        raise NotImplementedError

    def transcribe(self, samples: np.ndarray, sr: int = SAMPLE_RATE) -> AsrResult:
        raise NotImplementedError

    # -- 公共 -------------------------------------------------------------
    def warmup(self) -> None:
        self.transcribe(np.zeros(int(0.5 * SAMPLE_RATE), dtype=np.float32))

    @staticmethod
    def _check(directory, *names):
        missing = [str(directory / name) for name in names if not (directory / name).is_file()]
        if missing:
            raise FileNotFoundError("识别模型不完整：" + "、".join(missing))

    def _prep(self, samples: np.ndarray, sr: int) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32)
        if sr != SAMPLE_RATE:
            x = resample(x, sr, SAMPLE_RATE)
        return np.ascontiguousarray(x)


class SenseVoiceAsr(BaseAsr):
    key = "sense-voice"
    label = "SenseVoice int8"

    def _build(self) -> None:
        d = self.models_root / "sense-voice"
        self._check(d, "model.int8.onnx", "tokens.txt")
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=self.num_threads,
            language="zh",
            use_itn=True,
            debug=False,
        )

    def transcribe(self, samples: np.ndarray, sr: int = SAMPLE_RATE) -> AsrResult:
        x = self._prep(samples, sr)
        audio_sec = len(x) / SAMPLE_RATE
        t0 = time.perf_counter()
        s = self.rec.create_stream()
        s.accept_waveform(SAMPLE_RATE, x)
        self.rec.decode_stream(s)
        text = s.result.text
        return AsrResult(text.strip(), time.perf_counter() - t0, audio_sec)


class ZipformerCtcAsr(BaseAsr):
    key = "zipformer-ctc"
    label = "Zipformer-CTC 中文 int8"

    def _build(self) -> None:
        d = self.models_root / "zipformer-ctc-zh"
        self._check(d, "model.int8.onnx", "tokens.txt")
        self.rec = sherpa_onnx.OfflineRecognizer.from_zipformer_ctc(
            model=str(d / "model.int8.onnx"),
            tokens=str(d / "tokens.txt"),
            num_threads=self.num_threads,
            debug=False,
        )

    def transcribe(self, samples: np.ndarray, sr: int = SAMPLE_RATE) -> AsrResult:
        x = self._prep(samples, sr)
        audio_sec = len(x) / SAMPLE_RATE
        t0 = time.perf_counter()
        s = self.rec.create_stream()
        s.accept_waveform(SAMPLE_RATE, x)
        self.rec.decode_stream(s)
        text = s.result.text
        return AsrResult(text.strip(), time.perf_counter() - t0, audio_sec)


class StreamingZipformerAsr(BaseAsr):
    """流式 zipformer：按 chunk 喂音频，记录松键后收尾耗时。"""

    key = "streaming-zip"
    label = "流式 Zipformer 中文 int8"
    streaming = True
    chunk_sec = 0.32

    def _build(self) -> None:
        d = self.models_root / "streaming-zipformer-zh"
        self._check(d, "encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt")
        self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"),
            encoder=str(d / "encoder.int8.onnx"),
            decoder=str(d / "decoder.onnx"),
            joiner=str(d / "joiner.int8.onnx"),
            num_threads=self.num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=False,
        )

    def transcribe(self, samples: np.ndarray, sr: int = SAMPLE_RATE) -> AsrResult:
        x = self._prep(samples, sr)
        audio_sec = len(x) / SAMPLE_RATE
        stream = self.rec.create_stream()
        n = max(1, int(self.chunk_sec * SAMPLE_RATE))
        chunks = 0
        for i in range(0, len(x), n):
            stream.accept_waveform(SAMPLE_RATE, x[i:i + n])
            while self.rec.is_ready(stream):
                self.rec.decode_stream(stream)
            chunks += 1
        stream.input_finished()
        t0 = time.perf_counter()
        while self.rec.is_ready(stream):
            self.rec.decode_stream(stream)
        text = self.rec.get_result(stream)
        return AsrResult(text.strip(), time.perf_counter() - t0, audio_sec, chunks)


REGISTRY: dict[str, Type[BaseAsr]] = {
    SenseVoiceAsr.key: SenseVoiceAsr,
    ZipformerCtcAsr.key: ZipformerCtcAsr,
    StreamingZipformerAsr.key: StreamingZipformerAsr,
}


def load(name: str, num_threads: int = 4, models_root=None) -> BaseAsr:
    cls = REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"未知模型 {name}，可选：{list(REGISTRY)}")
    return cls(num_threads=num_threads, models_root=models_root)
