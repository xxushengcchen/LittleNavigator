# 交接说明 — AI 比赛工程师 / 语音改车 / 界面重做

分支 `feat/ai-race-engineer`，领先 `main` 39 个提交。**真机验证通过前不要合并。**

## 1. 建立基线

```powershell
git fetch origin
git checkout feat/ai-race-engineer
cd voice-assistant
python -m unittest discover -s tests
```

预期：**480 项通过**（2026-10-19 实测；此前写 453 项已过时）。

本机装了 `pyirsdk` 时，`test_car_control` 的「无 SDK」用例会被真实 SDK 顶替而误判。
测试已用 `no_irsdk()` 上下文显式屏蔽 `sys.modules['irsdk']`，不再受环境影响。

若出现 1 项跳过，是 `test_pack_audio_is_readable` 之类依赖运行时光碟素材的用例，
本机没有 `build/` 时跳过属正常。

若 `test_msgid_exists_in_pack` 失败：
说明文案已加但音频未合成，按第 3 节重跑合成与打包。

## 2. 配置大模型

密钥**绝对不能**写进 `voice-assistant/config.yaml`——该文件随安装包分发。

两条合法路径：
- 写进同目录的 `config.local.yaml`（已被 .gitignore 覆盖）
- 设环境变量 `LITTLE_NAVIGATOR_AI_KEY`（优先级更高）

```yaml
# config.local.yaml
ai:
  enabled: true
  mode: "whitelist"     # whitelist | analysis_key | full
  base_url: "https://api.deepseek.com"
  api_key: "sk-..."
  model: "deepseek-chat"
```

`mode` 三档：
- `whitelist`（默认，最省）：只对白名单意图与分析型问题调模型
- `analysis_key`：按住专用分析键才走 AI
- `full`：规则答不上就接管（最智能、最慢、最贵）

## 3. 重建语音包

新增了 8 条话术素材（`AST_CC_*` / `AST_NO_CAR_CONTROL` / `AST_CC_COMPOUND`）。

```powershell
$env:DOUBAO_API_KEY = "<key>"
python scripts\synthesize_telemetry.py --content content\assistant-telemetry-v1.2.json
```

**没重建也不会崩**：代码有三級降级链，缺素材时自动退到旧话术。

## 4. 必读文档

| 文件 | 内容 |
|---|---|
| `docs/design/DESIGN.md` | 界面设计系统：调色板、字阶、六条硬规则、反面清单 |
| `docs/superpowers/specs/2026-10-18-ai-race-engineer-design.md` | AI 功能设计（20 章） |
| `docs/superpowers/plans/2026-10-18-ai-race-engineer.md` | 18 个任务的实施计划 |

改 UI 前必须读 `DESIGN.md`。

## 5. 红线（不要破坏）

1. **绝不猜** — 读不到就明说读不到，不许推测。全项目底线。
2. **安全类永不进模型** — 解释处罚、操作车辆、问比赛外的事，
   `query_class.py` 在路由前拦住。动这条链路要格外小心。
3. **改车默认关闭** — `car_control` 会通过 iRacing 广播真的改游戏进站设置。
   三条约束必须保持：只走确定性词表、绝不经模型、发送后回读 `PitSvFlags` 验证。

## 6. 未在真机验证的部分（优先验这些）

1. **改车广播** — `pit_command` 在真游戏里的行为。指令码、参数、回读逻辑
   都用假 SDK 全测过，真机没跑过。建议先练习赛试熟。
2. **WebView2 界面渲染** — 只在 macOS 用无头 Chromium 验证了对比度、
   布局、溢出；字体与中文字号观感需目视确认。
   截图在 `docs/ui-preview/redesign/`。
3. **豆包 TTS 音频能否被 player 播放** — 返回 WAV 头是流式占位
   （长度字段 `0xFFFFFFFF`），已用 `tts_engine.fix_streaming_wav()` 修正，
   真机播放未验。

## 7. 验收工具

```powershell
python tools/ai_bench.py      # 基准：误杀率、拦截零漏放、改车解析
python tools/stress_test.py   # 90 条真实场景问句
```

两个都需要 `DEEPSEEK_KEY` 环境变量。改完代码后跑，别让数字变差。

当前基线：
```
误杀率            0.0%（门槛 ≤20%）
拦截漏放          红线 0 / 能力边界 0
改车解析          13 条可执行全对 / 19 条做不到全部拒绝
压力测试          90 条问句、零问题零崩溃
延迟              p50 ~1.0s / max ~1.6s
```

## 8. 已知待办

已完成（2026-10-19）：
- ✅ 完整语音包重建：615 条（v1.1 422 + v1.2 193）全部合成，0 失败
- ✅ `AST_REFUSE_HEARD` / `AST_REFUSE_CANT` 各补 6 条变体，已合成入包
      （此前这两个 msgId **只存在于代码常量里**，content 与语音包都没有，
       导致 `_say_with_fallback` 的后两级兜底为空、缺素材时静默）
- ✅ 全量测试 480 项通过；`ai_bench` 与 `stress_test` 复跑数字未变差

仍未验证（需真机）：
- 改车广播在真实游戏里的行为（指令码/参数/回读都只用假 SDK 测过）
- WebView2 渲染的中文字体观感（macOS 无头 Chromium 只能验对比度与布局）
- 豆包 TTS 返回音频的真机播放（`fix_streaming_wav()` 已修长度字段，未实听）

## 9. 架构速查

```
src/
  ai_constants.py    硬上限钳制（超时/token/字数）
  query_class.py     问题分类：SAFETY/OPERATION/ANALYSIS/KNOWN/UNKNOWN
  race_context.py    上下文组装，按问题分层，token 裁剪
  answer_checks.py   三道校验闸：长度/字段白名单/数字比对
  llm_engine.py      模型调用：硬截止、单飞、熔断
  tts_engine.py      TTS 双适配器（豆包/OpenAI 兼容）
  ai_learn.py        学习库：条件模板安全求值（不用 eval）
  ai_pipeline.py     编排层：档位矩阵、学习库优先
  car_control.py     改车执行器：广播消息 + 回读验证
  command_match.py   改车指令解析（确定性，不经模型）
  engine.py          Assistant：ask() 规则层 / ask_ai() 模型层（不持锁）
```

关键设计：`ask()` 持 `@exclusive` 锁走规则层；`ask_ai()` **不持锁**，
进入前把 `busy` 置假——否则等模型的 1–2 秒里麦克风是聋的。
`test_engine_ai.py::test_ask_ai_clears_busy_while_waiting` 钉住了这条。
