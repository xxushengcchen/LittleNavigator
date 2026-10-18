"""通过 iRacing 广播消息操作进站服务勾选。

这是本项目**唯一**写游戏状态的地方，所以每一处都按最坏情况设计。

能力边界（已核对 iRacing SDK 定义，见下方常量）：
    ✅ 换胎（按角：左前/右前/左后/右后，可带胎压 KPa）
    ✅ 加油（可带升数）
    ✅ 快修（Fast Repair）
    ✅ 清洁挡风（用了撕膜 Tearoff）
    ✅ 清除全部 / 清单项

    ❌ **轮胎配方（软/硬/中性）** —— 广播消息里没有这个操作。
       pyirsdk 1.3.7 里有个未文档化的 `tc = 12`，但官方 Go 定义止于 11、
       Rust 库止于 ClearFuel，`tc` 的参数语义无法核实。
       配方选错是要丢比赛的，按「绝不猜」不实现——这只在黑框里选。
    ❌ 刹车平衡、TC/ABS 档位、雨刷、方向盘 —— 都不是进站服务项，
       广播 API 管不到。

为什么发送后还要回读 `PitSvFlags`：
    `SendNotifyMessageW` 只是把消息丢过去，返回 0 也不代表游戏受理了
    （未进车、回放中、旁观时都会静默忽略）。所以「已发送」不等于
    「已生效」——必须回读进站服务勾选位，真的变了才敢跟用户说设好了。
"""
from __future__ import annotations

import time

# 广播消息：BroadcastMsg.pit_command
_BROADCAST_PIT_COMMAND = 9

# PitCommandMode（与 iRacing SDK 官方定义逐一对应）
PIT_CLEAR = 0          # 清空所有进站勾选
PIT_WS = 1             # 清洁挡风（用掉一张撕膜）
PIT_FUEL = 2           # 加油；var=升数，0 表示沿用现有量
PIT_LF = 3             # 换左前胎；var=胎压 KPa，0 表示沿用
PIT_RF = 4
PIT_LR = 5
PIT_RR = 6
PIT_CLEAR_TIRES = 7    # 取消换胎勾选
PIT_FR = 8             # 快修
PIT_CLEAR_WS = 9
PIT_CLEAR_FR = 10
PIT_CLEAR_FUEL = 11

# PitSvFlags 位（回读验证用）
FLAG_TYRES = {"LF": 0x01, "RF": 0x02, "LR": 0x04, "RR": 0x08}
FLAG_FUEL = 0x10
FLAG_WS = 0x20
FLAG_FR = 0x40

# 命令模式 → 角位名
_CORNER_CMD = {"LF": PIT_LF, "RF": PIT_RF, "LR": PIT_LR, "RR": PIT_RR}

# 加油量的合理范围。超出就拒绝，不去猜用户想加多少。
FUEL_MIN_L = 1.0
FUEL_MAX_L = 120.0

_VERIFY_TIMEOUT_S = 1.0
_VERIFY_POLL_S = 0.05


class CarControlError(RuntimeError):
    """改车失败。调用方据此降级为「没改成」。"""


class CarController:
    """进站服务的执行器。`sdk` 可注入，便于在没装 pyirsdk 的机器上测。"""

    def __init__(self, cfg: dict | None = None, sdk=None, out=None):
        import sys

        cc = (cfg or {}).get("car_control") or {}
        self.enabled = bool(cc.get("enabled", False))
        self.verify = bool(cc.get("verify", True))
        self.out = out or sys.stdout
        self.sdk = sdk
        self._err: str | None = None
        if self.sdk is None and self.enabled:
            try:
                import irsdk  # type: ignore
                self.sdk = irsdk.IRSDK()
            except Exception as exc:      # noqa: BLE001
                self._err = f"pyirsdk 不可用：{exc}"

    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        """能不能真的发指令。未启用 / 无 pyirsdk / 未连接都返回 False。"""
        if not self.enabled or self.sdk is None:
            return False
        try:
            return bool(getattr(self.sdk, "is_initialized", False)
                        and getattr(self.sdk, "is_connected", False))
        except Exception:      # noqa: BLE001
            return False

    def status(self) -> dict:
        return {"enabled": self.enabled,
                "available": self.available,
                "verify": self.verify,
                "error": self._err}

    # ------------------------------------------------------------------
    def _send(self, mode: int, var: int = 0) -> None:
        """发一条广播。失败一律抛 CarControlError。"""
        if not self.enabled:
            raise CarControlError("改车功能未启用")
        if self.sdk is None:
            raise CarControlError(self._err or "pyirsdk 不可用")
        try:
            # pyirsdk 的 pit_command(mode, var) 内部就是 BroadcastMsg.pit_command
            self.sdk.pit_command(mode, int(var))
        except Exception as exc:      # noqa: BLE001
            raise CarControlError(f"发送失败：{exc}") from exc

    # ------------------------------------------------------------------
    def expected_flags(self, action: dict) -> int:
        """算这条动作应该点亮哪些 PitSvFlags 位。用于回读验证。"""
        kind = action.get("kind")
        if kind == "tyre":
            flags = 0
            for corner in action.get("corners") or []:
                flags |= FLAG_TYRES[corner]
            return flags
        if kind == "fuel":
            return FLAG_FUEL
        if kind == "tearoff":
            return FLAG_WS
        if kind == "fast_repair":
            return FLAG_FR
        return 0            # clear 类：验证方式是「这些位都灭了」

    def apply(self, action: dict) -> dict:
        """执行一条已解析的动作，返回结果。不抛异常给引擎。"""
        kind = action.get("kind")
        sent: list[int] = []
        try:
            if kind == "clear":
                self._send(PIT_CLEAR)
                sent.append(PIT_CLEAR)
            elif kind == "clear_tires":
                self._send(PIT_CLEAR_TIRES)
                sent.append(PIT_CLEAR_TIRES)
            elif kind == "tyre":
                for corner in action.get("corners") or []:
                    pressure = int(action.get("pressure_kpa") or 0)
                    self._send(_CORNER_CMD[corner], pressure)
                    sent.append(_CORNER_CMD[corner])
            elif kind == "fuel":
                litres = action.get("litres")
                self._send(PIT_FUEL, int(round(litres)) if litres else 0)
                sent.append(PIT_FUEL)
            elif kind == "tearoff":
                self._send(PIT_WS)
                sent.append(PIT_WS)
            elif kind == "fast_repair":
                self._send(PIT_FR)
                sent.append(PIT_FR)
            else:
                raise CarControlError(f"未知动作：{kind}")
        except CarControlError as exc:
            return {"ok": False, "sent": sent, "error": str(exc)}
        return {"ok": True, "sent": sent, "kind": kind,
                "expected_flags": self.expected_flags(action)}

    # ------------------------------------------------------------------
    def verify_applied(self, action: dict, read_flags) -> dict:
        """回读进站服务位，确认游戏真的受理了。

        `read_flags` 是个零参函数，返回当前 PitSvFlags（拿不到返回 None）。
        超时也算失败——宁可不做承诺，也不能说「设好了」而其实没设。
        """
        expected = self.expected_flags(action)
        if expected == 0:
            return {"verified": None, "reason": "该动作没有可回读的位"}
        deadline = time.monotonic() + _VERIFY_TIMEOUT_S
        last = None
        while time.monotonic() < deadline:
            last = read_flags()
            if last is None:
                return {"verified": None, "reason": "读不到 PitSvFlags"}
            if (last & expected) == expected:
                return {"verified": True, "flags": last}
            time.sleep(_VERIFY_POLL_S)
        return {"verified": False, "flags": last,
                "reason": f"游戏没有点亮这些位（期望 {expected:#x}，实际 "
                          f"{0 if last is None else last:#x}）"}
