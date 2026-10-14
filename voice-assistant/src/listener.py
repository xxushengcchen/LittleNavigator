"""PTT 语音输入：按住键说话 → 松键 → 出文本。

键位检测用 Windows 的 GetAsyncKeyState 轮询（全局生效，iRacing 全屏也有用），
录音用 sounddevice，16 kHz 单声道。

防回环（对应 config 的 audio_guard）：
  · 只在按住 PTT 期间开麦，松键立刻关流
  · 播报/处理期间按 PTT 一律忽略（is_busy 回调）
  · 录到的音频过短（< min_hold_ms）直接丢弃，当作误触
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

VK_NAMES = {
    "RCONTROL": 0xA3, "LCONTROL": 0xA2, "RSHIFT": 0xA1, "LSHIFT": 0xA0,
    "RALT": 0xA5, "LALT": 0xA4, "CAPSLOCK": 0x14, "TAB": 0x09,
    "SPACE": 0x20, "MOUSE4": 0x05, "MOUSE5": 0x06,
}


def resolve_vk(vk: int | str) -> int:
    if isinstance(vk, int):
        return vk
    s = str(vk).strip().upper()
    if s in VK_NAMES:
        return VK_NAMES[s]
    if s.startswith("0X"):
        return int(s, 16)
    return int(s)


def resolve_device(d) -> int | str | None:
    """设备号允许写成 "1" 这种字符串；None 表示系统默认。"""
    if d is None or d == "":
        return None
    if isinstance(d, int):
        return d
    s = str(d).strip()
    return int(s) if s.isdigit() else s


def input_format(device):
    """优先 16 kHz；WASAPI 不支持时用设备原生采样率，识别前再重采样。"""
    device = resolve_device(device)
    info = sd.query_devices(device, kind="input")
    for rate in dict.fromkeys((16000, int(info['default_samplerate']))):
        for channels in dict.fromkeys((1, min(2, info['max_input_channels']))):
            if channels < 1:
                continue
            try:
                sd.check_input_settings(device=device, channels=channels, samplerate=rate, dtype="float32")
                return rate, channels
            except sd.PortAudioError:
                continue
    raise ValueError("麦克风没有可用的录音格式")


def check_input_device(device) -> str | None:
    """开 0.3 秒试录一段，返回错误描述；正常返回 None。

    有些虚拟声卡（如 VB-Audio Point）会打开成功但回吐垃圾数据
    （超大浮点数），必须在开工前就把它揪出来。
    """
    try:
        chunks = []
        rate, channels = input_format(device)
        with sd.InputStream(samplerate=rate, channels=channels, dtype="float32",
                            device=resolve_device(device),
                            callback=lambda data, *_: chunks.append(data.copy())):
            sd.sleep(300)
        rec = np.concatenate(chunks) if chunks else np.zeros(0)
    except Exception as exc:  # noqa: BLE001
        return f"麦克风打不开：{exc}。请检查设备连接、Windows 麦克风权限及独占占用"
    a = np.asarray(rec).reshape(-1)
    if not len(a):
        return "输入设备未返回音频数据"
    if not np.all(np.isfinite(a)) or float(np.max(np.abs(a))) > 8.0:
        return ("这个输入设备回吐的数据不正常（虚拟声卡常见），"
                "换一个设备：python -m src.voice_app --list-devices")
    return None


def _key_down(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


_VK_BY_CODE = {v: k for k, v in VK_NAMES.items()}

# 撷取时扫描的候选键：常见键位集合，避免全表 256 键瞎扫
_CAPTURE_CANDIDATES: list[int] = (
    list(range(0x01, 0x07)) +            # 鼠标左右中 + 侧键
    list(range(0x08, 0x0E)) +            # 退格/回车等
    list(range(0x10, 0x13)) + [0x14, 0x20, 0x1B, 0x09] +
    list(range(0x25, 0x29)) +            # 方向键
    list(range(0x30, 0x3A)) +            # 数字
    list(range(0x41, 0x5B)) +            # 字母
    list(range(0x60, 0x70)) +            # 小键盘
    list(range(0x70, 0x88)) +            # F1–F24
    list(range(0xA0, 0xA6)) +            # 左右 Ctrl/Shift/Alt
    list(range(0xBA, 0xC1)) + list(range(0xDB, 0xDF))
)


def vk_name(vk: int) -> str:
    return _VK_BY_CODE.get(vk, f"0x{vk:02X}")


def capture_key(timeout_s: float = 6.0, poll_s: float = 0.02) -> dict | None:
    """等下一个按键按下，返回 {vk, name}。超时返回 None。

    常用于界面上的「按下撷取」：用户按一下想当 PTT 的键。
    """
    t0 = time.perf_counter()
    # 不把点击「采集」时仍按住的鼠标键当 PTT；优先区分左右修饰键。
    candidates = [v for v in _CAPTURE_CANDIDATES if v not in (0x10, 0x11, 0x12)]
    held = {v for v in candidates if _key_down(v)}
    while time.perf_counter() - t0 < timeout_s:
        held = {v for v in held if _key_down(v)}
        for vk in candidates:
            if vk in held:
                continue
            if _key_down(vk):
                # 等它松开，避免把「按住不放」算成连击
                while _key_down(vk) and time.perf_counter() - t0 < timeout_s + 2:
                    time.sleep(poll_s)
                return {"vk": vk, "name": vk_name(vk)}
        time.sleep(poll_s)
    return None


def _trim_silence(x: np.ndarray, sr: int = 16000, frame_ms: int = 20,
                  keep_ms: int = 60) -> np.ndarray:
    """掐掉首尾的静音，留一点余量。全静音就原样返回。"""
    n = max(1, sr * frame_ms // 1000)
    frames = [x[i:i + n] for i in range(0, len(x) - n + 1, n)]
    if not frames:
        return x
    rms = np.array([float(np.sqrt(np.mean(f ** 2))) for f in frames])
    thr = max(rms.max() * 0.06, 0.004)
    loud = np.where(rms > thr)[0]
    if len(loud) == 0:
        return x
    keep = max(1, keep_ms // frame_ms)
    a = max(0, loud[0] - keep) * n
    b = min(len(x), (loud[-1] + keep + 1) * n)
    return x[a:b]


class PttListener:
    """常驻轮询线程；每次「按下→松开」产出一条识别结果。"""

    def __init__(self, asr, cfg: dict, on_result: Callable[[str, dict], None],
                 is_busy: Callable[[], bool] | None = None, out=sys.stdout):
        ptt = cfg.get("ptt", {})
        self.asr = asr
        self.on_result = on_result
        self.is_busy = is_busy or (lambda: False)
        self.out = out
        self.vk = resolve_vk(ptt.get("vk", 0xA3))
        self.binding=ptt.get('binding')
        self.wheel=None
        if self.binding:
            from .wheel import get_input
            self.wheel=get_input()
        self.sr = 16000
        self.min_hold_s = ptt.get("min_hold_ms", 250) / 1000.0
        self.max_hold_s = ptt.get("max_hold_s", 8.0)
        self.device = resolve_device(ptt.get("input_device"))
        self.poll_s = ptt.get("poll_ms", 10) / 1000.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture_lock = threading.Lock()
        self.error = None
        self.stats = {"segments": 0, "ignored_busy": 0, "too_short": 0, "errors": 0}
        self.state = "idle"          # idle | recording | thinking
        self.last: dict | None = None

    # ------------------------------------------------------------------
    def _down(self):
        return self.wheel.pressed(self.binding) if self.wheel is not None else _key_down(self.vk)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ptt-listener", daemon=True)
        self._thread.start()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> bool:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        return not self.running

    # ------------------------------------------------------------------
    def _record_while(self, released: Callable[[], bool]) -> np.ndarray:
        chunks: list[np.ndarray] = []
        err: list[Exception] = []

        def cb(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                err.append(RuntimeError(f"录音数据丢失：{status}"))
            chunks.append(indata.mean(axis=1).astype(np.float32))

        t0 = time.perf_counter()
        rate, channels = input_format(self.device)
        with sd.InputStream(samplerate=rate, channels=channels, dtype="float32",
                            blocksize=512, device=self.device, callback=cb):
            while True:
                sd.sleep(int(self.poll_s * 1000))
                if self._stop.is_set() or self.is_busy() or err or released() or time.perf_counter() - t0 > self.max_hold_s:
                    break
        if err:
            raise err[0]
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        if len(audio) and rate != self.sr:
            from tools.audio_io import resample
            audio = resample(audio, rate, self.sr)
        return audio

    def record_for(self, seconds):
        """界面测试直接录音，不向游戏注入系统按键。"""
        if not self._capture_lock.acquire(blocking=False):
            return {"error": "PTT 正忙"}
        try:
            if self.is_busy() or self._stop.is_set():
                return {"error": "当前正在播报或监听已停止"}
            self.state = "recording"
            deadline = time.monotonic() + seconds
            audio = self._record_while(lambda: time.monotonic() >= deadline)
            if not self._stop.is_set():
                self._process(audio)
            return {"running": self.running, "simulated_seconds": seconds}
        finally:
            self.state = "idle"
            self._capture_lock.release()

    def _process(self, audio):
        if self._stop.is_set() or self.is_busy():
            return
        if len(audio) < int(self.min_hold_s * self.sr):
            self.stats["too_short"] += 1
            return
        if not np.all(np.isfinite(audio)) or float(np.max(np.abs(audio))) > 8:
            raise ValueError("麦克风返回无效音频")
        self.stats["segments"] += 1
        audio = _trim_silence(audio, self.sr)
        started = time.perf_counter()
        self.state = "thinking"
        res = self.asr.transcribe(audio, self.sr)
        if self._stop.is_set():
            return
        meta = {"record_s": round(len(audio) / self.sr, 2),
                "asr_ms": round((time.perf_counter() - started) * 1000),
                "asr_model": getattr(self.asr, "key", "?")}
        self.last = {"text": res.text, **meta}
        print(f"🎙 听到：「{res.text}」", file=self.out)
        self.on_result(res.text, meta)

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        print(f"PTT 监听中：按住键说话，松开出结果（vk=0x{self.vk:02X}）", file=self.out)
        # 设置采集或重启监听时仍按住的按钮，不应生成一次提问。
        while not self._stop.is_set() and self._down():
            self._stop.wait(self.poll_s)
        while not self._stop.is_set():
            if not self._down():
                time.sleep(self.poll_s)
                continue
            # 按下
            if self.is_busy():
                self.stats["ignored_busy"] += 1
                # 等键松开，避免连发
                while self._down() and not self._stop.is_set():
                    time.sleep(self.poll_s)
                continue
            if not self._capture_lock.acquire(blocking=False):
                self._stop.wait(self.poll_s)
                continue
            self.state = "recording"
            try:
                audio = self._record_while(lambda: not self._down())
                self._process(audio)
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                print(f"✗ PTT 处理失败：{exc}", file=self.out)
                self.stats["errors"] += 1
            finally:
                self.state = "idle"
                self._capture_lock.release()
            # 超时或失败后必须等到松键；一次按住只产生一条录音。
            while not self._stop.is_set() and self._down():
                self._stop.wait(self.poll_s)
