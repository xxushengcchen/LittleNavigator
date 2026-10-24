"""语音包体检：合并主包与助手素材后，逐条验证变体文件真的在磁盘上。

这个脚本是被一个真实 bug 逼出来的：合并两个包时文件路径按主包目录解析，
助手素材的 17 条全部指向不存在的路径，一问「还剩几圈」就报「音频不存在」。

用法：
    python tools/check_pack.py
    python tools/check_pack.py --pack D:/iRacing/sound/spcc/Mandarin-CNTW-Xusheng_Chen
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg_mod      # noqa: E402
from src.voicepack import VoicePack    # noqa: E402


def main() -> int:
    cfg_mod.fix_console_encoding()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", default=None, help="覆盖主包路径")
    ap.add_argument("--extra", default=None, help="覆盖助手素材路径；--no-extra 可关掉")
    ap.add_argument("--no-extra", action="store_true")
    args = ap.parse_args()

    cfg = cfg_mod.load()
    vp = cfg["voice_pack"]
    roots = [cfg_mod.resolve(cfg, args.pack or vp["dir"])]
    if not args.no_extra:
        extra = args.extra if args.extra is not None else vp.get("extra_dir")
        if extra:
            roots.append(cfg_mod.resolve(cfg, extra))
        if vp.get('telemetry_dir'):roots.append(cfg_mod.resolve(cfg,vp['telemetry_dir']))

    pack = VoicePack.load_many(roots)
    print(f"载入 {len(roots)} 个包：{[str(r) for r in roots]}")
    print(f"合并后：msgId {len(pack.variants)} 个，变体 {sum(len(v) for v in pack.variants.values())} 条")

    missing_by_msg: dict[str, list[str]] = {}
    for mid, vs in sorted(pack.variants.items()):
        for v in vs:
            if not pack.path_of(v).exists():
                missing_by_msg.setdefault(mid, []).append(v.file)

    if not missing_by_msg:
        print("✅ 每条变体的文件都在磁盘上")
        return 0
    total = sum(len(v) for v in missing_by_msg.values())
    print(f"✗ {len(missing_by_msg)} 个 msgId 共 {total} 条变体找不到文件：")
    for mid, files in missing_by_msg.items():
        print(f"   {mid}: {', '.join(files[:4])}{' …' if len(files) > 4 else ''}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
