# 交接说明 — AI 比赛工程师 / 语音改车 / 界面重做

分支 `feat/ai-race-engineer`，领先 `main` 39 个提交。**真机验证通过前不要合并。**

## 1. 建立基线

```powershell
git fetch origin
git checkout feat/ai-race-engineer
cd voice-assistant
python -m unittest discover -s tests
```

预期：453 项通过、2 项跳过。

若出现 2 个 `setUpClass` error 提到 `build/pack/.../spmsg.txt`：
说明本地没有构建产物，先跑语音包构建（见第 3 节）。

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

- 完整语音包重建（本机无 `build/` 目录，未跑全量）
- `AST_REFUSE_HEARD` / `AST_REFUSE_CANT` 各补 3 条变体到 6 条
  （见 `voice-assistant/CHANGELOG.md` 待办）
- 真机端到端：改车、TTS 播放、WebView2 渲染

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
