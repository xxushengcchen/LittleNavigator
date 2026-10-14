"""播报时压低游戏音量 —— Windows 按应用音量（Core Audio）。

用 pycaw 找到 iRacing 的音频会话，播报期间把它的音量乘一个系数，播完还原。

原则：
  · 只动 iRacing 自己的会话，不碰系统总音量、不碰别的应用
  · iRacing 没在跑（没有音频会话）时静默跳过，下次播报再找
  · pycaw 缺失或 COM 出错时降级为空操作，只提示一次，绝不打断播报
"""
from __future__ import annotations

import atexit
import sys
import threading

DEFAULT_PROCS = ["iRacingSim64DX11.exe"]


class GameDucker:
    def __init__(self, enabled: bool = True, processes: list[str] | None = None,
                 amount_db: float = -12.0, out=sys.stdout):
        self.enabled = enabled
        self.amount_db = float(amount_db)
        self.procs = [p.lower() for p in (processes or DEFAULT_PROCS)]
        self.out = out
        self._ducked: list[tuple[object, float]] = []   # [(音量接口, 原始值)]
        self._warned = False
        self._owner = None
        self._com = None
        if self.enabled:
            try:
                import pycaw.pycaw  # noqa: F401
            except Exception:  # noqa: BLE001
                self.enabled = False
                self._warn("没装 pycaw，播报时不会压低游戏音量")
        # 进程被关掉时也要把游戏音量还回去，否则会一直闷着
        atexit.register(self.restore)

    # ------------------------------------------------------------------
    def _warn(self, msg) -> None:
        if not self._warned:
            print(f"⚠ {msg}", file=self.out)
            self._warned = True

    def _match(self, proc_name: str) -> bool:
        n = (proc_name or "").lower()
        return any(n == p or n.startswith(p.split(".")[0]) for p in self.procs)

    def _find(self) -> list[tuple[object, float]]:
        from pycaw.pycaw import AudioUtilities

        found: list[tuple[object, float]] = []
        for s in AudioUtilities.GetAllSessions():
            try:
                proc = s.Process
                if proc is None or not self._match(proc.name()):
                    continue
                vol = s.SimpleAudioVolume
                found.append((vol, float(vol.GetMasterVolume())))
            except Exception:  # noqa: BLE001
                continue
        return found

    # ------------------------------------------------------------------
    def duck(self) -> None:
        if not self.enabled or self._ducked:
            return
        try:
            import comtypes
            comtypes.CoInitialize()
            self._com = comtypes
            self._owner = threading.get_ident()
            sessions = self._find()
        except Exception as exc:  # noqa: BLE001
            self._warn(f"找不到音频会话：{exc}")
            self.restore()
            return
        factor = 10 ** (self.amount_db / 20.0)
        for vol, orig in sessions:
            try:
                vol.SetMasterVolume(min(1.0, max(0.0, orig * factor)), None)
            except Exception:  # noqa: BLE001
                continue
        self._ducked = sessions

    def restore(self) -> None:
        if self._owner is not None and self._owner != threading.get_ident():
            return  # 音频线程将在 finally 中还原，COM 接口不能跨线程使用。
        for vol, orig in self._ducked:
            try:
                vol.SetMasterVolume(orig, None)
            except Exception:  # noqa: BLE001
                pass
        self._ducked = []
        if self._com is not None:
            self._com.CoUninitialize()
            self._com = None
        self._owner = None

    @property
    def active(self) -> bool:
        return bool(self._ducked)
