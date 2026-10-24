"""键盘问答控制台 —— 里程碑 1 的入口（M3 起与语音入口共用 engine）。

用打字代替麦克风，验证「问题 → 意图 → 遥测 → msgId → 播放」这条链路。

    python -m src.console              # 按 config.yaml 的 provider
    python -m src.console --sim        # 强制用模拟遥测
    python -m src.console --no-play    # 不发声，只打印
    python -m src.console --once "油还够吗"
"""
from __future__ import annotations

import argparse
import sys

from . import config as cfg_mod
from .engine import Assistant


def _fmt_snapshot(s) -> str:
    def v(x, unit="", nd=1):
        return "—" if x is None else f"{x:.{nd}f}{unit}"

    return (
        f"  连接={s.connected} 来源={s.source}\n"
        f"  天气 气温={v(s.air_temp,'°C')} 路温={v(s.track_temp,'°C')} "
        f"积水档={s.track_wetness} 天况={s.skies} 降水={v(s.precip,'')} 宣告湿地={s.declared_wet}\n"
        f"  燃油 {v(s.fuel_level,'L',2)}  每圈={v(s.fuel_per_lap,'L',2)}  可跑={v(s.laps_of_fuel,'圈')}\n"
        f"  圈数 当前第{s.lap}圈 剩{s.laps_remain}圈 上圈={v(s.lap_last,'s',3)} 最佳={v(s.lap_best,'s',3)}\n"
        f"  名次 P{s.position}/{s.field_size}  事故点={s.incident_count}  旗语={s.flags}\n"
        f"  差距 前车={v(s.gap_ahead_s,'s')} 后车={v(s.gap_behind_s,'s')}"
    )


def main(argv: list[str] | None = None) -> int:
    cfg_mod.fix_console_encoding()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true", help="强制用模拟遥测")
    ap.add_argument("--no-play", action="store_true", help="不发声")
    ap.add_argument("--pack", default=None, help="覆盖语音包路径")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--once", default=None, help="只问一句就退出")
    ap.add_argument("--gap-ms", type=int, default=60, help="拼装片段之间的静音毫秒")
    args = ap.parse_args(argv)

    cfg = cfg_mod.load(args.config)
    print("=" * 74)
    print("  iRacing 语音问答助手 · 键盘控制台")
    print("=" * 74)
    try:
        asst = Assistant(cfg, no_play=args.no_play, gap_ms=args.gap_ms,
                         force_sim=args.sim, pack_dir=args.pack)
    except FileNotFoundError as exc:
        print(f"✗ 语音包读不到：{exc}")
        print("  用 --pack 指到 build/pack/Mandarin-CNTW-Xusheng_Chen 也可以")
        return 2
    for line in asst.info_lines():
        print(line)
    if asst.tel_warn:
        print(f"⚠ {asst.tel_warn}")
    print(f"遥测 provider = {cfg['telemetry']['provider']}"
          f"{'（强制 sim）' if args.sim else ''}  →  {type(asst.tel).__name__}")
    print(f"播放后端 = {asst.player.backend}")
    print("输入问题后回车；:snap 看快照，:pack 看语音包，:q 退出")
    print()

    if args.once:
        print(f"> {args.once}")
        asst.ask(args.once)
        asst.close()
        return 0

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in (":q", ":quit", "exit", "quit"):
            break
        if line == ":snap":
            print(_fmt_snapshot(asst.snapshot()))
            continue
        if line == ":pack":
            print(f"  {asst.stats}")
            continue
        if line.startswith(":msgid"):
            parts = line.split()
            if len(parts) < 2:
                print("  用法：:msgid SPCC_YELOUT")
                continue
            if not asst.play_msgid(parts[1]):
                print(f"  ✗ 语音包里没有 {parts[1]}")
            continue
        asst.ask(line)

    print(f"结束。本次问答 {len(asst.history)} 条，日志写在 {asst.qa_log}")
    asst.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
