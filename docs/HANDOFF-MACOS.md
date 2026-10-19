# macOS 接手说明 — AI 比赛工程师分支

写给在 macOS 上接手 `feat/ai-race-engineer` 的人。**先读完这一页再动手**，
尤其是「macOS 能做什么」——这台机器跑不了一半的验证，别把时间花在跑不通的路上。

配套阅读顺序：

1. 本文（平台边界 + 当前状态）
2. [`HANDOFF.md`](HANDOFF.md)（上一轮的通用交接，红线与架构速查）
3. [`design/DESIGN.md`](design/DESIGN.md)（**改 UI 前必读**）
4. [`superpowers/specs/2026-10-18-ai-race-engineer-design.md`](superpowers/specs/2026-10-18-ai-race-engineer-design.md)（AI 功能设计，20 章）

---

## 1. 当前状态

分支 `feat/ai-race-engineer`，最新提交 `8fe89cb`（2026-10-19，Windows 端推送）。

| 项目 | 状态 |
|---|---|
| 全量测试 | **480 项通过**，退出码 0 |
| 语音包 | **627 条**（v1.1 的 422 + v1.2 的 205），24 kHz / 16-bit / 单声道 |
| `ai_bench.py` | 误杀率 0.0%、拦截漏放 0/39 与 0/96、改车解析 13 全对 / 19 全拒 |
| `stress_test.py` | 90 条问句零问题零崩溃，模型调用 13 次 |

**注意：语音包不在 Git 里。** `build/` 被 `.gitignore` 排除，627 条音频需要自己重建
（见第 4 节）。代码在远端，音频不在——这是设计如此，不是漏推。

### 本轮修复了两个缺陷

1. **降级链落到空处**（`8fe89cb`）
   `AST_REFUSE_CANT` / `AST_REFUSE_HEARD` 此前只写在 `src/engine.py` 的常量里，
   `content/` 与语音包都没有这两条素材。`_say_with_fallback()` 的三级兜底
   后两级因此为空：用户语音包未重建时按 PTT **完全静默**。
   已在 `content/assistant-telemetry-v1.2.json` 补齐各 6 条变体，并新增
   `RefusalConstantTests` 比对代码常量与 content 定义。

2. **测试被本机环境带偏**（`8fe89cb`）
   `test_car_control` 的「无 SDK」用例，在装了 `pyirsdk` 的机器上会被真实 SDK
   顶替而误判为成功。已加 `no_irsdk()` 显式屏蔽 `sys.modules['irsdk']`。

---

## 2. macOS 能做什么（重要）

### ✅ 可以

| 事项 | 说明 |
|---|---|
| 跑全量测试 | 480 项全部是纯逻辑测试，不依赖 Windows |
| 跑 `ai_bench.py` / `stress_test.py` | 只需 `DEEPSEEK_KEY`，纯 HTTP 调用 |
| 改 AI 层代码 | `query_class` / `race_context` / `answer_checks` / `llm_engine` / `ai_learn` / `ai_pipeline` 都与平台无关 |
| 改界面 | `app/static/*` 是纯 HTML/CSS/JS，可用无头 Chromium 渲染验收 |
| 重建语音包 | 纯 ffmpeg + 文件读写，只要拿到音频素材 |
| 跑 `--sim` 模拟模式 | `SimTelemetry` 是内置假数据，跨平台 |

### ❌ 不能（或只能做一半）

| 事项 | 原因 |
|---|---|
| **构建 Windows 安装包** | 需要 Inno Setup，只能在 Windows 上出 `.exe` |
| **真机验证改车广播** | 依赖 iRacing，且只有 Windows 版游戏 |
| **真机验证 PTT 按键** | `listener.py:92` 用 `ctypes.windll.user32.GetAsyncKeyState`，Windows API |
| **真机验证遥测读取** | `telemetry.py:469-470` 用 `irsdk.IRSDK()` 读 iRacing 共享内存，Windows 实现 |
| **WebView2 渲染** | macOS 上 pywebview 走 WKWebView，**不是**同一渲染引擎，验不了。另注意 `desktop.py:151` 把 renderer 硬编码成 `'edgechromium'`，在 macOS 上跑出的冒烟结果不可信 |
| **真实音频输出** | `pycaw` / `comtypes` 是 Windows 专属（`requirements.txt` 已有平台标记，装不上也不影响测试） |

结论：**macOS 的定位是「代码与逻辑的开发者」，不是「发布者」，也不是「真机验收者」。**

---

## 3. 环境搭建

```bash
git clone https://github.com/xxushengcchen/LittleNavigator.git
cd LittleNavigator
git checkout feat/ai-race-engineer
git log --oneline -3          # 最上面应是 8fe89cb

python3.12 -m venv .venv
source .venv/bin/activate
```

**不要直接 `pip install -r voice-assistant/requirements-tested.txt`。**
那份是 Windows 实测版本清单，其中 `pycaw==20260927` 和 `comtypes==1.4.17`
**没有平台标记**，在 macOS 上会安装失败（对比：`requirements.txt` 里这两个
带 `sys_platform == 'win32'`）。

macOS 上的做法是跳过这两个 Windows 专属包：

```bash
grep -v -E '^(pycaw|comtypes)' voice-assistant/requirements-tested.txt > /tmp/req-macos.txt
pip install -r /tmp/req-macos.txt
```

另外 `imageio-ffmpeg` 是**实际使用但未声明**的依赖（`build_pack.py`、
`resample_pack.py`、`tts_doubao.py` 都 `import imageio_ffmpeg`）。
重建语音包之前需要补装：

```bash
pip install imageio-ffmpeg
```

`pycaw` / `comtypes` 缺席不影响测试，只影响真实音频输出（那也是 Windows 功能）。

### 跑基线

```bash
cd voice-assistant
python -m unittest discover -s tests
```

预期 **480 项通过**。

没有 `build/` 时会有 1–2 项跳过（依赖运行时光盘素材的用例），属正常。
若 `test_msgid_exists_in_pack` **失败**，说明文案有、音频没有——按第 4 节重建。

---

## 4. 重建语音包（拿到 627 条音频）

音频不进 Git，需要两条路之一：

### 路线 A：从安装包导入（推荐）

先下载并安装 Windows 发行版（Releases 里的 `LittleNavigator-1.4.2-Setup-x64.exe`），
再从这个安装目录导入：

```bash
python scripts/import_runtime_assets.py --from "<安装目录>"
```

脚本会复制 `models/`、`build/pack/Mandarin-CNTW-Xusheng_Chen/`、
`build/assistant-extra/`、`build/assistant-telemetry/` 四处，
不含用户配置、日志或状态。

**⚠️ 已知问题：发行版安装包里的音频是 16 kHz 派生版。**
v1.4.2 为缩小体积，把安装包内语音从 24 kHz 降采样到了 16 kHz
（`scripts/resample_pack.py`）。而开发用的语音包母带是 **24 kHz**
（本地实测 `AST_AI_SEE_SCREEN_01.WAV` = 24000 Hz / 16-bit / 单声道）。

所以走路线 A 拿到的 `build/assistant-telemetry/` 会是 16 kHz。
这对**跑测试没影响**（测试只检查文件存在与可读），
但如果要重新出安装包或做音质相关工作，应该走路线 B 拿 24 kHz 母带。

### 路线 B：用豆包 TTS 重新合成（拿到 24 kHz 母带）

```bash
export DOUBAO_API_KEY="<你的 key>"
python scripts/synthesize_telemetry.py \
  --content content/assistant-telemetry-v1.1.json \
            content/assistant-telemetry-v1.2.json
```

**注意**：

- 脚本会把 **627 条全部检查一遍**，但已有音频会命中缓存直接跳过
  （`build/telemetry-tts/*.mp3`，按 `msgId__序号.mp3` 命名）
- 全新环境没有缓存时会真的合成 627 条，耗时约 2 小时（Windows 实测），且消耗 TTS 额度
- 结束会自动调 `build_pack.py` 产出 `build/assistant-telemetry/`

### 重建后自检

```bash
# 1. WAV 总数应为 627
ls build/assistant-telemetry/*.WAV | wc -l

# 2. spmsg.txt 应为 635 行（1 行表头 + 634 行条目）
wc -l < build/assistant-telemetry/spmsg.txt

# 3. 关键 msgId 都要在，且各有 6 条变体
for id in AST_AI_SEE_SCREEN AST_NO_CAR_CONTROL AST_CC_TYRE \
          AST_CC_FUEL AST_CC_WS AST_CC_FR AST_CC_CLEAR \
          AST_CC_FAIL AST_CC_COMPOUND AST_REFUSE_CANT AST_REFUSE_HEARD; do
  printf "%-22s %s\n" "$id" "$(grep -c "^$id," build/assistant-telemetry/spmsg.txt)"
done

# 4. 采样率应为 24000（路线 B）或 16000（路线 A）
python -c "import wave;w=wave.open('build/assistant-telemetry/AST_AI_SEE_SCREEN_01.WAV');print(w.getframerate(),'Hz')"
```

---

## 5. 验收工具

两个工具都只从环境变量读密钥，**不接受命令行参数**（避免写进 shell 历史）。

```bash
export DEEPSEEK_KEY="sk-..."
cd voice-assistant
python tools/ai_bench.py       # 误杀率 / 拦截零漏放 / 改车解析
python tools/stress_test.py    # 90 条真实场景问句
```

**改完代码必须复跑，别让数字变差。** 当前基线：

```
误杀率            0.0%（门槛 ≤20%）
拦截漏放          红线 0 / 能力边界 0
改车解析          13 条可执行全对 / 19 条做不到全部拒绝
压力测试          90 条问句、零问题零崩溃
模型调用          13 次 / 90 条（C 档白名单设计）
```

---

## 6. 红线（不要破坏）

1. **绝不猜** —— 读不到就明说读不到，不许推测。全项目底线。
2. **安全类永不进模型** —— 解释处罚、操作车辆、问比赛外的事，
   `query_class.py` 在路由前拦住。改这条链路要格外小心。
3. **改车默认关闭** —— `car_control` 会真的改游戏进站设置。三条约束必须保持：
   只走确定性词表、绝不经模型、发送后回读 `PitSvFlags` 验证。

改任何一条，`ai_bench.py` 的「拦截零漏放」必须仍然是 0。

---

## 7. 未验证项（留给 Windows 真机）

Windows 端这一轮也没能验完，需要在有 iRacing 的机器上做：

1. **改车广播** —— 指令码、参数、回读逻辑都只用假 SDK 测过。
   建议先练习赛试熟，注意它真的会改游戏设置。
2. **WebView2 渲染** —— 只在 macOS 用无头 Chromium 验过对比度、布局、溢出。
   中文字体与字号观感需 Windows 上目视确认。截图在 `docs/ui-preview/redesign/`。
3. **豆包 TTS 实际播放** —— 返回的 WAV 头是流式占位（长度字段 `0xFFFFFFFF`），
   已用 `tts_engine.fix_streaming_wav()` 修正，但没在真机上实听。

---

## 8. 架构速查

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

---

## 9. 已知待办

- `docs/HANDOFF.md` 与本文里的临时说明文档，合并进 `main` 前应删除
- `AST_REFUSE_*` 现在各 6 条，若要扩充变体记得走 `content/` → 合成 → 打包全流程
- 真机三项（第 7 节）
- 大模型接入后的实际延迟测量（当前 `ai_bench` 只测质量，没测端到端 p50/p95）

---

## 10. 密钥提醒

开发机上的豆包、DeepSeek 密钥曾出现在对话记录中，接手时**请使用自己的密钥**，
不要复用上文任何示例值（本文已刻意不写任何真实密钥）。
