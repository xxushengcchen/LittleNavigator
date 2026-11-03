"""试播一条语音包消息：验证输出设备与音量。

    python tools/play_test.py SPCC_YELOUT
    python tools/play_test.py SPCC_YELOUT --device 4 --volume 0.8
    python tools/play_test.py AST_REFUSE_HEARD --repeat 2
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg_mod          # noqa: E402
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
    device = resolve_device(args.device if args.device is not None else pb.get("device"))
    player = Player(device=device,
                    volume=args.volume if args.volume is not None else pb.get("volume", 1.0))
    print(f"播放后端 = {player.backend}")
    for i in range(args.repeat):
        v = picker.pick(args.msgid)
        print(f"[{i + 1}/{args.repeat}] {args.msgid}  「{v.text}」")
        sec = player.play(pack.path_of(v))
        print(f"    播放耗时 {sec:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
