# 安全策略

## 报告安全漏洞

请通过 GitHub 的**私密安全公告**报告：仓库页面 **Security → Advisories → Report a vulnerability**（本仓库已开启私密漏洞报告）。不要在公开 Issue 中粘贴密钥、账号、日志或可复现的攻击细节。

## 密钥与配置

- 本仓库不包含任何服务商的真实密钥。`config.yaml` 会随安装包分发，只放默认值。
- 用户自己的密钥保存在本机 `%LOCALAPPDATA%\LittleNavigator\config.local.yaml`，或通过环境变量 `LITTLE_NAVIGATOR_AI_KEY` 提供；不要上传、截图或粘贴到 Issue / PR。
- 如果怀疑密钥已泄露：先在服务商控制台**撤销旧密钥**，再生成新密钥并重新保存。只删除本地文件不足以止损。

## 安装包完整性

- 安装包当前**未代码签名**，Windows 会提示未知发布者。
- 请从 [Releases](https://github.com/xxushengcchen/LittleNavigator/releases/latest) 下载，并用发布页附带的 `SHA256SUMS.txt` 核对 SHA-256 后再安装。具体命令见 README 的「下载与安装」一节。

## 自动化检查

- GitHub Secret Scanning 与 push protection 已启用。
- GitHub Actions（`.github/workflows/secret-scan.yml`）在每次 push / PR 时对**全部 git 历史**运行 gitleaks 与项目自定义密钥规则，并每周兜底扫描一次。
- 本地可选安装 pre-commit（见 `.pre-commit-config.yaml`），在提交前拦截密钥与本地专用路径。
