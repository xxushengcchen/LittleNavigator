# Windows 端交接 — v1.4.3 构建与发布

分支 `feat/ai-race-engineer`，最新提交见 `git log -1`。本文写给在 Windows 上
负责**构建安装包并发布**的人。读完就能动手。

配套阅读：

1. 本文（构建与发布）
2. [`HANDOFF.md`](HANDOFF.md)（红线与架构速查）
3. [`HANDOFF-MACOS.md`](HANDOFF-MACOS.md)（语音包重建细节，平台差异可忽略）
4. [`design/DESIGN.md`](design/DESIGN.md)（改 UI 前必读）

---

## 1. 这版要发什么

**v1.4.3** 加入三项新功能，全部**默认关闭**——不配置时规则型问答与 v1.4.2 完全一致。

| 功能 | 默认 | 玩家要做什么 |
|---|---|---|
| AI 大模型 | 关闭 | 填自己的 `base_url` / `model` / `api_key` |
| 语音合成 TTS | 关闭 | 填豆包或 OpenAI 兼容的 key |
| 语音改车 | 关闭 | 界面里打开开关 |

另外界面整体重做（蓝绿白三色 + 赛前检查总线 + 导航分组）。

**产品决策已定：改车一并发布。** 它不是灰度功能，是正式功能，文档里已写清
风险和使用建议。不要在任何地方把它标成「实验性」或隐藏。

---

## 2. 构建前必须确认

### 2.1 素材齐备

`build_dist.py` 会硬性校验这四样，缺一样直接报错：

```
models/sense-voice/model.int8.onnx
models/sense-voice/tokens.txt
build/pack/Mandarin-CNTW-Xusheng_Chen/spmsg.txt
build/assistant-extra/spmsg.txt
build/assistant-telemetry/spmsg.txt
```

本次两个助手包的规模：

| 包 | msgId | 音频 | 说明 |
|---|---|---|---|
| `assistant-extra` | 5 | 30 | 拼接连接件（还剩/圈/上一圈/前车距离） |
| `assistant-telemetry` | 117 | 627 | 回答话术、拒绝提示、改车确认 |

**如果这两个包还没重建**，按 [`HANDOFF-MACOS.md`](HANDOFF-MACOS.md) 第 4 节的
「路线 B 补充：三个包要分开建」做。注意 `synthesize_telemetry.py` 的输出目录
硬编码成 `assistant-telemetry`，用它建 `assistant-extra` 会覆盖 telemetry 的
`spmsg.txt`——必须先合成 mp3 缓存，再用 `build_pack.py --out/--name` 分别打包。

### 2.2 依赖

```powershell
pip install -r voice-assistant\requirements-tested.txt
pip install imageio-ffmpeg      # 实际使用但两个 requirements 都没列
```

`build_dist.py` 会 `import irsdk` 做存在性检查——缺了会直接失败，这是有意的，
防止带着模拟回退发布。

### 2.3 基线测试

```powershell
cd voice-assistant
python -m unittest discover -s tests
```

预期 **480 项通过，退出码 0**。三个语音包目录缺任何一个都会让
`test_driver_questions` 与 `test_regressions.ServiceTests` 的 `setUpClass`
失败，合计 26 个用例跑不起来。

---

## 3. 构建

```powershell
cd voice-assistant
python packaging\build_dist.py --clean --zip
```

产出在 `build\dist\` 下。脚本会：

- 用 PyInstaller 打包 exe
- 铺 `config.yaml`（路径改成相对的）、`使用说明.txt`、`intents_examples.yaml`
- 复制模型与三个语音包
- 可选压成 zip

然后出安装包（需要 Inno Setup）：

```powershell
python packaging\build_installer.py
```

### 构建后检查清单

- [ ] `VERSION` 文件内容是 `1.4.3`
- [ ] 安装包大小与 v1.4.2 相近（约 313 MiB；若明显变大先查是不是语音包重复铺了）
- [ ] 解开安装目录，确认 `使用说明.txt` 里含「三·五、想用 AI 大模型？」和
      「三·六、想用语音改车？」两节
- [ ] **确认 AI / 改车默认关闭**：打开 `config.yaml` 看 `ai.enabled` 与
      `car_control.enabled` 都是 `false`
- [ ] **确认没有把开发产物打进包**：`tools\ai_bench.py`、`tools\stress_test.py`、
      `docs\` 都不该出现在安装目录里（spec 只收 `app/static`，正常不会）

---

## 4. 发布

### 4.1 版本号与标签

```powershell
git tag v1.4.3
git push origin v1.4.3
```

### 4.2 Release 正文要点

必须讲清楚**新功能默认关闭**，否则会收到一堆「打开没看到 AI」的反馈。建议结构：

```
## 新增
- AI 大模型接驳（默认关闭，用自己的 API Key）
- 语音合成（默认关闭）
- 语音改车（默认关闭，可语音换胎/加油/快修）
- 界面重做

## 重要
这三个功能都需要你在「设置 → AI 与改车」里手动打开。
不配置的话，行为和 1.4.2 完全一样。

⚠️ 语音改车会真的改动游戏里的进站设置。语音识别可能听错，
   建议先在练习赛里试熟。

## 修复
- 某些情况下缺少提示音导致完全静默
- 「还剩几圈」这类拼装回答缺件
```

### 4.3 附件

- 安装包 `LittleNavigator-1.4.3-Setup-x64.exe`
- 可选：独立语音包（24 kHz 母带）

---

## 5. 这一版没做真机验证（重要）

以下三项**只在 macOS 上用无头 Chromium 和无 iRacing 的假 SDK 验过**，
真机没跑过。发布后要重点收集这类反馈：

1. **改车广播** —— 指令码、参数、回读 `PitSvFlags` 的逻辑都用假 SDK 测过，
   真游戏里的行为未知。玩家反馈「说了没反应」或「改错了」优先查这里。
2. **WebView2 渲染** —— 对比度、布局、溢出在 Chromium 里验过（九页零问题），
   但 WebView2 是不同引擎，中文字体和字号观感可能有差异。
   截图参考在 `docs/ui-preview/redesign/`。
3. **豆包 TTS 实际播放** —— 已修流式 WAV 头的长度字段（`0xFFFFFFFF` 占位会被
   解析成 37 小时音频），但没在真机上实听。

这三项**不阻塞发布**，属于已知风险。收集反馈时按这三类分桶。

---

## 6. 红线（不要破坏）

1. **绝不猜** —— 读不到就明说读不到，不许推测。
2. **安全类永不进模型** —— 解释处罚、操作车辆、问比赛外的事，
   `query_class.py` 在路由前拦住。改这条链路要格外小心。
3. **改车默认关闭** —— 发布时必须保持 `car_control.enabled: false`。
   三条约束：只走确定性词表、绝不经模型、发送后回读 `PitSvFlags` 验证。

改任何一条，跑 `python tools\ai_bench.py`，「拦截零漏放」必须仍然是 0。

---

## 7. 验收基准（改代码后必跑）

```powershell
$env:DEEPSEEK_KEY = "<你的 key>"
cd voice-assistant
python tools\ai_bench.py
python tools\stress_test.py
```

当前基线（macOS，2026-10-19 实测）：

```
误杀率            2.2%（门槛 ≤20%）
拦截漏放          红线 0 / 能力边界 0
改车解析          13 条可执行全对 / 19 条做不到全部拒绝
压力测试          90 条问句、零问题零崩溃、模型调用 13 次
延迟              p50 50ms / p90 1012ms / max 1708ms
```

两个工具都只从环境变量读密钥，不接受命令行参数（避免写进 shell 历史）。
它们**不进安装包**，只用于开发验收。

---

## 8. 已知待办（不阻塞本次发布）

- `AST_REFUSE_*` 若要扩充变体，走 `content/` → 合成 → 打包全流程
- `docs/HANDOFF*.md` 三个交接文档，合并进 main 前应删除
- 大模型接入后的端到端延迟还没做 p50/p95 的正式测量
  （`ai_bench` 测的是质量，不是延迟分布）

---

## 9. 密钥提醒

本文不包含任何真实密钥。**发布时不要复用任何历史密钥或示例值。**
构建脚本和配置文件里不应出现任何真实密钥——`config.yaml` 会随安装包分发，
密钥只能走 `config.local.yaml` 或环境变量 `LITTLE_NAVIGATOR_AI_KEY`。

发版前扫一遍：

```powershell
git grep -nE "sk-[A-Za-z0-9]{20,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
```
