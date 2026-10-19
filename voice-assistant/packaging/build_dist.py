"""一键打包：出 exe，再把语音包 / 模型 / 配置铺进发行目录。

    python packaging/build_dist.py               # 完整包（含模型与语音包，约 500 MB）
    python packaging/build_dist.py --no-assets   # 只出程序本体（体积小，开发自测用）
    python packaging/build_dist.py --zip         # 顺便压成 zip（几百 MB，要几分钟）

产物：build/dist/小小领航员/
    小小领航员.exe          双击启动
    config.yaml             设置（相对路径，指向同目录的语音包与模型）
    intents_examples.yaml   短语库（加词改这份）
    models/                 语音识别模型
    build/pack/             语音包
    build/assistant-extra/  助手补充素材
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

VA = pathlib.Path(__file__).resolve().parents[1]        # voice-assistant/
PROJ = VA.parent                                        # iRacing spotter/
PKG = VA / "packaging"
DIST = PROJ / "build" / "dist"
APP_NAME = "小小领航员"


def run(cmd: list[str], **kw) -> None:
    print(">>", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def make_config() -> str:
    """把工作区 config.yaml 变成发行版：语音包/素材路径改成相对的。"""
    text = (VA / "config.yaml").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(\s*dir:).*$", r'\1 "build/pack/Mandarin-CNTW-Xusheng_Chen"',
                  text, count=1)
    text = re.sub(r"(?m)^(\s*extra_dir:).*$", r'\1 "build/assistant-extra"',
                  text, count=1)
    text = re.sub(r"(?m)^(\s*models_dir:).*$", r'\1 "models"', text, count=1)
    text = re.sub(r"(?m)^(\s*telemetry_dir:).*$", r'\1 "build/assistant-telemetry"', text, count=1)
    banner = ("# 发行版配置：相对路径都指向本目录下的语音包与模型。\n"
              "# 改完保存即生效（界面上能改的项会被 config.local.yaml 覆盖）。\n")
    return banner + text


def readme() -> str:
    ver = (VA / "VERSION").read_text(encoding="utf-8").strip()
    return fr"""iRacing 小小领航员 v{ver}
制作：xushengchen from Moondrop-Dox Racing
=========================================================

一、这是什么
按住一个键（默认右 Ctrl），对着麦克风问一句，比如「油还够吗」，
用车内语音包的同款音色回答你。识别和匹配全程在本机跑，
不联网、不上传、不花钱。

二、怎么启动
双击「小小领航员.exe」——会打开一个原生窗口，就是软件本体。
没有命令行黑框；安装版日志位于 %LOCALAPPDATA%\LittleNavigator\logs；便携版写在本目录 logs。
退出：窗口右上角 × ，或界面左下角的「退出程序」按钮。
命令行可选参数：
    小小领航员.exe --sim        不开游戏，用模拟数据试
    小小领航员.exe --port 8788  换端口（端口被占用时）

三、第一次要做的三件事
1. 界面 →「设置 · 语音输入」→ 选择「采集键盘」或「采集方向盘」
   按一下希望作为 PTT 的按钮，采集后自动保存；方向盘通过 SDL 直接读取按钮。
2. 同一页确认麦克风选对了（默认一般就行）
3. 按住 PTT 说「油还够吗」，看主面板有没有反应

三·五、想用 AI 大模型？（可选，默认关闭）

规则型问答不联网、不花钱，日常够用。如果你希望「为什么我在变慢」这类
需要推理的问题也有人答，可以接自己的大模型：

1. 打开「AI 与改车」页 → 启用 AI 分析 → 档位先保持「C · 白名单」
2. 预设选你的服务商（DeepSeek / 火山方舟 / 月之暗面 / 本地 Ollama），
   或者直接填接口地址和模型名
3. 填入你自己的 API Key → 点「测试连接」
4. 在 config.local.yaml 里填 key 也可以，比界面更安全（那个文件不进安装包）

费用由你的账号承担，软件不代付、不转发。模型调用有硬上限（单次 2 秒、
回答 60 字），超时或读不到数据会明确告诉你，不会瞎编。

语音合成（TTS）同样是可选项：不配就只有文字，配了才出声。

三·六、想用语音改车？（可选，默认关闭）

打开「AI 与改车」页 → 允许语音改车，然后可以说：
    「换胎」「换左前胎」「加二十升油」「加满」「快修」「撕膜」「清空进站」

能做：换胎（按角或四条）、加油、快修、清洁挡风、清空进站设置
做不到：轮胎配方（软/硬/雨）要在车内黑框选；刹车平衡、TC/ABS、雨刷也改不了

⚠️ 语音识别会听错。说「换左前胎」被识别成「换右前胎」就是换错轮胎。
   建议先在练习赛里试熟，再上正式比赛。

四、界面怎么用
顶栏右侧有四个方块，是「赛前检查」：游戏 / 麦克风 / AI / 改车。
全是绿点就可以开跑；黄点表示能跑但有前提（比如没连游戏、AI 没填密钥）；
灰点表示该项没启用。开赛前扫一眼就知道准备到哪了。

主面板        连接状态、PTT 状态、上一条问答、油量/名次/落差/赛道
问答记录      每一次提问、走哪条路、花多久
诊断统计      命中率、延迟分布、没听明白的问法清单
意图识别      语音严格模式（对不上就拒绝，推荐一直开）
语音输入      ASR 模型、麦克风、PTT 按键、误触阈值
语音播报      音量、变体冷却、快速试听
音频路由      播报输出设备、播报时压低游戏音量
AI 与改车     AI 大模型、语音合成、语音改车（三项都默认关闭）
意图调试台    打字提问，看逐层判定和将播的内容，可试听

五、文件说明
config.yaml             所有设置（带注释，可直接编辑）
intents_examples.yaml   能听懂哪些问法都写在这里，可以自己加
models/                 本地语音识别模型（SenseVoice）
build/pack/             语音包（iRacing 用同一份，也可单独装进游戏）
build/assistant-extra/  助手拼接件（「还剩」「圈」这类连接件，30 条）
build/assistant-telemetry/  627 条助手话术（遥测回答、拒绝提示、改车确认）
logs/                   问答记录、没听明白的问法（下次加词就看它）
state/                  变体冷却记录（安装版保存在用户数据目录）

六、常用问法
目前还有多少油；油还能跑几圈；油够跑完吗；每圈油耗多少。
比赛还剩几圈；比赛还剩多久；已经完成几圈；我上一圈多少；最佳圈速多少。
第一名圈速多少；第一名最佳圈速多少；前车圈速多少；后车最佳圈速多少。
前车多远；后车多远；现在第几名；同组第几名。
维修区限速多少；还剩几次快修；必修还剩多久；事故点多少。
气温多少；路面温度多少；降雨强度多少；左前冷胎设定气压多少。
水温多少；油温多少；现在引擎转速多少；ABS 现在几档；TC 现在几档。
第一名指全场第一名，圈速默认上一完整圈，胎温和磨损是有效进站检测数据。

七、常见问题
· 游戏未连接：真实模式不会切换成模拟数据；需要测试时请显式使用 --sim
· 问题能识别却答不了：缺少可靠遥测时会明确拒绝；车损不能从事故点推算。
  冷胎压是设定值，胎温/磨损是进站历史值，邻车秒差是估计值。
  缺少可靠数据时会说明原因；一次询问一个具体项目。
· 麦克风打不开：检查 Windows 麦克风权限、设备是否连接、是否被独占占用
· 按 PTT 没反应：确认麦克风选对；确认该键在 iRacing 里没被占用为别的功能
· 经常「没听明白」：到「诊断 · 统计」看清单，把这些说法加进
  intents_examples.yaml（格式照抄，重启程序生效）
· 想换语音包：替换 build/pack 目录内容，或改 config.yaml 的语音包路径
· 播报时游戏声变小：那是「压低游戏音量」功能，可在「音频路由」关掉
· 安装版用户设置和日志：%LOCALAPPDATA%\LittleNavigator，升级/卸载均保留。
· 出问题：查看用户数据目录中的 logs/app.log 与 logs/qa.jsonl
· AI 没反应：确认「AI 与改车」页里已启用、填了接口地址和 Key，并点过「测试连接」。
  顶栏 AI 方块是灰的就说明没启用；黄的说明在启用但缺密钥。
· AI 答「现在读不到」：那是设计如此——遥测里没有的数据它不会猜。这是好事。
· 改车没反应：顶栏「改车」方块是灰的说明没启用；黄的是启用了但没连上游戏。
  另外改车指令只在游戏里有效，练习赛/回放里可能不生效。
· 说「换软胎」没反应：轮胎配方不在广播接口里，只能进车内黑框手动选。
"""


def copy_tree(src: pathlib.Path, dst: pathlib.Path) -> int:
    if not src.exists():
        raise FileNotFoundError(f"缺少发行素材：{src}")
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return sum(1 for _ in dst.rglob("*") if _.is_file())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-assets", action="store_true", help="不铺模型/语音包（小程序本体）")
    ap.add_argument("--zip", action="store_true", help="最后压成 zip")
    ap.add_argument("--clean", action="store_true", help="先删旧产物")
    ap.add_argument("--dist-root", type=pathlib.Path, default=DIST, help="独立发行目录，必须位于本项目 build 目录内")
    args = ap.parse_args()

    dist = args.dist_root.resolve()
    if not dist.is_relative_to((PROJ / 'build').resolve()) or dist == (PROJ / 'build').resolve():
        raise ValueError('发行目录必须位于本项目 build 的子目录内')
    out_dir = dist / APP_NAME
    if args.clean and out_dir.exists():
        if out_dir.resolve().parent != dist:
            raise ValueError("构建清理路径超出项目发行目录")
        print("清掉旧产物：", out_dir)
        shutil.rmtree(out_dir)
    if not args.no_assets:
        required = [PROJ / "models" / "sense-voice" / "model.int8.onnx",
                    PROJ / "models" / "sense-voice" / "tokens.txt",
                    PROJ / "build" / "pack" / "Mandarin-CNTW-Xusheng_Chen" / "spmsg.txt",
                    PROJ / "build" / "assistant-extra" / "spmsg.txt"]
        required.append(PROJ/'build'/'assistant-telemetry'/'spmsg.txt')
        for asset in required:
            if not asset.is_file():
                raise FileNotFoundError(f"缺少发行素材：{asset}")
    import irsdk  # 确保真实 SDK 依赖存在，不能带着模拟回退发布。

    py = sys.executable
    run([py, "-m", "PyInstaller", "--noconfirm",
         "--distpath", str(dist),
         "--workpath", str(PROJ / "build" / "pyinstaller"),
         str(PKG / "xiaoxiao.spec")], cwd=str(VA))

    # ---- 铺配置与素材 ----
    (out_dir / "config.yaml").write_text(make_config(), encoding="utf-8")
    (out_dir / "使用说明.txt").write_text(readme(), encoding="utf-8")
    shutil.copy2(VA / "intents_examples.yaml", out_dir / "intents_examples.yaml")
    shutil.copy2(VA / "VERSION", out_dir / "VERSION")
    if not args.no_assets:
        print("铺模型与语音包（几百 MB，慢一点）…")
        n1 = copy_tree(PROJ / "models" / "sense-voice", out_dir / "models" / "sense-voice")
        n2 = copy_tree(PROJ / "build" / "pack" / "Mandarin-CNTW-Xusheng_Chen",
                       out_dir / "build" / "pack" / "Mandarin-CNTW-Xusheng_Chen")
        n3 = copy_tree(PROJ / "build" / "assistant-extra",
                       out_dir / "build" / "assistant-extra")
        n4 = copy_tree(PROJ/'build'/'assistant-telemetry',out_dir/'build'/'assistant-telemetry')
        print(f"模型 {n1} 个文件 / 语音包 {n2} 个 / 助手素材 {n3+n4} 个")

    total = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file())
    print(f"\n发行目录：{out_dir}")
    print(f"合计 {total / 1024 / 1024:.0f} MB")

    if args.zip:
        zpath = dist / f"{APP_NAME}_v{(VA / 'VERSION').read_text(encoding='utf-8').strip()}.zip"
        print("压缩中…（几百 MB，要几分钟）")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in sorted(out_dir.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(out_dir.parent))
        print(f"zip：{zpath}（{zpath.stat().st_size / 1024 / 1024:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
