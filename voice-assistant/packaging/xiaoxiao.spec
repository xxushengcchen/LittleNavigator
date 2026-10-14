# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：iRacing 小小领航员。

程序本体（exe + Python 运行时 + 界面 + 各依赖）打进 dist；
语音包、ASR 模型、配置这些大件由 packaging/build_dist.py 在打包后铺进去，
这样用户能直接看见、替换它们。
"""

import pathlib

from PyInstaller.utils.hooks import collect_all, get_package_paths

VA = pathlib.Path(SPECPATH).resolve().parent          # voice-assistant/

datas = [
    (str(VA / "app" / "static"), "app/static"),       # 界面（HTML/JS）
    (str(VA / "intents_examples.yaml"), "."),         # 内置短语库（exe 旁那份优先）
    (str(VA / "VERSION"), "."),
]

hiddenimports = [
    # 都是运行时才 import 的，PyInstaller 静态分析看不到
    "app.server", "src.config", "src.engine", "src.voicepack", "src.phrase_match",
    "src.listener", "src.telemetry", "src.router", "src.numbers", "src.player",
    "src.duck", "src.intents", "tools.asr_models", "tools.audio_io",
    "irsdk", "yaml",
    "src.wheel", "sdl2", "sdl2dll",
    "src.telemetry_answers", "src.question_forms",
    "sounddevice", "sherpa_onnx", "pypinyin", "opencc",
    "pycaw", "pycaw.pycaw", "pycaw.api", "comtypes", "comtypes.client",
    # 原生窗口（WebView2）
    "app.desktop", "webview", "webview.platforms.edgechromium", "clr", "pythonnet",
]

binaries = []
for pkg in ("sherpa_onnx", "sounddevice", "opencc", "pypinyin", "pycaw", "comtypes",
            "webview", "pythonnet", "clr_loader", "sdl2", "sdl2dll"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# pywebview 的冻结路径回退查找 _MEIPASS 根目录，不只查 webview/lib。
# CLR 程序集及三个平台 native 目录均须按 interop_dll_path 的布局提供。
webview_lib = pathlib.Path(get_package_paths('webview')[1]) / 'lib'
for dll in webview_lib.glob('*.dll'):
    datas.append((str(dll), '.'))
for platform in ('win-x64', 'win-x86', 'win-arm64'):
    native_root = webview_lib / 'runtimes' / platform
    if not (native_root / 'native' / 'WebView2Loader.dll').is_file():
        raise FileNotFoundError(native_root)
    datas.append((str(native_root), platform))

a = Analysis(
    [str(VA / "launcher.py")],
    pathex=[str(VA)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "pytest", "setuptools", "pip"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="小小领航员",
    debug=False,
    strip=False,
    upx=False,
    console=False,                # 原生窗口应用：不要命令行黑框（日志写 logs/app.log）
    icon=str(pathlib.Path(SPECPATH).resolve() / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="小小领航员",
)
