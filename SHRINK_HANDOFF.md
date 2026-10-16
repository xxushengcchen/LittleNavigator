# 安装包瘦身 —— 交接说明（临时文档，合并进 main 前可删）

> 2026-10-16 在 macOS 上推进「安装包瘦身」时写的交接说明，让工作能在 Windows 打包机上无缝接续。
> 所有改动都在分支 `feat/shrink-installer-1-webview2-bootstrap`（**未合并**）。

## 一、目标

把 **597 MB** 的 Windows 安装包压小。原定四步：

| 步 | 内容 | 状态 | 预计省 |
|---|---|---|---|
| 1 | WebView2 离线安装器(~180MB) → 微软官方引导器(~2MB) | ✅ 已做 | ~180 MB |
| 2 | 语音包 24 kHz → 16 kHz | ✅ 已做 | ~57 MB |
| 3 | 拆分下载（程序小包 + 素材包单独下载） | ⬜ 未开始 | 更新包降到 ~120 MB |
| 4 | 程序本体瘦身（`xiaoxiao.spec` 只留 win-x64、开 `upx`） | ⬜ 未开始 | ~30–60 MB |

第 3、4 步要改 `app/` 首次运行逻辑与发版流程，改动比前两步重。

## 二、在 Windows 上接上

### 0. 拿到分支（关键，别漏）

分支目前只在 Mac 上。先把它推到 GitHub，Windows 上：

```powershell
git fetch origin
git checkout feat/shrink-installer-1-webview2-bootstrap
git log --oneline -6      # 应看到 5 个提交，最新 5240ce4
```

### 1. 环境

Windows + Python 3.12 venv（见 `DEVELOPMENT.md`）。**额外装一个**（重采样要用）：

```powershell
.\.venv\Scripts\pip.exe install imageio-ffmpeg
```

### 2. 备份 24 kHz 母带（高规格版本，别删）

```powershell
# 各复制一份到 build/master-24k/
mkdir build\master-24k
Copy-Item -Recurse build\pack\Mandarin-CNTW-Xusheng_Chen build\master-24k\pack
Copy-Item -Recurse build\assistant-extra     build\master-24k\assistant-extra
Copy-Item -Recurse build\assistant-telemetry build\master-24k\assistant-telemetry
```

再把母带挂到 Release 备份（**仓库政策：大音频放 Release，不进源码仓库**）：

```powershell
Compress-Archive -Path build\assistant-extra     -DestinationPath LittleNavigator-AssistantExtra-24k-v1.4.1.zip
Compress-Archive -Path build\assistant-telemetry -DestinationPath LittleNavigator-AssistantTelemetry-24k-v1.4.1.zip
gh release create voice-master-24k-v1.4.1 LittleNavigator-AssistantExtra-24k-v1.4.1.zip LittleNavigator-AssistantTelemetry-24k-v1.4.1.zip `
  --repo xxushengcchen/LittleNavigator `
  --title "语音素材母带备份 24 kHz v1.4.1" `
  --notes "24 kHz 高规格母带，供派生 16 kHz 发行包；主包见 v1.4.1 的 VoicePack zip。"
```

（主包母带已经在 Release 里了：`LittleNavigator-iRacing-VoicePack-v1.4.1.zip`。）

### 3. 派生 16 kHz 发行包

**约定**：16 kHz 产物放 `build_dist.py` 读取的那三处；24 kHz 母带放 `build/master-24k/`。
三份各跑一次，源＝母带、目标＝发行位置：

```powershell
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/pack/Mandarin-CNTW-Xusheng_Chen --dst build/pack/Mandarin-CNTW-Xusheng_Chen
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/assistant-extra --dst build/assistant-extra
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/assistant-telemetry --dst build/assistant-telemetry
```

### 4. 验收

```powershell
Set-Location voice-assistant
..\.venv\Scripts\python.exe tools/audit_offline.py
```

应看到全部音频为 **16 kHz / 16-bit / 单声道**、0 错误、无缺失素材。跑完回到仓库根目录。

### 5. 下载 WebView2 引导器

从微软官网下 **Evergreen Bootstrapper**，存为：

```text
build\tooling\MicrosoftEdgeWebview2Setup.exe
```

（约 2 MB。构建脚本会校验它确实是微软签名，不是官网下的会直接失败。）

### 6. 出安装包

```powershell
.\.venv\Scripts\python.exe voice-assistant/packaging/build_installer.py --compiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

预期安装包从 **597 MB → ~360 MB**。产物与 `SHA256SUMS.txt` 在 `build/releases/v版本号/`。

### 7. 实测安装（本机做不到，只能在这里做）

- 正常机器（已带 WebView2）→ 应走快速路径、不弹任何东西。
- 再找一台**没装 WebView2** 的机器 → 验证引导器自动联网补装（这是本次唯一有实际行为变化的分支）。

## 三、已验证 / 未验证

- ✅ **重采样工具**用真 ffmpeg + 真母带跑过：主包 2,496 条全过，**197.6 → 131.8 MB（省 33%）**，耗时 5.9 秒；抽查时长差 0.0 ms、无损坏。
- ✅ 界面语音包标签改为读实际采样率；文档路径与 `build_dist.py` 读取处逐一对齐。
- ✅ WebView2 改动的 8 项静态断言全 PASS（无真实缺陷）。
- ❌ **安装包本身从未编译过**（Mac 上没有 Inno Setup）—— 第 6 步是第一次真编译，可能有构建期报错要处理。
- ❌ 实机安装、iRacing 内 16 kHz 语音包兼容性 —— 都还没测。

## 四、已定决策（别再翻案，除非有理由）

- 16 kHz 发行包落 `build/pack/…`、`build/assistant-extra`、`build/assistant-telemetry`；24 kHz 母带另存 `build/master-24k/…`。这样 `build_dist.py` 一行不用改。
- `scripts/build_pack.py` 的 `--rate` 默认**仍是 24000**（它产母带，刻意不改）；16 kHz 由 `resample_pack.py` 派生。
- 母带备份走 Releases，不进 git 仓库。
- 全离线版仍能出：`build_installer.py --webview-installer` 指回离线安装器即可（`.iss` 按传入文件名自动适配）。

## 五、坑 / 注意

- `gh` 的 owner 是 **`xxushengcchen`**（很容易多打一个 c 拼成 `xxushengccchen`，会报 `release not found`）。
- `installer.iss` 的 `[Code]` 里 WebView2 文件名由 `build_installer.py` 传的 `/DWebViewExe=<basename>` 决定，**不再硬编码**；直接手动跑 ISCC 记得带这个 define。
- 改一处规格（如采样率）时，记得全仓搜**用户可见**的硬编码文案（前端 `app/static/*.js`、文档），别只改代码注释——这次差点漏掉 `app.js` 里的 “24 kHz”。
