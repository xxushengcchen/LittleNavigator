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
import queue
import sys
import threading
import time
from typing import Callable

import numpy as np

# 审计 R06：sounddevice 依赖 PortAudio DLL，缺了会让模块 import 直接失败，
# 进而拖垮整个 engine（连 TTS 播放、文本问答都用不了）。这里改成可选依赖：
# 真正需要录音时才按需导入，缺库给结构化错误而不是 ImportError。
try:                                    # pragma: no cover - 环境相关
    import sounddevice as _sd
except Exception:                       # noqa: BLE001
    _sd = None


class AudioBackendError(RuntimeError):
    """录音后端不可用（例如缺少 PortAudio）。"""


def _sounddevice():
    global _sd
    if _sd is None:
        try:
            import sounddevice as imported
        except Exception as exc:        # noqa: BLE001
            raise AudioBackendError(
                "录音后端不可用：缺少 sounddevice/PortAudio，"
                "可以改用文本输入或安装 PortAudio") from exc
        _sd = imported
    return _sd


class _SoundDeviceProxy:
    """保持既有 ``sd.xxx`` 调用写法，但每次访问时才解析真实模块。"""

    def __getattr__(self, name):
        return getattr(_sounddevice(), name)


sd = _SoundDeviceProxy()

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
                 is_busy: Callable[[], bool] | None = None, out=sys.stdout,
                 analysis_vk: int | None = None,
                 on_analysis_result: Callable[[str, dict], None] | None = None):
        """`analysis_vk` 是 AI 分析键：按它录音的提问会标成 source=analysis，
        由上层决定走模型而不是规则层。None 表示未绑定（默认）。"""
        ptt = cfg.get("ptt", {})
        self.asr = asr
        self.on_result = on_result
        self.is_busy = is_busy or (lambda: False)
        self.out = out
        self.vk = resolve_vk(ptt.get("vk", 0xA3))
        # 分析键必须与 PTT 不同：同一个键绑两用会让每次提问都变成分析请求
        self.analysis_vk = (resolve_vk(analysis_vk)
                            if analysis_vk is not None else None)
        if self.analysis_vk is not None and self.analysis_vk == self.vk:
            self.analysis_vk = None
        self.on_analysis_result = on_analysis_result or on_result
        self.pending_source = "ptt"
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
        # 审计 R01：识别/回调（可能要等模型 1~2 秒）不能跑在轮询线程里，
        # 否则等待期间按下的 PTT 事件整段丢失。轮询线程只负责录音并投递，
        # 处理放进单条工作线程，保证「一次按住一条结果」且顺序不乱。
        self._work_q: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._capture_lock = threading.Lock()
        self.error = None
        self.stats = {"segments": 0, "ignored_busy": 0, "too_short": 0, "errors": 0}
        self.state = "idle"          # idle | recording | thinking
        self.last: dict | None = None

    # ------------------------------------------------------------------
    def apply_analysis_key(self, vk) -> int | None:
        """热更新分析键：保存 ai.analysis_key 后无需停开监听即可生效。

        审计 R04：以前 analysis_vk 只在构造时快照一次，运行中改绑/解除
        都要手动重启监听。这里提供与构造同语义的入口（同键仍被拒绝）。
        """
        resolved = resolve_vk(vk) if vk is not None else None
        if resolved is not None and resolved == self.vk:
            resolved = None
        self.analysis_vk = resolved
        return self.analysis_vk

    def _down(self):
        return self.wheel.pressed(self.binding) if self.wheel is not None else _key_down(self.vk)

    def _analysis_down(self) -> bool:
        """分析键是否按下。方向盘绑定只作用于 PTT，分析键始终是键盘键。"""
        return (self.analysis_vk is not None
                and _key_down(self.analysis_vk))

    def start(self) -> None:
        if self.running:
            return
        # 审计复审 R01：上一次的 worker 可能还卡在识别/模型回调里（stop 只
        # join 2 秒）。它恢复后会继续消费同一个队列，出现两个消费者。
        # 用代次令牌让旧 worker 的下一次循环直接退出，新 worker 用新代次。
        self._work_gen = getattr(self, "_work_gen", 0) + 1
        gen = self._work_gen
        self._stop.clear()
        # 上一次 stop 之后可能残留音频，重新开始监听时直接丢掉。
        while True:
            try:
                self._work_q.get_nowait()
            except queue.Empty:
                break
        self._worker = threading.Thread(target=self._work_loop, args=(gen,),
                                        name="ptt-process", daemon=True)
        self._worker.start()
        self._thread = threading.Thread(target=self._loop, name="ptt-listener", daemon=True)
        self._thread.start()

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> bool:
        self._stop.set()
        # 作废旧 worker：即使它还阻塞在回调里，回来也不能再处理/写 state。
        self._work_gen = getattr(self, "_work_gen", 0) + 1
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        # 让工作线程退出（若正阻塞在外部回调里，join 会超时后自然放弃，
        # 仍由 daemon 属性保证不挡进程退出）。
        self._work_q.put(None)
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=2.0)
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
            # 审计 R09：界面测试录的是普通提问，不能继承上一条分析键留下的
            # pending_source，否则会绕过本地规则直接请求 AI。
            self.pending_source = "ptt"
            self.state = "recording"
            deadline = time.monotonic() + seconds
            audio = self._record_while(lambda: time.monotonic() >= deadline)
            if not self._stop.is_set():
                self._process(audio, "ptt")
            return {"running": self.running, "simulated_seconds": seconds}
        finally:
            self.state = "idle"
            self._capture_lock.release()

    def _process(self, audio, source: str | None = None):
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
        if source is None:
            source = self.pending_source
        self.last = {"text": res.text, **meta, "source": source}
        label = "分析" if source == "analysis" else ""
        print(f"🎙 听到{label}：「{res.text}」", file=self.out)
        callback = (self.on_analysis_result if source == "analysis"
                    else self.on_result)
        callback(res.text, {**meta, "source": source})

    def _work_loop(self, gen: int) -> None:
        """串行执行识别/回调（审计 R01）。轮询线程只投递录音，不等模型。

        审计复审：gen 是启动代次；stop()/start() 会让它失效，旧 worker
        恢复后不再消费队列，也不会覆盖新一轮的 state/error。
        """
        while True:
            item = self._work_q.get()
            if item is None:
                return
            if gen != getattr(self, "_work_gen", 0):
                return
            audio, source = item
            try:
                self._process(audio, source)
            except Exception as exc:  # noqa: BLE001
                if gen != getattr(self, "_work_gen", 0):
                    return
                self.error = str(exc)
                print(f"✗ PTT 处理失败：{exc}", file=self.out)
                self.stats["errors"] += 1
            finally:
                if gen == getattr(self, "_work_gen", 0):
                    self.state = "idle"

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        log = f"PTT 监听中：按住键说话，松开出结果（vk=0x{self.vk:02X}）"
        if self.analysis_vk is not None:
            log += f"；分析键 0x{self.analysis_vk:02X}"
        print(log, file=self.out)
        # 方向盘后端在构造时已死：正确报失败，不冒充键盘继续轮询
        # （审计 R05：Service 会读 listener.error，而不是显示「运行中」）。
        wheel_err = getattr(self.wheel, "error", None)
        if wheel_err:
            self.error = wheel_err
            print(f"✗ {wheel_err}", file=self.out)
            return
        # 设置采集或重启监听时仍按住的按钮，不应生成一次提问。
        # 分析键同理（审计 R10）：按住分析键启动会立刻录音并直接请求 AI。
        while not self._stop.is_set() and (self._down() or self._analysis_down()):
            self._stop.wait(self.poll_s)
        while not self._stop.is_set():
            # 审计复审 R05：wheel 的失败可能晚于构造时的等待才出现，
            # 只在 _loop 开头查一次会留下「running=True 但按不出东西」。
            late_err = getattr(self.wheel, "error", None)
            if late_err:
                self.error = late_err
                print(f"✗ {late_err}", file=self.out)
                return
            # 两个输入源共用一套录音管线，用 pressed() 区分是谁；
            # 同时按下时 PTT 优先——它是比赛中的主入口，AI 是补充。
            pressed = self._down
            if pressed():
                source = "ptt"
            elif self._analysis_down():
                pressed = self._analysis_down
                source = "analysis"
            else:
                time.sleep(self.poll_s)
                continue
            # 按下
            if self.is_busy():
                self.stats["ignored_busy"] += 1
                # 等键松开，避免连发
                while pressed() and not self._stop.is_set():
                    time.sleep(self.poll_s)
                continue
            if not self._capture_lock.acquire(blocking=False):
                self._stop.wait(self.poll_s)
                continue
            self.pending_source = source
            self.state = "recording"
            try:
                audio = self._record_while(lambda: not pressed())
                # 审计 R01：只投递，不在轮询线程里跑识别+回调。
                self._work_q.put((audio, source))
                # 审计复审：idle 只在 worker 的 finally 里设，录音结束到处理完
                # 之间会一直显示“录音中”。投递后立刻转 thinking 更贴近实际。
                self.state = "thinking"
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                print(f"✗ PTT 处理失败：{exc}", file=self.out)
                self.stats["errors"] += 1
                # 审计复审：异常时没有投递队列，worker 的 finally 不会跑，
                # state 会永远停在 recording。这里必须自己复位。
                self.state = "idle"
            finally:
                self._capture_lock.release()
            # 超时或失败后必须等到松键；一次按住只产生一条录音。
            while not self._stop.is_set() and pressed():
                self._stop.wait(self.poll_s)
