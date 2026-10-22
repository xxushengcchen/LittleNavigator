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

一、启动与第一次使用
双击「小小领航员.exe」打开原生窗口。右上角 × 退出。
在「语音输入」选麦克风，采集键盘或方向盘 PTT 按钮。
开启监听，按住按钮说「油还够吗」，松开后等待回答。
规则问答与本地语音识别无需联网或 API Key。
命令行：--sim 使用明确标记的模拟数据；--port 8788 更换端口。

二、十一个页面
比赛控制台    连接状态、最近回答、油量/名次/后车差距/上一圈、进站勾选读回
问答记录      提问、回答和执行结果；可导出日志
诊断统计      实际命中路径、首包延迟分布与未命中问法
语音输入      模型、麦克风、PTT 绑定与误触阈值（自动保存）
语音播报      音量、变体冷却与试听（自动保存）
音频路由      耳机输出、播报时压低游戏音量（自动保存）
AI 工程师     模型、TTS、专用分析键、赛后复盘（每区单独保存）
进站控制      允许语音改车、执行回读与只读服务示意图（单独保存）
识别设置      严格语音模式与识别边界（自动保存）
文字试问      预览解析，再选择「执行并播报」；操作类会改变进站设置
知识库        短语与意图的连线图、矩阵、可疑样本和数据缺口（只读）

窗口可调整大小。断线时清空遥测；— 表示未读到，0 是真实零值。
轮胎示意图只是读回，不可点击改车。模拟数据不会冒充真实游戏。

「知识库」是只读的诊断页面：把短语、意图和回答字段的对应关系画成连线图，
可切换矩阵视图，也能点开一个意图看它由哪些问法支撑。「可疑样本」列出能被
两条以上意图同时接受的问法，「数据缺口」是游戏端本来读不到的值，不是缺陷。

三、AI 与 TTS（可选，默认关闭）
在「AI 工程师」启用 AI，选择预设或填 base_url / model / API Key，
保存并测试连接。模型费用由自己的服务账号承担。
TTS 在同页的折叠区单独配置、保存；不配 TTS 时 AI 只有文字。
模型有超时、长度和数值来源检查；数据变化或读不到时会拒答。
这些检查不能替代真实比赛与语音识别验证。
响应方式可选「全部交给 AI」：连规则答得上的也交给模型；
安全、处罚与改车仍走本地规则；需配好 TTS 才能听到回答。
「等待语音」可在同页开关；游戏内 Overlay 默认关闭，在 AI 页开启，
可调透明度，用 Ctrl+Alt+O 临时显示/隐藏。

四、语音改车（可选，默认关闭）
在「进站控制」启用并保存，然后说「帮我取消换胎」「只换左侧轮胎」
「帮我加10升油」「取消快修」「勾上快修」。
当前加油只发送整数升数；小数明确拒绝，不会静默取整。
换胎、加油、快修、挡风清洁与清空进站均走确定性词表。
不支持轮胎配方、刹车平衡、TC/ABS 或雨刷操作。
「我是否需要快修」按咨询处理，不依赖问号；单独「快修」仍是快捷口令。
语音可能识别错误，建议先在练习赛核对游戏黑框与软件读回。

五、数据和文件
安装版设置/日志/状态：%LOCALAPPDATA%\LittleNavigator
便携版设置/日志/状态：程序目录；config.local.yaml 覆盖默认配置。
升级前退出程序，在相同范围和位置安装。升级和卸载保留用户数据。
intents_examples.yaml 是短语库；models 是本地模型；build 下是语音。
使用说明、LICENSE、LICENSE-CHANGE.md、ASSETS-LICENSE.md、COMMERCIAL-LICENSE.md、
TRADEMARK.md、INSTALLER-EULA.md、THIRD_PARTY_NOTICES.md 随包提供。
个人非商业免费；商业使用需单独取得商业授权（见 COMMERCIAL-LICENSE.md）。
不要向公开反馈上传 API Key 或完整个人日志。

六、数据范围
前后车秒差是估计值；冷胎压是设定值，胎温/磨损是进站历史值。
事故点不能判断具体车损；没有可靠圈差就不能用秒差代替。
一次询问一个具体项目。软件不支持多轮对话或主动工程师提醒。
连接失败检查游戏是否运行；麦克风失败检查 Windows 权限与设备。
修改失败查看明确错误与游戏读回；反馈时附版本、原话和预期结果。

完整使用指南：https://github.com/xxushengcchen/LittleNavigator/blob/main/docs/guide/user-guide.md
项目主页与反馈：https://github.com/xxushengcchen/LittleNavigator
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
    for notice in ("LICENSE", "LICENSE-CHANGE.md", "ASSETS-LICENSE.md",
                   "COMMERCIAL-LICENSE.md", "TRADEMARK.md",
                   "INSTALLER-EULA.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(PROJ / notice, out_dir / notice)
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
