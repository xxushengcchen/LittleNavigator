# 开发指南

## 环境和资源

使用 Windows 和 Python 3.12。当前版本在 Python 3.12.14 上开发，直接依赖版本在 `voice-assistant/requirements-tested.txt`。

在仓库根目录创建环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r voice-assistant/requirements-tested.txt
```

模型和大量 WAV 没有放进源码仓库。先从 Releases 下载并安装完整发行版，再导入资源。下面用默认当前用户安装路径示范；如果安装到了别处，替换 `--from` 后的路径。

```powershell
.\.venv\Scripts\python.exe scripts/import_runtime_assets.py --from "$env:LOCALAPPDATA\Programs\LittleNavigator"
```

这个脚本只复制模型和语音，不复制用户设置或日志。资源放在根目录的 `models/`、`build/pack/`、`build/assistant-extra/`、`build/assistant-telemetry/`，都被 `.gitignore` 排除。

## 运行

```powershell
Set-Location voice-assistant
..\.venv\Scripts\python.exe -m app.desktop --sim
```

`--sim` 使用明确标记的模拟数据，方便不开游戏测试。连接真实游戏时去掉该参数。源码模式下配置、日志和冷却状态位于 `voice-assistant/`；安装版则保存到用户 AppData 目录。

## 检查

在 `voice-assistant/` 中：

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\.venv\Scripts\python.exe tools/check_library.py
..\.venv\Scripts\python.exe tools/audit_offline.py
```

涉及窗口依赖和打包的改动，还应对实际打包后的 exe 使用 `--window-smoke-test --sim` 检查页面加载。无窗口的服务测试不能代替 WebView2 窗口启动检查。麦克风、方向盘和真实比赛问题也需要实际设备验证。

## 打包

回到仓库根目录，修改 `voice-assistant/VERSION` 后构建便携发行目录：

```powershell
.\.venv\Scripts\python.exe voice-assistant/packaging/build_dist.py
```

完整安装包还需要 Inno Setup，以及 WebView2 安装组件。默认用微软官方**引导器** `MicrosoftEdgeWebview2Setup.exe`（约 2 MB）：缺运行时的机器在安装时联网补装，从而把安装包缩小约 180 MB。要出**全离线版**，改用 Evergreen Standalone Installer `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`（约 180 MB）。两者都从微软官方渠道取得，再显式指定路径：

```powershell
# 默认：引导器（推荐，安装包更小）
.\.venv\Scripts\python.exe voice-assistant/packaging/build_installer.py --compiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" --webview-installer "C:\Downloads\MicrosoftEdgeWebview2Setup.exe"

# 全离线版：离线运行时
.\.venv\Scripts\python.exe voice-assistant/packaging/build_installer.py --compiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" --webview-installer "C:\Downloads\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
```

安装构建脚本会验证 WebView2 安装文件的微软签名。成品及 SHA-256 校验文件输出到 `build/releases/v版本号/`。发布前检查安装、升级、实际窗口和卸载；不要把个人配置或日志带进安装包。

## 补充语音

先改 `content/assistant-telemetry-v1.1.json`。音频生成工具在 `scripts/`，需要额外安装 `imageio-ffmpeg`，并自行准备可用的豆包语音凭据。`scripts/synthesize_telemetry.py` 从环境变量 `DOUBAO_API_KEY` 或不回显输入中读取密钥。

### 采样率：24 kHz 母带 → 16 kHz 发行包

语音包母带是 **24 kHz / 16-bit PCM**（`scripts/build_pack.py` 的默认值）。发行版用 **16 kHz** 以减小安装包体积：母带原样保留（另存并在 Release 备份），再用 `scripts/resample_pack.py` 派生 16 kHz 的发行包。脚本用 ffmpeg 带限重采样，逐条校验采样率／位深／声道与数量，不符合即报错退出。

约定：**16 kHz 发行包放在 `build_dist.py` 读取素材的那三处**——`build/pack/Mandarin-CNTW-Xusheng_Chen`、`build/assistant-extra`、`build/assistant-telemetry`；24 kHz 母带另存别处（如 `build/master-24k/…`），别放这三处，以免被 `build_dist.py` 当作发行素材带上。三份各跑一次，源＝母带、目标＝上述位置：

```powershell
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/pack/Mandarin-CNTW-Xusheng_Chen --dst build/pack/Mandarin-CNTW-Xusheng_Chen
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/assistant-extra --dst build/assistant-extra
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/assistant-telemetry --dst build/assistant-telemetry
```

降采样后用 `tools/audit_offline.py` 核对全部音频为 16 kHz/16-bit/单声道且无损坏。

不要把密钥写进源码、命令行示例、Issue 或 PR。语音服务调用可能收费，普通玩家使用现成发行版不需要调用这些工具。
