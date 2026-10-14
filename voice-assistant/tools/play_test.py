"""试播一条语音包消息：验证输出设备 / 音量 / 压低游戏音量。

    python tools/play_test.py SPCC_YELOUT
    python tools/play_test.py SPCC_YELOUT --device 4 --volume 0.8
    python tools/play_test.py AST_REFUSE_HEARD --no-duck
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg_mod          # noqa: E402
from src.duck import GameDucker            # noqa: E402
from src.listener import resolve_device    # noqa: E402
from src.player import Player              # noqa: E402
from src.voicepack import VariantPicker, VoicePack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("msgid", help="msgId，例如 SPCC_YELOUT / AST_LAPS_LEAD")
    ap.add_argument("--config", default=None)
    ap.add_argument("--device", default=None, help="输出设备序号/名称；默认读配置")
    ap.add_argument("--volume", type=float, default=None)
    ap.add_argument("--no-duck", action="store_true", help="不要压低游戏音量")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()

    cfg = cfg_mod.load(args.config)
    vp = cfg["voice_pack"]
    roots = [vp["dir"]] + ([vp["extra_dir"]] if vp.get("extra_dir") else [])
    pack = VoicePack.load_many(roots)
    if not pack.has(args.msgid):
        print(f"✗ 语音包里没有 {args.msgid}")
        return 2
    picker = VariantPicker(pack, vp["cooldown"], cfg_mod.resolve(cfg, vp["state_file"]))

    pb = cfg.get("playback", {})
    duck_cfg = pb.get("ducking", {}) or {}
    ducker = None
    if duck_cfg.get("enabled") and not args.no_duck:
        ducker = GameDucker(processes=duck_cfg.get("processes"),
                            amount_db=duck_cfg.get("amount_db", -12.0))
    device = resolve_device(args.device if args.device is not None else pb.get("device"))
    player = Player(device=device,
                    volume=args.volume if args.volume is not None else pb.get("volume", 1.0),
                    ducker=ducker)
    print(f"播放后端 = {player.backend}  压低游戏音量 = "
          f"{'开' if ducker and ducker.enabled else '关'}")
    for i in range(args.repeat):
        v = picker.pick(args.msgid)
        print(f"[{i + 1}/{args.repeat}] {args.msgid}  「{v.text}」")
        sec = player.play(pack.path_of(v))
        print(f"    播放耗时 {sec:.2f}s；ducking 生效 = {bool(ducker and ducker.active)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
