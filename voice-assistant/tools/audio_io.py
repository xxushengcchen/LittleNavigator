"""音频读写小工具：只依赖标准库 + numpy，够测试用。"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """读 16-bit PCM wav → (float32[-1,1], sample_rate)。"""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"只支持 16-bit PCM，拿到 {width * 8}-bit：{path}")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        data = data.reshape(-1, 2).mean(axis=1).astype(np.float32)
    return data, sr


def write_wav(path: str | Path, samples: np.ndarray, sr: int) -> None:
    """写 16-bit PCM wav。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    clip = np.clip(samples, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((clip * 32767.0).astype(np.int16).tobytes())


def resample(samples: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """线性插值重采样，测试用精度足够。"""
    if sr_in == sr_out or len(samples) == 0:
        return samples.astype(np.float32)
    n_out = int(round(len(samples) * sr_out / sr_in))
    x_old = np.arange(len(samples), dtype=np.float64)
    x_new = np.linspace(0.0, len(samples) - 1.0, n_out)
    return np.interp(x_new, x_old, samples.astype(np.float64)).astype(np.float32)
