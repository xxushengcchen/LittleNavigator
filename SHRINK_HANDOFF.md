# 安装包瘦身 —— 交接说明（临时文档，合并进 main 前可删）

> 2026-10-16 起在 macOS 上规划，2026-10-17 在 Windows 打包机上执行完成第 1、2 步。
> 所有改动都在分支 `feat/shrink-installer-1-webview2-bootstrap`（**未合并**）。

## 一、目标与结果

把 **597 MB** 的 Windows 安装包压小。四步：

| 步 | 内容 | 状态 | 结果 |
|---|---|---|---|
| 1 | WebView2 离线安装器(~180MB) → 微软官方引导器(~2MB) | ✅ 已完成 | 已出包验证 |
| 2 | 语音包 24 kHz → 16 kHz | ✅ 已完成 | 已出包验证 |
| 3 | 拆分下载（程序小包 + 素材包单独下载） | ⬜ 未开始 | 目标：更新包 ~120 MB |
| 4 | 程序本体瘦身（`xiaoxiao.spec` 只留 win-x64、开 `upx`） | ⬜ 未开始 | ~30–60 MB |

**实际结果：安装包 597 MB → 312.5 MiB（327,657,481 字节）**，SHA-256
`dad642769728919c10e153aa9d63703f2ee85d3a30efbdbb4bcae66794a95d0d`。

- 预发布：`v1.4.1-slim`（正式 `v1.4.1` 未动）
- 24 kHz 母带备份：`voice-master-24k-v1.4.1`（已设为 pre-release，避免抢 Latest）
- **分支上 `VERSION` 已升到 1.4.2**（提交 `40ca32c`），1.4.2 安装包**尚未构建**；转正发布前必须用 1.4.2 重出包。

## 二、已执行的步骤（Windows，留档）

### 1. 环境

Windows + Python 3.12 venv（见 `DEVELOPMENT.md`），另需 `pip install imageio-ffmpeg`（重采样用）。

### 2. 24 kHz 母带备份

- 本地另存 `build/master-24k/…`；`assistant-extra` / `assistant-telemetry` 已打成 zip 挂到 `voice-master-24k-v1.4.1`；主包母带仍是 `LittleNavigator-iRacing-VoicePack-v1.4.1.zip`。
- **权威素材必须来自公开 v1.4.1 安装包 / VoicePack**，见下方「坑」。

### 3. 派生 16 kHz 发行包

```powershell
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/pack/Mandarin-CNTW-Xusheng_Chen --dst build/pack/Mandarin-CNTW-Xusheng_Chen
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/assistant-extra --dst build/assistant-extra
.\.venv\Scripts\python.exe scripts/resample_pack.py --src build/master-24k/assistant-telemetry --dst build/assistant-telemetry
```

### 4. 验收

`tools/audit_offline.py`：851 msgId / 3,074 条全部 16 kHz·16-bit·单声道、0 错误、无缺失素材。
另有 132 项 unittest 全绿；WebView2 窗口冒烟测试通过；静默安装／启动／卸载通过，用户设置与日志保留。

### 5. WebView2 引导器

微软官网 **Evergreen Bootstrapper** → `build\tooling\MicrosoftEdgeWebview2Setup.exe`
（1,867,984 字节，微软签名校验通过）。

### 6. 出安装包

```powershell
.\.venv\Scripts\python.exe voice-assistant/packaging/build_installer.py --compiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

产物与 `SHA256SUMS.txt` 在 `build/releases/v版本号/`。

## 三、剩余待办

1. **无 WebView2 的干净 Win10/11 x64 机器或 VM**：确认引导器自动联网补装、程序能启动；再确认已装 WebView2 的机器不弹窗、不联网。
2. **iRacing 内加载 16 kHz 语音包**：进游戏实际听一次 spotter，确认兼容。
3. 以上通过后再决定发 1.4.2：
   - 用已升好的 `VERSION=1.4.2` 重出安装包；
   - `RELEASE_NOTES.md` / `README.md` 的 1.4.2 条目已写好；
   - 考虑是否补一个 16 kHz 独立 VoicePack（当前独立附件仍是 v1.4.1 的 24k 母带）。
4. 之后再做第 3、4 步（拆分下载、`xiaoxiao.spec` 瘦身）。

## 四、已定决策（别再翻案，除非有理由）

- 16 kHz 发行包落 `build/pack/…`、`build/assistant-extra`、`build/assistant-telemetry`；24 kHz 母带另存 `build/master-24k/…`。这样 `build_dist.py` 一行不用改。
- `scripts/build_pack.py` 的 `--rate` 默认**仍是 24000**（它产母带，刻意不改）；16 kHz 由 `resample_pack.py` 派生。
- 母带备份走 Releases，不进 git 仓库（仓库政策：不收录大音频）。
- 全离线版仍能出：`build_installer.py --webview-installer` 指回离线安装器即可（`.iss` 按传入文件名自动适配）。
- 独立语音包附件保持 24 kHz 母带；安装包内用 16 kHz 派生版。

## 五、坑 / 注意

- **旧素材陷阱（已踩过一次）**：Windows 工作区原来的 `build/pack` 是 2026-10-12 的旧副本，2,496 条 WAV 字节与公开 v1.4.1 全部不同，`assistant-telemetry` 还少 139 条。**不要把它当母带**；权威素材来自公开 v1.4.1 安装包 / VoicePack。正确组成 = 主包 2496 + 附加 17 + 遥测 561 = 3074。错包在 `build/stale-20261012`。
- **建“备份／素材”类 Release 一律带 `--prerelease`**，否则会抢走 GitHub 的 **Latest** 标记（已发生过一次：母带备份被当成最新发布）。
- `gh` 的 owner 是 **`xxushengcchen`**（很容易多打一个 c 拼成 `xxushengccchen`，会报 `release not found`）。
- `installer.iss` 的 `[Code]` 里 WebView2 文件名由 `build_installer.py` 传的 `/DWebViewExe=<basename>` 决定，**不再硬编码**；直接手动跑 ISCC 记得带这个 define。
- 改一处规格（如采样率）时，记得全仓搜**用户可见**的硬编码文案（前端 `app/static/*.js`、文档），别只改代码注释——这次差点漏掉 `app.js` 里的 “24 kHz”。
- 本机（macOS）没有 Inno Setup，**安装包只能在 Windows 上编译**。
