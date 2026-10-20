# 发给 Windows 端 agent 的提示词

下面整段可以复制给 Windows 那边的 agent。它假设对方已经能访问仓库。

---

接手 LittleNavigator 的 **v1.4.3 构建与发布**工作。

```powershell
git fetch origin
git checkout feat/ai-race-engineer
git pull --ff-only
git log --oneline -3
```

**先完整读 `docs/HANDOFF-WINDOWS.md`**，那是本次任务的操作手册，里面写清了
构建前置检查、命令、发版步骤和验收基线。配套再读：

- `docs/HANDOFF.md` —— 红线与架构速查
- `docs/CHANGELOG.md`（实际路径 `voice-assistant/CHANGELOG.md`）—— v1.4.3 改了什么
- `docs/design/DESIGN.md` —— 改 UI 前必读

## 你的任务

构建 v1.4.3 安装包并发布到 GitHub Releases。

## 关键前提（别搞错）

1. **三项新功能默认关闭**：AI 大模型、语音合成、语音改车。发布时必须保持
   `ai.enabled: false` 和 `car_control.enabled: false`。不配置的话行为和
   1.4.2 完全一致——这一点要在 Release 正文里讲清楚，否则会收到一堆
   「打开没看到 AI」的反馈。

2. **改车是正式功能，不是实验品**。产品决策已定，一并发布。
   文档里已写清风险和使用建议，不要在任何地方标成「实验性」或隐藏入口。

3. **语音包不在 Git 里**。`build/` 被 `.gitignore` 排除，需要自己重建或
   从已有素材导入。三件套都要齐：
   - `build/pack/Mandarin-CNTW-Xusheng_Chen/` —— 主包，2496 条
   - `build/assistant-extra/` —— 拼接件，30 条
   - `build/assistant-telemetry/` —— 助手话术，627 条

   重建方法见 `HANDOFF-MACOS.md` 第 4 节。**注意坑**：
   `synthesize_telemetry.py` 的输出目录硬编码成 `assistant-telemetry`，
   用它建 extra 包会覆盖 telemetry 的 `spmsg.txt`。正确做法是先合成 mp3 缓存，
   再用 `build_pack.py --out/--name` 分别打包。

## 环境

```powershell
pip install -r voice-assistant\requirements-tested.txt
pip install imageio-ffmpeg      # 实际使用但两个 requirements 都没列
```

出安装包需要 Inno Setup。

## 基线

改任何代码之前先跑一遍，确认起点干净：

```powershell
cd voice-assistant
python -m unittest discover -s tests
```

预期 **480 项通过，退出码 0**。三个语音包缺任何一个都会让 26 个用例跑不起来。

## 构建

```powershell
cd voice-assistant
python packaging\build_dist.py --clean --zip
python packaging\build_installer.py
```

## 构建后必须自查

- [ ] `VERSION` 是 `1.4.3`
- [ ] 安装包约 313 MiB（明显变大要查是不是语音包重复铺了）
- [ ] 解开安装目录，`使用说明.txt` 里含「三·五、想用 AI 大模型？」
      和「三·六、想用语音改车？」两节
- [ ] `config.yaml` 里 `ai.enabled` 与 `car_control.enabled` 都是 `false`
- [ ] `tools\ai_bench.py`、`tools\stress_test.py`、`docs\` 都不在安装目录里

## 发版

```powershell
git tag v1.4.3
git push origin v1.4.3
```

Release 正文按 `HANDOFF-WINDOWS.md` 第 4.2 节的要点写，**必须**包含
「新功能默认关闭」和改车的风险提示。

## 红线

1. **绝不猜** —— 读不到就明说读不到，不许推测
2. **安全类永不进模型** —— 解释处罚、操作车辆、比赛外问题在路由前拦住
3. **改车默认关闭** —— 只走确定性词表、绝不经模型、发送后回读 `PitSvFlags` 验证

改任何一条，跑 `python tools\ai_bench.py`，「拦截零漏放」必须仍是 0。

## 这一版没做真机验证

改车广播、WebView2 渲染、豆包 TTS 实际播放——这三项只在 macOS 上用假 SDK
和无头 Chromium 验过。**不阻塞发布，属于已知风险**。发版后收集反馈时按这三类分桶。

## 不要做的事

- 不要合并到 `main`，也不要动 `main`
- 不要往 Git 提交音频、模型等大文件
- 不要在 `config.yaml` 或构建脚本里写真实密钥（那个文件随安装包分发）

## 密钥

请使用你自己的密钥，不要复用任何历史示例值。
发版前扫一遍：

```powershell
git grep -nE "sk-[A-Za-z0-9]{20,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
```

---

## 如果它问起参考效果

截图在 `docs/ui-preview/redesign/`（11 张，含 1080/1440 两档宽度和桌面外壳），
`before-redesign/` 是改版前的样子。

界面重做的设计依据在 `docs/design/DESIGN.md`，含调色板、字阶、六条硬规则
和「这个项目已经踩过的反面」。
