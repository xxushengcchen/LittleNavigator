"""语音问答入口（里程碑 3）：按住 PTT 说话 → 松键 → 识别 → 回答。

    python -m src.voice_app                 # 正常跑（需要麦克风 + 手柄/键盘按键）
    python -m src.voice_app --sim           # 模拟遥测，无游戏也能试
    python -m src.voice_app --no-play       # 不发声，只看识别与判定
    python -m src.voice_app --selftest 3    # 不开 PTT，直接录 3 秒做硬件自检
    python -m src.voice_app --list-devices  # 列出音频设备
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from . import config as cfg_mod
from .engine import Assistant
from .listener import PttListener


def main(argv: list[str] | None = None) -> int:
    cfg_mod.fix_console_encoding()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true", help="强制用模拟遥测")
    ap.add_argument("--no-play", action="store_true", help="不发声")
    ap.add_argument("--pack", default=None, help="覆盖语音包路径")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--model", default=None, help="覆盖 ASR 模型，见 tools/asr_models.py")
    ap.add_argument("--device", default=None, help="输入设备（名称或序号）")
    ap.add_argument("--list-devices", action="store_true", help="列出音频设备后退出")
    ap.add_argument("--selftest", type=float, default=0.0,
                    help="直接录 N 秒并识别一次（不用按 PTT）")
    ap.add_argument("--wav", default=None,
                    help="把一条 wav 当成「刚说完的话」走完整链路（无麦克风也能验证集成）")
    args = ap.parse_args(argv)

    import sounddevice as sd

    if args.list_devices:
        print(sd.query_devices())
        return 0

    cfg = cfg_mod.load(args.config)
    model = args.model or cfg.get("asr", {}).get("model", "sense-voice")
    threads = cfg.get("asr", {}).get("num_threads", 4)
    if args.device:
        cfg.setdefault("ptt", {})["input_device"] = args.device
    if args.selftest:
        # 自检只开麦，不碰语音包/遥测
        sys.path.insert(0, str(cfg_mod.ROOT))
        from tools.asr_models import load as load_asr
        from .listener import _trim_silence, check_input_device

        dev_err = check_input_device(args.device)
        if dev_err:
            print(f"✗ {dev_err}")
            return 2
        asr = load_asr(model, num_threads=threads)
        print(f"ASR {model} 载入 {asr.load_sec:.2f}s；录 {args.selftest:.0f} 秒…开口说话")
        audio = sd.rec(int(args.selftest * 16000), samplerate=16000, channels=1,
                       dtype="float32", device=args.device)
        sd.wait()
        x = _trim_silence(np.asarray(audio).reshape(-1), 16000)
        res = asr.transcribe(x, 16000)
        print(f"识别（{len(x) / 16000:.2f}s 有效音频，{res.decode_sec * 1000:.0f}ms）："
              f"「{res.text}」")
        return 0

    print("=" * 74)
    print("  iRacing 语音问答助手 · 语音入口（里程碑 3）")
    print("=" * 74)
    try:
        asst = Assistant(cfg, no_play=args.no_play, force_sim=args.sim, pack_dir=args.pack)
    except FileNotFoundError as exc:
        print(f"✗ 语音包读不到：{exc}")
        return 2
    for line in asst.info_lines():
        print(line)
    if asst.tel_warn:
        print(f"⚠ {asst.tel_warn}")
    print(f"遥测 provider = {cfg['telemetry']['provider']}"
          f"{'（强制 sim）' if args.sim else ''}  →  {type(asst.tel).__name__}")

    sys.path.insert(0, str(cfg_mod.ROOT))
    from tools.asr_models import load as load_asr

    asr = load_asr(model, num_threads=threads, models_root=cfg_mod.resolve(cfg,cfg['asr']['models_dir']))
    print(f"ASR {model} 载入 {asr.load_sec:.2f}s（{asr.label}）")
    asr.warmup()
    print(f"播放后端 = {asst.player.backend}")
    print()

    if args.wav:
        sys.path.insert(0, str(cfg_mod.ROOT))
        from tools.audio_io import read_wav

        x, sr = read_wav(args.wav)
        res = asr.transcribe(x, sr)
        print(f"🎙 听到（{len(x) / sr:.2f}s，识别 {res.decode_sec * 1000:.0f}ms）：「{res.text}」")
        asst.ask(res.text, source="voice",
                 extra={"record_s": round(len(x) / sr, 2),
                        "asr_ms": round(res.decode_sec * 1000), "asr_model": model})
        return 0

    from .listener import check_input_device

    dev_err = check_input_device(cfg.get("ptt", {}).get("input_device"))
    if dev_err:
        print(f"✗ {dev_err}")
        return 2

    def on_result(text: str, meta: dict) -> None:
        asst.ask(text, source="voice", extra=meta)
        print()

    listener = PttListener(asr, cfg, on_result, is_busy=lambda: asst.busy)
    try:
        listener.start()
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
    finally:
        listener.stop()
        asst.close()
    s = listener.stats
    print(f"结束。触发 {s['segments']} 次，播报中忽略 {s['ignored_busy']} 次，"
          f"误触 {s['too_short']} 次，出错 {s['errors']} 次。")
    print(f"日志写在 {asst.qa_log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
