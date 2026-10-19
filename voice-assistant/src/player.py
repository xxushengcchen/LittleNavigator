"""播一个 wav：可指定输出设备、可调音量、播报时压低游戏音量。

默认后端 sounddevice（能选设备、能控音量）；环境缺 PortAudio 时退回
Windows 自带的 winsound（只能播到系统默认设备，零依赖兜底）。
"""
from __future__ import annotations

import io
import pathlib
import sys
import time
import wave

import numpy as np


def _read_wav(path: pathlib.Path) -> tuple[np.ndarray, int]:
    """读 16-bit PCM wav → (float32 [-1,1], 采样率)。"""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    else:
        raise ValueError(f"只支持 8/16-bit PCM：{path}")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.float32)
    return data, sr


def _read_wav_bytes(blob: bytes) -> tuple[np.ndarray, int]:
    """从内存里的 WAV 字节读出 (float32, 采样率)。

    TTS 返回的就是内存字节，没必要先落盘再读回来。
    """
    with wave.open(io.BytesIO(blob), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    else:
        raise ValueError("只支持 8/16-bit PCM")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.float32)
    return data, sr


class Player:
    def __init__(self, enabled: bool = True, device=None, volume: float = 1.0,
                 ducker=None, out=sys.stdout):
        self.enabled = enabled
        self.device = device
        self.volume = float(volume)
        self.ducker = ducker
        self.out = out
        self._backend = "none"
        self._sd = None
        self._winsound = None
        # 即使启动时禁播也初始化后端，以便界面开关可以热生效。
        try:
            import sounddevice as sd
            self._sd = sd
            self._backend = "sounddevice"
        except Exception:
            try:
                import winsound
                self._winsound = winsound
                self._backend = "winsound"
            except Exception:
                self.enabled = False

    @property
    def backend(self) -> str:
        if not self.enabled:
            return "dry-run"
        if self._backend == "sounddevice":
            extra = "" if self.device is None else f" dev={self.device}"
            return f"sounddevice{extra}"
        if self._backend == "winsound":
            return "winsound"
        return "none"

    # ------------------------------------------------------------------
    def play(self, path: str | pathlib.Path) -> float:
        """播一个 wav，返回墙钟耗时（秒）。同步播完才返回。"""
        p = pathlib.Path(path)
        t0 = time.perf_counter()
        if not self.enabled:
            return 0.0
        if not p.exists():
            raise FileNotFoundError(f"音频不存在：{p}")

        if self.ducker is not None:
            self.ducker.duck()
        try:
            if self._sd is not None:
                x, sr = _read_wav(p)
                if self.volume != 1.0:
                    x = x * self.volume
                self._sd.play(x, sr, device=self.device, blocking=True)
            elif self._winsound is not None:
                self._winsound.PlaySound(str(p), self._winsound.SND_FILENAME)
        finally:
            if self.ducker is not None:
                self.ducker.restore()
        return time.perf_counter() - t0

    def play_bytes(self, blob: bytes) -> float:
        """播内存里的 WAV 字节，返回墙钟耗时（秒）。

        TTS 合成结果是字节，直接播；不要为了复用 play() 先落盘——
        落盘会带来清理负担，也会在只读目录（Program Files 安装）下失败。
        """
        t0 = time.perf_counter()
        if not self.enabled or not blob:
            return 0.0
        if self.ducker is not None:
            self.ducker.duck()
        try:
            if self._sd is not None:
                x, sr = _read_wav_bytes(blob)
                if self.volume != 1.0:
                    x = x * self.volume
                self._sd.play(x, sr, device=self.device, blocking=True)
            elif self._winsound is not None:
                # winsound 只认文件；临时文件是这个后端的唯一出路
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
                    fh.write(blob)
                    tmp = fh.name
                try:
                    self._winsound.PlaySound(tmp, self._winsound.SND_FILENAME)
                finally:
                    try:
                        pathlib.Path(tmp).unlink()
                    except OSError:
                        pass
        finally:
            if self.ducker is not None:
                self.ducker.restore()
        return time.perf_counter() - t0

    def play_many(self, paths, gap_ms=60, on_start=None):
        """整句一次提交，避免片段之间反复打开设备和恢复游戏音量。"""
        if not self.enabled:
            return 0.0
        if self._sd is None:
            return sum(self.play(p) for p in paths)
        parts = []
        rate = None
        for path in paths:
            data, sr = _read_wav(pathlib.Path(path))
            if rate is None:
                rate = sr
            if sr != rate:
                count = round(len(data) * rate / sr)
                data = np.interp(np.arange(count) * sr / rate, np.arange(len(data)), data).astype(np.float32)
            if parts and gap_ms:
                parts.append(np.zeros(round(rate * gap_ms / 1000), dtype=np.float32))
            parts.append(data)
        data = np.clip(np.concatenate(parts) * self.volume, -1, 1)
        started = time.perf_counter()
        if self.ducker:
            self.ducker.duck()
        try:
            if on_start:
                on_start()
            self._sd.play(data, rate, device=self.device, blocking=True)
        finally:
            if self.ducker:
                self.ducker.restore()
        return time.perf_counter() - started

    def stop(self) -> None:
        if self._sd is not None:
            self._sd.stop()
        if self._winsound is not None:
            self._winsound.PlaySound(None, self._winsound.SND_PURGE)
        if self.ducker is not None:
            self.ducker.restore()
