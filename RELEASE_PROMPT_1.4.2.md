# v1.4.2 发布执行说明（临时文档，合并进 main 前可删）

> 给 Windows 端 Claude Code 用。到 Windows 上 `git pull` 后，直接说：
> **「读 RELEASE_PROMPT_1.4.2.md，按里面的步骤帮我发布 1.4.2」** 即可，不用手抄。

---

请帮我在 Windows 上发布 LittleNavigator v1.4.2（安装包瘦身版）。我不太熟悉命令行，请你直接替我执行命令，并把每一步在做什么、结果是什么讲给我听。

## 背景

- 项目：当前目录的 xxushengcchen/LittleNavigator
- 分支：`feat/shrink-installer-1-webview2-bootstrap`
- 远端最新提交应为：`0f595d8`（其中 `40ca32c` 已把 VERSION 升到 1.4.2）
- 这次是"只改打包方式"的发布：WebView2 改微软官方引导器、安装包内语音改 16 kHz 派生版；功能与 v1.4.1 完全一致。
- 构建只能在 Windows 做（需要 Inno Setup）。

## 执行顺序

### 1. 同步代码

```powershell
git fetch origin
git checkout feat/shrink-installer-1-webview2-bootstrap
git pull --ff-only
git log --oneline -3
type voice-assistant\VERSION
```

确认两件事：`git log` 最上面是 `0f595d8`；`VERSION` 内容是 `1.4.2`。
只要有一个不符，立刻停下告诉我，不要继续。

### 2. 校验语音素材（非常重要，别用旧副本）

统计这三个目录里的 WAV 数量：

```powershell
(Get-ChildItem build\pack\Mandarin-CNTW-Xusheng_Chen\*.WAV).Count   # 应 2496
(Get-ChildItem build\assistant-extra\*.WAV).Count                   # 应 17
(Get-ChildItem build\assistant-telemetry\*.WAV).Count               # 应 561
```

三者相加必须是 **3074**。对不上就停下告诉我——那说明是旧的 2026-10-12 素材，绝对不能拿去出包。

### 3. 检查 WebView2 引导器

确认 `build\tooling\MicrosoftEdgeWebview2Setup.exe` 存在（约 1.8 MB）。
不存在就告诉我，我去微软官网下载，你不要自己找来源。

### 4. 出安装包

```powershell
.\.venv\Scripts\python.exe voice-assistant/packaging/build_installer.py --compiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

产物应在 `build\releases\v1.4.2\` 下：`LittleNavigator-1.4.2-Setup-x64.exe` 与 `SHA256SUMS.txt`。
预期体积约 **312.5 MiB**。把实际体积和 SHA-256 报给我，并核对：

```powershell
certutil -hashfile "build\releases\v1.4.2\LittleNavigator-1.4.2-Setup-x64.exe" SHA256
```

### 5. 冒烟测试

静默安装、启动、卸载（和上一轮一样）。确认程序界面里版本显示 **1.4.2**，且 `%LOCALAPPDATA%\LittleNavigator` 里的用户配置在升级/卸载后保留。

### 6. 发布（先跟我确认，再执行）

```powershell
$notes = @"
**功能与 v1.4.1 完全一致，只改打包方式**：安装包从 597 MB 降到约 313 MiB。

- WebView2 改用微软官方 Evergreen Bootstrapper（约 2 MB）：已有 WebView2 的电脑安装时不联网，缺少时才联网补装。
- 安装包内语音由 24 kHz 母带派生为 16 kHz，体积约省 1/3；独立语音包附件仍是 24 kHz 母带。
- 问答逻辑、界面、遥测与语音识别均未改动。

验证：132 项测试通过；851 msgId / 3,074 条音频全部 16 kHz·16-bit·单声道且无损坏；桌面窗口冒烟、静默安装／启动／卸载通过。

24 kHz 母带备份见 Releases 里的「语音素材母带备份 24 kHz」。
"@

gh release create v1.4.2 `
  "build\releases\v1.4.2\LittleNavigator-1.4.2-Setup-x64.exe" `
  "build\releases\v1.4.2\SHA256SUMS.txt" `
  --repo xxushengcchen/LittleNavigator `
  --title "小小领航员 v1.4.2 — 安装包瘦身版" `
  --notes $notes
```

注意 owner 是 `xxushengcchen`（很容易多打一个 c，会报 `release not found`）。
如果 `gh` 没装或没登录：**不要自己安装或登录**，停下来告诉我改用网页上传。

### 7. 发布后核对

- Releases 页面的 **Latest 应该是 v1.4.2**
- 资产里应有 exe 和 `SHA256SUMS.txt`
- 把发布页链接发给我

## 边界（请严格遵守）

- 不要修改或删除 `v1.4.1`、`v1.4.1-slim`、`voice-master-24k-v1.4.1` 这几个 release。
- 不要往 git 提交任何音频/模型等大文件。
- 不要合并到 `main`，也不要动 `main`。
- 任何一步出错，停下来告诉我，不要自己猜着改。
