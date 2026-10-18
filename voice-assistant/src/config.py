"""配置加载。"""
from __future__ import annotations

import pathlib
import sys
import math
import os
import shutil
from typing import Any

import yaml

# 项目根目录 = src 的上一层（源码运行时）
ROOT = pathlib.Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def fix_console_encoding() -> None:
    """把控制台切到 UTF-8，并给标准输出加 errors=replace 兜底。

    中文 Windows 控制台默认 GBK：程序里打印 ✓ ✗ ⚠ 这类字符会直接抛
    UnicodeEncodeError（打包版实测踩过），任何字符都不该把程序写崩。
    """
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def runtime_dir() -> pathlib.Path:
    """运行目录：打包后 = exe 所在目录；源码运行时 = 项目根目录。

    打包版把 config.yaml、models/、build/ 都放在 exe 旁边，
    这样用户能直接看见、替换语音包和模型。
    """
    if is_frozen():
        return pathlib.Path(sys.executable).resolve().parent
    return ROOT


def installed_mode() -> bool:
    return is_frozen() and (runtime_dir() / 'installed-mode').is_file()


def data_dir() -> pathlib.Path:
    """安装版资源只读，所有用户数据独立保存；便携版仍用原目录。"""
    if not installed_mode():
        return runtime_dir()
    override = os.environ.get('LITTLE_NAVIGATOR_DATA_DIR')
    base = pathlib.Path(override) if override else pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home() / 'AppData' / 'Local')) / 'LittleNavigator'
    if not base.is_absolute():
        raise ValueError('用户数据目录必须为绝对路径')
    return base


def local_config_path(cfg_path=None) -> pathlib.Path:
    if cfg_path is not None:
        return pathlib.Path(cfg_path).resolve().parent / 'config.local.yaml'
    return data_dir() / 'config.local.yaml'


def migrate_user_data(destination: pathlib.Path) -> None:
    """只迁移旧覆盖层和冷却记录，保留原件且不覆盖已有用户数据。"""
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('config.local.yaml', 'state/cooldown.json'):
        old, new = runtime_dir() / name, destination / name
        if old.is_file() and not new.exists() and old.resolve() != new.resolve():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, new)

DEFAULTS: dict[str, Any] = {
    "voice_pack": {
        "telemetry_dir": None,
        "dir": "D:/iRacing/sound/spcc/Mandarin-CNTW-Xusheng_Chen",
        "cooldown": 4,
        "state_file": "./state/cooldown.json",
    },
    "telemetry": {
        "provider": "irsdk",
        "poll_hz": 60,
        "thread_priority": "below_normal",
        "ring_buffer": {"keep_laps": 20},
        "trend_window_s": 6.0,
    },
    "intent": {
        "l1_keyword": {"enabled": True},
        "voice_strict": True,
        "l3_llm": {"enabled": False},
        "miss_log": {"enabled": True, "path": "./logs/intent_miss.jsonl"},
    },
    "playback": {
        "enabled": True,
        "device": None,            # null = 系统默认输出
        "volume": 1.0,
        "ducking": {
            "enabled": True,
            "processes": ["iRacingSim64DX11.exe"],
            "amount_db": -12.0,
        },
    },
    "audio_guard": {"ptt_only": True, "mute_mic_while_playing": True},
    "asr": {
        "model": "sense-voice",        # sense-voice | zipformer-ctc | streaming-zip
        "models_dir": "../models",
        "num_threads": 4,
    },
    "ptt": {
        "binding": None,
        "vk": 0xA3,                    # 右 Ctrl；也可写 "RCONTROL" / "MOUSE5"
        "poll_ms": 10,
        "min_hold_ms": 250,            # 短于这个当误触丢弃
        "max_hold_s": 8.0,             # 最长录一段
        "input_device": None,          # null = 系统默认输入设备
    },
    "logging": {"level": "info", "dir": "./logs", "qa_log": "./logs/qa.jsonl"},
    # 语音改车：通过 iRacing 广播消息操作进站服务勾选。
    # **默认关闭**。打开后语音说「换胎」「加十升油」会真的改游戏里的进站设置。
    "car_control": {
        "enabled": False,
        "verify": True,        # 发送后回读 PitSvFlags 确认真的生效
    },
    # AI 比赛工程师。默认关闭——不配也能照常使用，行为与接入前完全一致。
    # api_key 刻意不在这里：config.yaml 随安装包分发，密钥走
    # config.local.yaml 或环境变量 LITTLE_NAVIGATOR_AI_KEY。
    "ai": {
        "enabled": False,
        "mode": "whitelist",           # whitelist | analysis_key | full
        "provider": "custom",          # 仅用于界面预填，不参与运行时逻辑
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout_s": 2.0,
        "max_tokens": 300,
        "max_answer_chars": 60,
        "rate_limit_per_min": 6,
        "analysis_key": None,          # {vk: int, binding: dict} 或 None
        "whitelist_intents": [
            "query_laptime", "query_pit", "query_fuel", "query_gap",
            "query_opponent", "query_weather", "query_damage",
        ],
        "tts": {
            "adapter": "off",          # off | doubao | openai
            "base_url": "",
            "api_key": "",
            "model": "",
            "voice": "",
            "speaker": "",             # 豆包音色 ID
            "resource_id": "seed-tts-2.0",
            "format": "wav",
            "sample_rate": 16000,
            "timeout_s": 1.5,
        },
    },
}

# AI 配置的硬上限钳制表：(键路径, 是否整数, 硬上限常量名)
# 放在 load() 合并之后、validate() 之前——用户可以把延迟调更小，调大无效。
# 审查提醒过：ai_constants 若没有生产调用方，硬上限形同虚设。
_AI_CLAMPS: tuple[tuple[tuple[str, ...], bool, str], ...] = (
    (("timeout_s",), False, "LLM_DEADLINE_HARD_MAX"),
    (("max_tokens",), True, "MAX_OUTPUT_TOKENS_HARD"),
    (("max_answer_chars",), True, "MAX_ANSWER_CHARS_HARD"),
    (("tts", "timeout_s"), False, "TTS_DEADLINE_HARD_MAX"),
)


def _clamp_ai(cfg: dict) -> None:
    """把 AI 配置的数值项压到硬上限以内，并留痕。"""
    from . import ai_constants as const

    ai = cfg.get("ai")
    if not isinstance(ai, dict):
        return
    for path, is_int, const_name in _AI_CLAMPS:
        node: Any = ai
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            continue
        leaf = path[-1]
        value = node.get(leaf)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        hard = getattr(const, const_name)
        node[leaf] = (const.clamp_int if is_int else const.clamp)(
            value, hard, "ai." + ".".join(path))



def _merge(base: dict, override: dict) -> dict:
    if not isinstance(base, dict) or not isinstance(override or {}, dict):
        raise ValueError("配置内容必须为对象")
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | pathlib.Path | None = None) -> dict:
    """读配置并补齐默认值。

    界面改的设定写进同目录的 config.local.yaml（覆盖层），
    config.yaml 保持人工维护、注释不被程序破坏。
    """
    p = (pathlib.Path(path) if path else runtime_dir() / "config.yaml").resolve()
    user = {}
    if p.exists():
        user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    local = local_config_path(path)
    managed = path is None and installed_mode()
    if managed:
        migrate_user_data(data_dir())
    if local.exists():
        user = _merge(user, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    cfg = _merge(DEFAULTS, user)
    if managed:
        # 用户覆盖层不能把可写文件重新定向到只读安装目录。
        data = data_dir()
        cfg['voice_pack']['state_file'] = str(data / 'state' / 'cooldown.json')
        cfg['logging']['dir'] = str(data / 'logs')
        cfg['logging']['qa_log'] = str(data / 'logs' / 'qa.jsonl')
        cfg['intent']['miss_log']['path'] = str(data / 'logs' / 'intent_miss.jsonl')
    _clamp_ai(cfg)
    _apply_ai_env(cfg)
    cfg['_ai_learn_dir'] = str(_ai_learn_dir())
    validate(cfg)
    cfg["_path"] = str(p)
    cfg["_root"] = str(p.parent)
    cfg['_local_path'] = str(local)
    return cfg


def _apply_ai_env(cfg: dict) -> None:
    """环境变量里的密钥优先级最高，方便临时换 key 而不动配置文件。"""
    key = os.environ.get("LITTLE_NAVIGATOR_AI_KEY")
    if key:
        cfg.setdefault("ai", {})["api_key"] = key


def _ai_learn_dir() -> pathlib.Path:
    """学习库目录：安装版放用户数据目录，便携版放运行目录。

    绝不写进安装目录——那里是只读的，而且升级会覆盖。
    """
    return data_dir() / "ai_learn"


def resolve(cfg: dict, rel: str) -> pathlib.Path:
    """把配置里的相对路径解析成绝对路径（相对项目根）。"""
    p = pathlib.Path(rel)
    return p if p.is_absolute() else (pathlib.Path(cfg.get("_root", runtime_dir())) / p).resolve()


def validate(cfg: dict) -> None:
    """配置出错时明确拒绝；避免错误设定写盘后让下次启动崩溃。"""
    for section in DEFAULTS:
        if not isinstance(cfg.get(section), dict):
            raise ValueError(f"{section} 必须是配置对象")
    for section, key in [("playback", "ducking"), ("intent", "l1_keyword"),
                         ("intent", "miss_log"), ("telemetry", "ring_buffer")]:
        if not isinstance(cfg[section].get(key), dict):
            raise ValueError(f"{section}.{key} 必须为对象")
    for section, key in [("playback", "enabled"), ("intent", "voice_strict")]:
        if not isinstance(cfg[section][key], bool):
            raise ValueError(f"{section}.{key} 必须为布尔值")
    for section, key in [("playback", "ducking"), ("intent", "l1_keyword"), ("intent", "miss_log")]:
        if not isinstance(cfg[section][key]["enabled"], bool):
            raise ValueError(f"{section}.{key}.enabled 必须为布尔值")
    duck = cfg["playback"]["ducking"]
    db = duck["amount_db"]
    if isinstance(db, bool) or not isinstance(db, (int, float)) or not math.isfinite(db) or not -60 <= db <= 0:
        raise ValueError("压低音量幅度必须为 -60–0 dB")
    if not isinstance(duck["processes"], list) or not all(isinstance(p, str) and p for p in duck["processes"]):
        raise ValueError("游戏进程列表无效")
    checks = [
        ("playback", "volume", 0, 1), ("asr", "num_threads", 1, 16),
        ("ptt", "poll_ms", 1, 1000), ("ptt", "min_hold_ms", 50, 8000),
        ("ptt", "max_hold_s", 0.1, 30), ("telemetry", "poll_hz", 1, 120),
        ("voice_pack", "cooldown", 0, 98),
    ]
    for section, key, lo, hi in checks:
        value = cfg[section][key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{section}.{key} 必须在 {lo}–{hi} 范围内")
    for section, key in [("asr", "num_threads"), ("voice_pack", "cooldown")]:
        if not isinstance(cfg[section][key], int):
            raise ValueError(f"{section}.{key} 必须为整数")
    if cfg["ptt"]["min_hold_ms"] >= cfg["ptt"]["max_hold_s"] * 1000:
        raise ValueError("误触阈值必须小于最大录音时长")
    if cfg["telemetry"]["provider"] not in ("sim", "irsdk"):
        raise ValueError("未知遥测来源")
    if cfg["asr"]["model"] not in ("sense-voice", "zipformer-ctc", "streaming-zip"):
        raise ValueError("未知 ASR 模型")
    from .listener import resolve_vk
    binding=cfg['ptt'].get('binding')
    if binding is not None:
        if not isinstance(binding,dict) or binding.get('type')!='wheel' or not isinstance(binding.get('guid'),str) or len(binding['guid'])!=32:
            raise ValueError('方向盘绑定无效')
        if isinstance(binding.get('button'),bool) or not isinstance(binding.get('button'),int) or not 0<=binding['button']<256:
            raise ValueError('方向盘按钮编号无效')
        for key in ('name','serial','path'):
            if key in binding and not isinstance(binding[key],str):raise ValueError('方向盘设备标识无效')
    if isinstance(cfg["ptt"]["vk"], bool) or not 1 <= resolve_vk(cfg["ptt"]["vk"]) <= 254:
        raise ValueError("PTT 键码必须在 1–254 范围内")
    keep = cfg["telemetry"]["ring_buffer"]["keep_laps"]
    if isinstance(keep, bool) or not isinstance(keep, int) or not 1 <= keep <= 100:
        raise ValueError("keep_laps 必须为 1–100 的整数")
    for section, key in [("ptt", "input_device"), ("playback", "device")]:
        value = cfg[section].get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value,(int,str)) or isinstance(value,int) and value<0):
            raise ValueError(f"{section}.{key} 必须为设备名、非负序号或 null")
    _validate_car_control(cfg)
    _validate_ai(cfg)


def _validate_car_control(cfg: dict) -> None:
    cc = cfg.get("car_control")
    if not isinstance(cc, dict):
        raise ValueError("car_control 必须是配置对象")
    for key in ("enabled", "verify"):
        if not isinstance(cc.get(key), bool):
            raise ValueError(f"car_control.{key} 必须为布尔值")


_AI_MODES = ("whitelist", "analysis_key", "full")
_TTS_ADAPTERS = ("off", "doubao", "openai")


def _validate_ai(cfg: dict) -> None:
    """AI 段校验。风格与既有 validate 一致：不合法就抛，不静默纠正。"""
    ai = cfg.get("ai")
    if not isinstance(ai, dict):
        raise ValueError("ai 必须是配置对象")
    if not isinstance(ai.get("enabled"), bool):
        raise ValueError("ai.enabled 必须为布尔值")
    if ai.get("mode") not in _AI_MODES:
        raise ValueError(f"ai.mode 必须是 {_AI_MODES} 之一")
    for key in ("base_url", "api_key", "model"):
        if not isinstance(ai.get(key), str):
            raise ValueError(f"ai.{key} 必须为字符串")
    for key in ("timeout_s", "max_tokens", "max_answer_chars",
                "rate_limit_per_min"):
        value = ai.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value < 0:
            raise ValueError(f"ai.{key} 必须为非负数")
    if not isinstance(ai.get("whitelist_intents"), list) or \
            not all(isinstance(x, str) for x in ai["whitelist_intents"]):
        raise ValueError("ai.whitelist_intents 必须为字符串列表")

    analysis_key = ai.get("analysis_key")
    if analysis_key is not None:
        if not isinstance(analysis_key, dict):
            raise ValueError("ai.analysis_key 必须为对象或 null")
        vk = analysis_key.get("vk")
        if isinstance(vk, bool) or not isinstance(vk, int) or not 1 <= vk <= 254:
            raise ValueError("ai.analysis_key.vk 必须在 1–254 范围内")

    tts = ai.get("tts")
    if not isinstance(tts, dict):
        raise ValueError("ai.tts 必须为配置对象")
    if tts.get("adapter") not in _TTS_ADAPTERS:
        raise ValueError(f"ai.tts.adapter 必须是 {_TTS_ADAPTERS} 之一")
    for key in ("base_url", "api_key", "model", "voice", "speaker",
                "resource_id", "format"):
        if not isinstance(tts.get(key), str):
            raise ValueError(f"ai.tts.{key} 必须为字符串")
