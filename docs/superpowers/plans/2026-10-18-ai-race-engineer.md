# AI 比赛工程师 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让小小领航员在比赛中接受任何问法，用用户自接的大模型基于遥测与近期圈史生成分析性回答，带秒级硬截止、防幻觉校验与赛后学习复用。

**Architecture:** 在现有「短语库 → 路由 → 录音」链路之外并列新增 AI 路径。问题先过 `query_class` 分类预检（纯文本，先于意图识别），据档位决定是否转交模型；模型调用独立于 `Assistant.ask()` 的锁，带硬截止与单飞；回答经三道校验闸后才显示；可选 TTS 合成；赛后复盘确认的问答沉淀进学习库，下次优先复用模板而非再调模型。

**Tech Stack:** Python 3.12 + PyInstaller（Windows 打包）、`urllib.request`（零新依赖）、`unittest`、现有 `pyirsdk` / `sherpa-onnx` / `sounddevice` 栈。

**Spec:** `docs/superpowers/specs/2026-10-18-ai-race-engineer-design.md`

## Global Constraints

- **延迟硬上限**：`LLM_DEADLINE_HARD_MAX = 2.0`、`TTS_DEADLINE_HARD_MAX = 1.5`、`AI_TOTAL_DEADLINE_HARD_MAX = 3.5`（秒）。配置文件只能调小。
- **Token 硬上限**：`MAX_INPUT_TOKENS_HARD = 1500`、`MAX_OUTPUT_TOKENS_HARD = 300`。
- **长度闸硬上限**：`MAX_ANSWER_CHARS_HARD = 60`。
- **单飞**：`SINGLE_INFLIGHT = True`，同一时刻只允许一个 AI 请求。
- **安全红线**：`SAFETY` 类问题在任何档位下都**不得**调用模型。
- **规则层优先**：规则层能答的问题永远不走 AI 路径；学习库永远不覆盖规则层已成功的回答。
- **零新依赖**：HTTP 用 `urllib.request`；不引入 `httpx` / `openai` / `requests`。
- **密钥纪律**：不进 `config.yaml`、不进日志、不进界面回显、不进 `export_logs()` 的 zip。
- **不缓存带动态数值的成品答案**：动态型只缓存条件化模板。
- **测试框架**：`unittest`，在 `voice-assistant/` 目录下运行 `python -m unittest discover -s tests -v`。
- **现有 132 项测试必须保持全绿**，任何改动不得破坏既有断言。

## Review Focus

以下五类输入/条件最可能伤到用户，且不是随便一个测试就能覆盖：

1. **ASR 听错的问法**（同音字、漏字）——分类预检与学习库模糊匹配都可能误判，用户会听到答非所问或张冠李戴。
2. **遥测字段为 None / 未连接 / 在车库**——`race_context` 组装与模板填数必须优雅降级，不能抛异常或填入 `None`。
3. **用户接入极慢或返回非 JSON 的模型**——必须按硬截止砍掉并给出明确提示，不能让界面卡死或麦克风变聋。
4. **安全类问句的变体写法**（「能不能帮我调一下尾翼」「这个处罚凭什么」）——任何档位下都不得触发模型调用。
5. **学习库模板引用越界字段或推不出的结论**——必须拒绝入库，否则下次复用会播报错误数据。

每一条的测试都钉在拥有相关代码的任务里，见各任务步骤。

---

## 文件结构

| 文件                                          | 责任                                            |
| ------------------------------------------- | --------------------------------------------- |
| `voice-assistant/src/ai_constants.py`（新建）   | 所有硬上限常量与钳制函数，单一来源                             |
| `voice-assistant/src/query_class.py`（新建）    | 问题分类预检（`SAFETY`/`ANALYSIS`/`KNOWN`/`UNKNOWN`） |
| `voice-assistant/src/race_context.py`（新建）   | 从 Snapshot + 圈记录组装紧凑上下文，含派生值预计算               |
| `voice-assistant/src/llm_engine.py`（新建）     | OpenAI 兼容调用、硬截止、单飞、自适应降级                      |
| `voice-assistant/src/answer_checks.py`（新建）  | 三道校验闸（长度/数据/数字）                               |
| `voice-assistant/src/tts_engine.py`（新建）     | 豆包 + OpenAI 兼容双适配器                            |
| `voice-assistant/src/ai_learn.py`（新建）       | 学习库：知识型缓存 + 动态型模板 + 权重                        |
| `voice-assistant/src/ai_pipeline.py`（新建）    | 编排：分类 → 档位判断 → 学习库 → 模型 → 校验 → TTS            |
| `voice-assistant/src/telemetry.py`（修改）      | `DerivedTracker` 扩展圈记录；`SimTelemetry` 可选圈推进   |
| `voice-assistant/src/config.py`（修改）         | `ai` 配置段、钳制、`ai_learn` 路径重定向                  |
| `voice-assistant/src/engine.py`（修改）         | `ask()` 接入 AI 路径；模型调用移出锁                      |
| `voice-assistant/src/listener.py`（修改）       | 分析键第二输入源                                      |
| `voice-assistant/app/server.py`（修改）         | AI 状态、配置接口、复盘接口、密钥隔离                          |
| `voice-assistant/app/static/app.js`（修改）     | AI 设置页 + 复盘区块 + 主面板只读显示                       |
| `voice-assistant/app/static/index.html`（修改） | 新增 nav 按钮与容器                                  |
| `voice-assistant/config.yaml`（修改）           | 新增 `ai:` 段（无密钥）                               |
| `content/assistant-telemetry-v1.2.json`（修改） | 新增 `AST_AI_SEE_SCREEN` 文案                     |
| `voice-assistant/tests/test_ai_*.py`（新建多个）  | 各模块测试                                         |

**分层原则**：`ai_constants` 无依赖；`query_class`、`answer_checks` 纯函数无外部依赖；`race_context` 依赖 `telemetry`；`llm_engine`、`tts_engine` 只依赖标准库 + `ai_constants`；`ai_learn` 依赖文件系统；`ai_pipeline` 编排以上；`engine` 只调用 `ai_pipeline`。

---

## Task 1: 硬上限常量与钳制

**Files:**

- Create: `voice-assistant/src/ai_constants.py`
- Test: `voice-assistant/tests/test_ai_constants.py`

**Interfaces:**

- [ ] Consumes: 无
- [ ] Produces:
  - 常量：`LLM_DEADLINE_HARD_MAX = 2.0`、`TTS_DEADLINE_HARD_MAX = 1.5`、`AI_TOTAL_DEADLINE_HARD_MAX = 3.5`、`MAX_INPUT_TOKENS_HARD = 1500`、`MAX_OUTPUT_TOKENS_HARD = 300`、`MAX_ANSWER_CHARS_HARD = 60`、`SINGLE_INFLIGHT = True`
  - `clamp(value: float, hard_max: float, name: str) -> float`：返回值不超过 `hard_max`，被钳制时写 `logging.warning`
  - `clamp_int(value: int, hard_max: int, name: str) -> int`：同上，整数版
- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_constants.py
import unittest
from src import ai_constants as c

class ClampTests(unittest.TestCase):
    def test_values_under_max_pass_through(self):
        self.assertEqual(c.clamp(1.0, 2.0, "llm_timeout"), 1.0)

    def test_values_over_max_are_clamped(self):
        self.assertEqual(c.clamp(10.0, 2.0, "llm_timeout"), 2.0)

    def test_clamp_int_over_max(self):
        self.assertEqual(c.clamp_int(200, 60, "max_answer_chars"), 60)

    def test_hard_constants_match_spec(self):
        self.assertEqual(c.LLM_DEADLINE_HARD_MAX, 2.0)
        self.assertEqual(c.TTS_DEADLINE_HARD_MAX, 1.5)
        self.assertEqual(c.AI_TOTAL_DEADLINE_HARD_MAX, 3.5)
        self.assertEqual(c.MAX_ANSWER_CHARS_HARD, 60)
        self.assertTrue(c.SINGLE_INFLIGHT)

    def test_clamp_logs_a_warning(self):
        with self.assertLogs("src.ai_constants", level="WARNING"):
            c.clamp(10.0, 2.0, "llm_timeout")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_ai_constants -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'src.ai_constants'`

- [ ] **Step 3: Implement `voice-assistant/src/ai_constants.py`**

模块级 `logger = logging.getLogger(__name__)`；两个钳制函数在 `value > hard_max` 时 `logger.warning("配置 %s=%s 超过硬上限 %s，已钳制", name, value, hard_max)` 并返回 `hard_max`。常量按 Interfaces 定义。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_ai_constants -v`  
Expected: PASS（5 tests）

- [ ] **Step 5: Commit**

```bash
git add voice-assistant/src/ai_constants.py voice-assistant/tests/test_ai_constants.py
git commit -m "feat(ai): 硬上限常量与配置钳制"
```

---

## Task 2: 问题分类预检

**Files:**

- Create: `voice-assistant/src/query_class.py`
- Test: `voice-assistant/tests/test_query_class.py`

**Interfaces:**

- [ ] Consumes: 无（纯函数，不依赖 `router` / `telemetry`）
- [ ] Produces:
  - `class QueryKind(str, Enum)`：`SAFETY`、`ANALYSIS`、`KNOWN`、`UNKNOWN`
  - `classify(text: str, intent: str | None) -> QueryKind`
  - `SAFETY_WORDS: tuple[str, ...]`、`ANALYSIS_WORDS: tuple[str, ...]`（模块级，供测试与后续扩充）
  - 判定顺序固定：`SAFETY` → `ANALYSIS` → `KNOWN`（`intent` 非 None）→ `UNKNOWN`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_query_class.py
import unittest
from src.query_class import QueryKind, classify

class SafetyPriorityTests(unittest.TestCase):
    def test_penalty_reason_is_safety_not_analysis(self):
        # 含「为什么」，但属安全类，必须判 SAFETY
        self.assertIs(classify("为什么我被罚了", "query_flags"), QueryKind.SAFETY)

    def test_car_control_is_safety(self):
        self.assertIs(classify("帮我把尾翼调高两格", None), QueryKind.SAFETY)

    def test_out_of_scope_is_safety(self):
        self.assertIs(classify("现在几点了", None), QueryKind.SAFETY)

class AnalysisTests(unittest.TestCase):
    def test_why_slow_is_analysis_even_without_intent(self):
        self.assertIs(classify("刚才那个弯为什么慢了0.3秒", None), QueryKind.ANALYSIS)

    def test_analysis_wins_over_known_intent(self):
        self.assertIs(classify("我为什么进弯推头", "query_laptime"), QueryKind.ANALYSIS)

class KnownAndUnknownTests(unittest.TestCase):
    def test_known_intent_without_analysis_words(self):
        self.assertIs(classify("油还够吗", "query_fuel"), QueryKind.KNOWN)

    def test_no_intent_no_analysis_words(self):
        self.assertIs(classify("今天天气不错", None), QueryKind.UNKNOWN)

    def test_intent_present_but_garbage_text(self):
        # 即使 intent 命中，含分析词仍走 ANALYSIS（已在上面覆盖）；
        # 这里验证纯 KNOWN 分支
        self.assertIs(classify("现在第几名", "query_position"), QueryKind.KNOWN)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_query_class -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `voice-assistant/src/query_class.py`**

`SAFETY_WORDS` 至少覆盖：`处罚`、`被罚`、`怎么判`、`替我`、`帮我开`、`帮我关`、`调高`、`调低`、`调到`、`改成`、`设置为`、`几点`、`几号`、`星期几`、`库存`、`备用胎`。注意「怎么开」歧义大，**不放入 SAFETY**（归入 `UNKNOWN`，不转模型也不误杀）。

`ANALYSIS_WORDS` 至少覆盖：`为什么`、`什么原因`、`分析`、`评价`、`怎么看`、`哪里慢`、`慢在哪`、`稳定吗`、`有没有进步`、`该怎么跑`、`策略`。

`classify` 先用 `normalize`（复用 `src.phrase_match.normalize`）清洗文本，再按顺序判定。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_query_class -v`  
Expected: PASS（8 tests）

- [ ] **Step 5: Add Review Focus #4 test（安全类变体写法）**

在 `tests/test_query_class.py` 追加：

```python
class SafetyVariantTests(unittest.TestCase):
    def test_soft_phrasing_of_car_control_is_still_safety(self):
        for text in ("能不能帮我调一下尾翼", "这个处罚凭什么", "帮我把TC关掉"):
            with self.subTest(text=text):
                self.assertIs(classify(text, None), QueryKind.SAFETY)
```

Run: `cd voice-assistant && python -m unittest tests.test_query_class -v`  
Expected: PASS（11 tests）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/query_class.py voice-assistant/tests/test_query_class.py
git commit -m "feat(ai): 问题分类预检，SAFETY 优先于 ANALYSIS"
```

---

## Task 3: `DerivedTracker` 圈记录扩展

**Files:**

- Modify: `voice-assistant/src/telemetry.py`（`DerivedTracker` 类，约 182-286 行）
- Test: `voice-assistant/tests/test_lap_records.py`

**Interfaces:**

- [ ] Consumes: 现有 `Snapshot`
- [ ] Produces:
  - `DerivedTracker.lap_records: list[dict]`（最多 20 条，最近的在后）
  - `DerivedTracker.valid_lap: bool`（公共属性，替代私有 `_valid_lap` 的读取）
  - `DerivedTracker.record_lap(summary: dict, trace=None) -> None`
  - 圈记录字段见 spec 第七节
  - `DERIVED_VALUES` 中新增的派生量：`laps_spare`、`delta_to_best`、`avg3`、`trend_per_lap`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_lap_records.py
import unittest
from src.telemetry import DerivedTracker, Snapshot, FLAG_BITS

def snap(**kw):
    base = dict(connected=True, source="test", session_id=(1, 0, 7),
                session_time=0.0, lap=1, lap_completed=0, fuel_level=30.0,
                on_pit_road=False, in_garage=False,
                session_flags=FLAG_BITS["green"], position=4)
    base.update(kw)
    return Snapshot(**base)

class LapRecordTests(unittest.TestCase):
    def test_new_lap_appends_a_record(self):
        t = DerivedTracker()
        t.update(snap(lap=1, session_time=0.0, fuel_level=30.0))
        t.update(snap(lap=2, session_time=83.5, fuel_level=28.6, lap_completed=1))
        self.assertEqual(len(t.lap_records), 1)
        self.assertEqual(t.lap_records[0]["lap"], 1)

    def test_records_keep_only_last_twenty(self):
        t = DerivedTracker()
        for i in range(25):
            t.update(snap(lap=i + 1, session_time=i * 80.0,
                          fuel_level=30.0 - i * 1.4, lap_completed=i))
        self.assertEqual(len(t.lap_records), 20)

    def test_pit_lap_records_lap_but_no_fuel_use(self):
        t = DerivedTracker()
        t.update(snap(lap=1, fuel_level=30.0))
        t.update(snap(lap=2, fuel_level=28.6, lap_completed=1, on_pit_road=True))
        self.assertTrue(t.lap_records)             # 圈记录仍写入
        self.assertIsNone(t.lap_records[0]["fuel_used"])   # 但油耗不可用

    def test_session_reset_clears_records(self):
        t = DerivedTracker()
        t.update(snap(lap=1, session_id=(1, 0, 7), fuel_level=30.0))
        t.update(snap(lap=2, session_id=(1, 0, 7), fuel_level=28.6, lap_completed=1))
        self.assertEqual(len(t.lap_records), 1)
        t.update(snap(lap=1, session_id=(2, 0, 7), fuel_level=30.0))
        self.assertEqual(len(t.lap_records), 0)

    def test_record_is_written_after_gaps_are_derived(self):
        # 圈末记录必须带上该帧算出的 gap，而不是上一帧的
        t = DerivedTracker()
        s = snap(lap=1, fuel_level=30.0, car_idx=7,
                 car_lap_dist=[0.5, 0.48], car_est_times=[10.0, 8.0],
                 gap_ahead_s=None)
        t.update(s)
        s2 = snap(lap=2, lap_completed=1, fuel_level=28.6, car_idx=7,
                  car_lap_dist=[0.5, 0.48], car_est_times=[11.0, 9.0],
                  gap_ahead_s=None)
        t.update(s2)
        self.assertIsNotNone(t.lap_records[0]["gap_ahead_s"])
```


- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_lap_records -v`  
Expected: FAIL with `AttributeError: 'DerivedTracker' object has no attribute 'lap_records'`

- [ ] **Step 3: Implement the extension in `DerivedTracker`**

在 `__init__` 加 `self.lap_records: list[dict] = []` 与 `self._lap_open: dict | None = None`；`reset()` 清空两者。

在 `update()` 里，**在 `self._derive_gaps(s)` 之后**处理圈边界：

- 若 `self._lap_open is None`：开一个新圈的暂存（记 `lap`、`fuel_level`、`session_time`、`position` 等）
- 若 `s.lap != self._lap_open["lap"]` 且 `s.lap == self._lap_open["lap"] + 1`：收尾上一圈 → `record_lap(...)` → 开新圈
- 若圈号非连续：清空 `_lap_open` 重新开

`record_lap` 只保留最近 20 条（`del self.lap_records[:-20]`）。

**两套有效圈判据**（关键）：

- `fuel_used` 可用条件：`valid_lap`（现有 `_valid_lap` 语义，进站/加油/跨线维修区都置假）
- 圈记录本身可用条件：仅要求「圈号连续 + 非跨线维修区 + session 未重置」

即 `record["fuel_used"]` 在进站加油圈为 `None`，但记录照写。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_lap_records -v`  
Expected: PASS（5 tests）

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `cd voice-assistant && python -m unittest discover -s tests -v 2>&1 | tail -5`  
Expected: 全部通过（原 132 项 + 新增）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/telemetry.py voice-assistant/tests/test_lap_records.py
git commit -m "feat(ai): DerivedTracker 圈记录环形缓冲，油耗与圈记录双判据"
```

---

## Task 4: `SimTelemetry` 可选圈推进

**Files:**

- Modify: `voice-assistant/src/telemetry.py`（`SimTelemetry.read`）
- Modify: `voice-assistant/src/engine.py` 或 `app/desktop.py`（解析 `--sim-advance`）
- Test: `voice-assistant/tests/test_sim_advance.py`

**Interfaces:**

- [ ] Consumes: `SimTelemetry`
- [ ] Produces: `SimTelemetry(advance: bool = False, lap_seconds: float = 8.0)`；`advance=True` 时 `lap` 随时间递增
- [ ] **Step 1: Write the failing test**

```python
# tests/test_sim_advance.py
import time, unittest
from src.telemetry import SimTelemetry

class SimAdvanceTests(unittest.TestCase):
    def test_default_sim_lap_never_advances(self):
        s = SimTelemetry()
        first = s.read().lap
        time.sleep(0.05)
        self.assertEqual(s.read().lap, first)

    def test_advance_mode_increments_lap(self):
        s = SimTelemetry(advance=True, lap_seconds=0.02)
        first = s.read().lap
        time.sleep(0.06)
        self.assertGreater(s.read().lap, first)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_sim_advance -v`  
Expected: FAIL（`SimTelemetry.__init__` 不接受 `advance`）

- [ ] **Step 3: Implement**

`SimTelemetry.__init__(self, advance: bool = False, lap_seconds: float = 8.0, **overrides)`；`advance` 为真时在 `read()` 里按经过时间增量改 `lap`、`lap_completed`、`fuel_level`、`lap_last`。`--sim-advance` 只加 argparse 开关并透传，默认 False。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_sim_advance -v`  
Expected: PASS（2 tests）

- [ ] **Step 5: Confirm existing suite unaffected**

Run: `cd voice-assistant && python -m unittest discover -s tests -v 2>&1 | tail -3`  
Expected: 全绿（默认行为未变）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/telemetry.py voice-assistant/src/engine.py voice-assistant/tests/test_sim_advance.py
git commit -m "feat(ai): sim 模式可选圈推进，默认关闭不影响既有测试"
```

---

## Task 5: 上下文组装

**Files:**

- Create: `voice-assistant/src/race_context.py`
- Test: `voice-assistant/tests/test_race_context.py`

**Interfaces:**

- [ ] Consumes: `Snapshot`、`DerivedTracker.lap_records`、`QueryKind`
- [ ] Produces:
  - `build(snap: Snapshot, laps: list[dict], kind: QueryKind, question: str) -> dict`
  - `ALLOWED_FIELDS: frozenset[str]`（模板与数据闸共用的字段白名单）
  - `estimate_tokens(ctx: dict) -> int`（按字符数 / 2 估算）
  - `trim(ctx: dict, max_tokens: int) -> dict`（裁剪顺序：远圈 → opponents 非邻车 → derived 对手字段 → 只留 now+session）
- [ ] **Step 1: Write the failing test**

```python
# tests/test_race_context.py
import unittest
from src.race_context import build, estimate_tokens, trim, ALLOWED_FIELDS
from src.query_class import QueryKind
from src.telemetry import Snapshot

def snap(**kw):
    base = dict(connected=True, source="test", lap=12, lap_completed=11,
                lap_last=83.512, lap_best=83.104, laps_remain=5,
                fuel_level=18.4, position=4, field_size=18,
                gap_ahead_s=1.8, gap_behind_s=1.2,
                air_temp=28.0, track_temp=41.0, track_wetness=1)
    base.update(kw)
    return Snapshot(**base)

class BuildTests(unittest.TestCase):
    def test_minimal_question_gets_now_and_derived_only(self):
        ctx = build(snap(), [], QueryKind.KNOWN, "油还够吗")
        self.assertIn("now", ctx)
        self.assertIn("derived", ctx)
        self.assertNotIn("recent_laps", ctx)

    def test_analysis_includes_recent_laps(self):
        laps = [{"lap": i, "lap_time": 83.0 + i * 0.1} for i in range(1, 8)]
        ctx = build(snap(), laps, QueryKind.ANALYSIS, "为什么慢")
        self.assertIn("recent_laps", ctx)
        self.assertLessEqual(len(ctx["recent_laps"]), 5)

    def test_derived_values_are_prerounded(self):
        ctx = build(snap(), [{"lap": 11, "lap_time": 83.2}], QueryKind.KNOWN, "油")
        self.assertEqual(round(ctx["derived"]["delta_to_best"], 1), 0.4)

    def test_opponents_layer_is_not_included_by_default(self):
        ctx = build(snap(), [], QueryKind.KNOWN, "油还够吗")
        self.assertNotIn("opponents", ctx)

class TrimTests(unittest.TestCase):
    def test_trim_drops_far_laps_first(self):
        laps = [{"lap": i, "lap_time": 83.0} for i in range(1, 20)]
        ctx = build(snap(), laps, QueryKind.ANALYSIS, "为什么慢")
        tight = trim(ctx, 100)
        self.assertLessEqual(len(tight.get("recent_laps", [])),
                             len(ctx["recent_laps"]))

    def test_estimate_is_cheap_and_monotonic(self):
        small = {"a": "x"}
        big = {"a": "x" * 400}
        self.assertLess(estimate_tokens(small), estimate_tokens(big))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_race_context -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `voice-assistant/src/race_context.py`**

`ALLOWED_FIELDS` 必须包含 spec 第八节表格中全部派生量，以及 `now` 里的字段名。`build` 按 kind 决定包含哪些层；派生值**必须在此处算好并 `round(x, 1)`**（数字闸只接受原样引用）。

`opponents` 层只在问题含「前车/后车/第一名/对手/差距」时附加，且只带 3 台。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_race_context -v`  
Expected: PASS（6 tests）

- [ ] **Step 5: Add Review Focus #2 test（遥测缺失）**

```python
class MissingDataTests(unittest.TestCase):
    def test_disconnected_snapshot_does_not_raise(self):
        ctx = build(Snapshot(connected=False), [], QueryKind.ANALYSIS, "为什么慢")
        self.assertFalse(ctx["now"]["connected"])

    def test_none_fields_are_omitted_not_nulled(self):
        ctx = build(snap(fuel_level=None, lap_last=None), [], QueryKind.ANALYSIS, "油")
        self.assertNotIn("fuel_level", ctx["now"])
```

Run: `cd voice-assistant && python -m unittest tests.test_race_context -v`  
Expected: PASS（8 tests）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/race_context.py voice-assistant/tests/test_race_context.py
git commit -m "feat(ai): 上下文组装，派生值预计算、缺数据处理"
```

---

## Task 6: 三道校验闸

**Files:**

- Create: `voice-assistant/src/answer_checks.py`
- Test: `voice-assistant/tests/test_answer_checks.py`

**Interfaces:**

- [ ] Consumes: `ai_constants`、`race_context.ALLOWED_FIELDS`
- [ ] Produces:
  - `check_length(answer: str, max_chars: int) -> str | None`（`None` 为通过，否则返回失败原因）
  - `check_data(data_used: list[str], ctx: dict) -> str | None`
  - `check_numbers(answer: str, ctx: dict, question: str) -> str | None`
  - `run_all(payload: dict, ctx: dict, question: str, max_chars: int) -> tuple[bool, dict[str, str]]`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_answer_checks.py
import unittest
from src.answer_checks import check_length, check_data, check_numbers, run_all

CTX = {
    "now": {"lap_last": 83.512, "laps_remain": 5, "fuel_level": 18.4,
            "position": 4, "relative_humidity": 0.62},
    "derived": {"laps_of_fuel": 6.2, "delta_to_best": 0.4, "laps_spare": 1.2},
}

class LengthTests(unittest.TestCase):
    def test_short_answer_passes(self):
        self.assertIsNone(check_length("油够跑六圈", 60))

    def test_over_limit_fails(self):
        self.assertIsNotNone(check_length("字" * 61, 60))

class DataTests(unittest.TestCase):
    def test_known_fields_pass(self):
        self.assertIsNone(check_data(["laps_of_fuel"], CTX))

    def test_invented_field_fails(self):
        self.assertIsNotNone(check_data(["fuel_pressure"], CTX))

class NumberTests(unittest.TestCase):
    def test_exact_number_passes(self):
        self.assertIsNone(check_numbers("油还够跑 6.2 圈", CTX, "油还够吗"))

    def test_invented_number_fails(self):
        self.assertIsNotNone(check_numbers("油还够跑 9.9 圈", CTX, "油还够吗"))

    def test_chinese_numerals_are_rejected(self):
        self.assertIsNotNone(check_numbers("油还够跑六圈", CTX, "油还够吗"))

    def test_laptime_minutes_seconds_is_allowed(self):
        self.assertIsNone(check_numbers("上一圈 1 分 23 秒 5", CTX, "上圈多少"))

    def test_percentage_scaling_is_allowed(self):
        self.assertIsNone(check_numbers("湿度 62%", CTX, "湿度多少"))

    def test_ordinal_matches_position(self):
        self.assertIsNone(check_numbers("现在第 4 名", CTX, "第几名"))

    def test_number_from_question_is_exempt(self):
        self.assertIsNone(check_numbers("确实慢了 0.3 秒", CTX, "为什么慢了0.3秒"))

class RunAllTests(unittest.TestCase):
    def test_all_pass_returns_true(self):
        ok, checks = run_all({"answer": "油还够跑 6.2 圈", "data_used": ["laps_of_fuel"]},
                             CTX, "油还够吗", 60)
        self.assertTrue(ok)
        self.assertEqual(checks, {"length": "pass", "data": "pass", "number": "pass"})

    def test_one_fail_returns_false(self):
        ok, checks = run_all({"answer": "油还够跑 9.9 圈", "data_used": ["laps_of_fuel"]},
                             CTX, "油还够吗", 60)
        self.assertFalse(ok)
        self.assertEqual(checks["number"], "fail")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_answer_checks -v`  
Expected: FAIL with `ModuleNotFoundError`


- [ ] **Step 3: Implement `voice-assistant/src/answer_checks.py`**

数值提取用 `re.findall(r"\d+\.?\d*", answer)`；中文数字检测用 `re.search(r"[一二三四五六七八九十]", answer)` 直接判失败（除非该中文数字出现在问题原文里）。

匹配逻辑：先把 `ctx` 递归展平成 `{字段名: 数值}`，再对每个提取到的数字按顺序尝试：精确/容差匹配 → 分秒换算 → 百分比缩放 → 序数（与 `position` 比）→ 问题豁免 → 失败。

分秒换算：对每个候选秒值 `v`，计算 `m, s = divmod(v, 60)`，若 `abs(m - 提取的分钟) < 0.05` 且 `abs(s - 提取的秒) <= 0.6` 则通过。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_answer_checks -v`  
Expected: PASS（10 tests）

- [ ] **Step 5: Commit**

```bash
git add voice-assistant/src/answer_checks.py voice-assistant/tests/test_answer_checks.py
git commit -m "feat(ai): 三道校验闸，支持分秒换算与百分比缩放"
```

---

## Task 7: 模型调用层

**Files:**

- Create: `voice-assistant/src/llm_engine.py`
- Test: `voice-assistant/tests/test_llm_engine.py`

**Interfaces:**

- [ ] Consumes: `ai_constants`
- [ ] Produces:
  - `class LlmError(RuntimeError)`
  - `class LlmClient`：`__init__(base_url, api_key, model, timeout_s, max_tokens)`、`ask(ctx: dict, question: str) -> dict`（返回解析后的 JSON）、`test_connection() -> dict`、`close()`
  - `class AdaptiveBreaker`：`record(latency_ms: float, timed_out: bool)`、`should_skip() -> bool`、`reset_half_open()`、`state -> str`（`closed`/`open`）
  - 模块级单飞锁；`ask` 在已有请求在途时**取消旧请求**
- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_engine.py
import threading, time, unittest
from unittest.mock import patch
from src.llm_engine import LlmClient, LlmError, AdaptiveBreaker

class BreakerTests(unittest.TestCase):
    def test_opens_after_three_consecutive_timeouts(self):
        b = AdaptiveBreaker()
        for _ in range(3):
            b.record(2000, timed_out=True)
        self.assertTrue(b.should_skip())
        self.assertEqual(b.state, "open")

    def test_success_resets_counter(self):
        b = AdaptiveBreaker()
        b.record(2000, timed_out=True)
        b.record(2000, timed_out=True)
        b.record(300, timed_out=False)
        b.record(2000, timed_out=True)
        b.record(2000, timed_out=True)
        self.assertFalse(b.should_skip())

    def test_half_open_allows_one_probe(self):
        b = AdaptiveBreaker()
        for _ in range(3):
            b.record(2000, timed_out=True)
        b.reset_half_open()
        self.assertFalse(b.should_skip())

class TimeoutTests(unittest.TestCase):
    def test_slow_response_raises_within_budget(self):
        c = LlmClient("http://127.0.0.1:1", "k", "m", timeout_s=0.2, max_tokens=10)
        start = time.monotonic()
        with self.assertRaises(LlmError):
            c.ask({"now": {}}, "test")
        self.assertLess(time.monotonic() - start, 1.0)

    def test_timeout_is_clamped(self):
        c = LlmClient("http://x", "k", "m", timeout_s=99, max_tokens=9999)
        self.assertLessEqual(c.timeout_s, 2.0)
        self.assertLessEqual(c.max_tokens, 300)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_llm_engine -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `voice-assistant/src/llm_engine.py`**

`__init__` 用 `ai_constants.clamp` 钳制 `timeout_s` / `max_tokens`。

`ask()`：拼 OpenAI 兼容请求体（`response_format={"type":"json_object"}` 可选），用 `urllib.request.urlopen(req, timeout=self.timeout_s)`；解析 `choices[0].message.content` 为 JSON；任何异常包成 `LlmError`。单飞用模块级 `threading.Lock` + `threading.Event` 实现「新请求置旧请求的 cancel event」。

`AdaptiveBreaker` 维护 `_consecutive_timeouts`，`record` 时超时 +1、成功清零；`>= 3` 时 `state="open"`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_llm_engine -v`  
Expected: PASS（5 tests）

- [ ] **Step 5: Add Review Focus #3 test（慢/坏模型）**

```python
class BadModelTests(unittest.TestCase):
    def test_non_json_content_raises_cleanly(self):
        with patch("src.llm_engine._post_json", return_value="not json"):
            c = LlmClient("http://x", "k", "m", timeout_s=1.0, max_tokens=10)
            with self.assertRaises(LlmError):
                c.ask({"now": {}}, "test")
```

Run: `cd voice-assistant && python -m unittest tests.test_llm_engine -v`  
Expected: PASS（6 tests）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/llm_engine.py voice-assistant/tests/test_llm_engine.py
git commit -m "feat(ai): 模型调用层，硬截止/单飞/自适应熔断"
```

---

## Task 8: TTS 双适配器

**Files:**

- Create: `voice-assistant/src/tts_engine.py`
- Test: `voice-assistant/tests/test_tts_engine.py`

**Interfaces:**

- [ ] Consumes: `ai_constants`
- [ ] Produces:
  - `class TtsError(RuntimeError)`
  - `class TtsEngine`：`__init__(adapter: str, config: dict)`，`adapter` ∈ `{"doubao","openai","off"}`
  - `synth(text: str) -> bytes`：返回 WAV 字节，超过 `TTS_DEADLINE_HARD_MAX` 抛 `TtsError`
  - `test() -> dict`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_tts_engine.py
import unittest
from src.tts_engine import TtsEngine, TtsError

class AdapterSelectTests(unittest.TestCase):
    def test_off_raises_on_synth(self):
        e = TtsEngine("off", {})
        with self.assertRaises(TtsError):
            e.synth("测试")

    def test_unknown_adapter_raises(self):
        with self.assertRaises(ValueError):
            TtsEngine("nope", {})

    def test_missing_credentials_raises(self):
        e = TtsEngine("openai", {"base_url": "http://x"})
        with self.assertRaises(TtsError):
            e.synth("测试")

    def test_deadline_is_clamped(self):
        e = TtsEngine("openai", {"timeout_s": 99})
        self.assertLessEqual(e.timeout_s, 1.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_tts_engine -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `voice-assistant/src/tts_engine.py`**

豆包适配器复用 `scripts/tts_doubao.py` 的 NDJSON 解析逻辑（**复制核心解析，不 import scripts 包**——打包时不包含 `scripts/`）；OpenAI 适配器走 `POST {base_url}/audio/speech`。两者都返回二进制音频；若是 mp3 则用 `imageio_ffmpeg` 转 WAV（打包版已含）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_tts_engine -v`  
Expected: PASS（4 tests）

- [ ] **Step 5: Commit**

```bash
git add voice-assistant/src/tts_engine.py voice-assistant/tests/test_tts_engine.py
git commit -m "feat(ai): TTS 双适配器（豆包 / OpenAI 兼容），可选默认关"
```

---

## Task 9: 学习库

**Files:**

- Create: `voice-assistant/src/ai_learn.py`
- Test: `voice-assistant/tests/test_ai_learn.py`

**Interfaces:**

- [ ] Consumes: `race_context.ALLOWED_FIELDS`、`answer_checks`
- [ ] Produces:
  - `class LearnStore`：`__init__(root: pathlib.Path)`、`add_knowledge(question, answer, clip_path)`、`add_template(question, template)`、`match(question) -> tuple[str, dict] | None`（返回 `("knowledge", entry)` 或 `("template", entry)`）、`mark(entry_id: str, ok: bool)`、`stats() -> dict`、`clear()`
  - `validate_template(template: dict) -> str | None`（`None` 为通过）
  - `fill_template(template: dict, ctx: dict) -> str | None`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_learn.py
import pathlib, tempfile, unittest
from src.ai_learn import LearnStore, fill_template, validate_template

GOOD = {
    "params": ["laps_of_fuel", "laps_remain"],
    "rules": [
        {"when": "laps_of_fuel >= laps_remain * 1.15", "say": "够 {laps_of_fuel} 圈"},
        {"when": "true", "say": "得进站"},
    ],
}

class ValidateTests(unittest.TestCase):
    def test_good_template_passes(self):
        self.assertIsNone(validate_template(GOOD))

    def test_unknown_param_rejected(self):
        bad = {"params": ["fuel_pressure"], "rules": [{"when": "true", "say": "x"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_missing_fallback_rejected(self):
        bad = {"params": [], "rules": [{"when": "laps_of_fuel > 1", "say": "x"}]}
        self.assertIsNotNone(validate_template(bad))

    def test_function_call_in_when_rejected(self):
        bad = {"params": ["laps_of_fuel"], "rules": [
            {"when": "__import__('os')", "say": "x"}, {"when": "true", "say": "y"}]}
        self.assertIsNotNone(validate_template(bad))

class FillTests(unittest.TestCase):
    def test_fill_picks_first_matching_rule(self):
        ctx = {"derived": {"laps_of_fuel": 6.2}, "now": {"laps_remain": 5}}
        self.assertEqual(fill_template(GOOD, ctx), "够 6.2 圈")

    def test_fill_falls_back(self):
        ctx = {"derived": {"laps_of_fuel": 2.0}, "now": {"laps_remain": 5}}
        self.assertEqual(fill_template(GOOD, ctx), "得进站")

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LearnStore(pathlib.Path(self.tmp.name))
    def tearDown(self):
        self.tmp.cleanup()

    def test_add_and_match_template(self):
        self.store.add_template("我该不该进站", GOOD)
        hit = self.store.match("我该不该进站啊")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], "template")

    def test_disabled_after_two_errors(self):
        self.store.add_template("我该不该进站", GOOD)
        eid = self.store.stats()["templates"][0]["id"]
        self.store.mark(eid, ok=False)
        self.store.mark(eid, ok=False)
        self.assertIsNone(self.store.match("我该不该进站"))

    def test_clear_empties_store(self):
        self.store.add_template("我该不该进站", GOOD)
        self.store.clear()
        self.assertEqual(self.store.stats()["templates"], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_ai_learn -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `voice-assistant/src/ai_learn.py`**

存储为 `knowledge.jsonl` / `templates.jsonl`，每行一条。`match` 用 `phrase_match.normalize` 规范化后做字符相似度（复用 `_sim`）。

**安全：`when` 表达式求值绝不能用 `eval`。** 实现一个只支持 `<字段> <op> <数字|字段>` 与 `<字段> <op> <字段> * <数字>` 的极小解析器；无法解析即视为不匹配。

`validate_template` 检查：`params` 全在 `ALLOWED_FIELDS`、规则非空、必须有 `when == "true"` 兜底、每条 `when` 可解析、`say` 里的占位符都来自 `params`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_ai_learn -v`  
Expected: PASS（10 tests）

- [ ] **Step 5: Add Review Focus #5 test（越界模板与结论）**

```python
class UnsafeTemplateTests(unittest.TestCase):
    def test_template_with_hardcoded_conclusion_placeholder_mismatch_rejected(self):
        bad = {"params": ["laps_remain"], "rules": [
            {"when": "true", "say": "用 {laps_of_fuel} 圈"}]}   # 占位符不在 params
        self.assertIsNotNone(validate_template(bad))

    def test_fill_returns_none_when_placeholder_missing_in_ctx(self):
        ctx = {"derived": {}, "now": {}}
        self.assertIsNone(fill_template(GOOD, ctx))
```


Run: `cd voice-assistant && python -m unittest tests.test_ai_learn -v`  
Expected: PASS（12 tests）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/ai_learn.py voice-assistant/tests/test_ai_learn.py
git commit -m "feat(ai): 学习库，条件模板安全求值 + 权重纠错"
```

---

## Task 10: AI 编排层

**Files:**

- Create: `voice-assistant/src/ai_pipeline.py`
- Test: `voice-assistant/tests/test_ai_pipeline.py`

**Interfaces:**

- [ ] Consumes: `query_class`、`race_context`、`llm_engine`、`answer_checks`、`tts_engine`、`ai_learn`
- [ ] Produces:
  - `class AiResult`：`ok: bool`、`text: str`、`source: str`（`learn`/`model`/`refused`）、`reason: str`、`checks: dict`、`latency_ms: int`
  - `class AiPipeline`：`__init__(cfg: dict, store: LearnStore, out)`
  - `should_engage(kind: QueryKind, mode: str, rule_ok: bool) -> bool`
  - `handle(question: str, snap, kind, laps, rule_ok) -> AiResult`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_pipeline.py
import unittest
from src.ai_pipeline import should_engage, AiResult
from src.query_class import QueryKind

class EngageMatrixTests(unittest.TestCase):
    def test_safety_never_engages_in_any_mode(self):
        for mode in ("full", "analysis_key", "whitelist"):
            with self.subTest(mode=mode):
                self.assertFalse(should_engage(QueryKind.SAFETY, mode, rule_ok=False))

    def test_analysis_engages_in_all_modes(self):
        for mode in ("full", "analysis_key", "whitelist"):
            with self.subTest(mode=mode):
                self.assertTrue(should_engage(QueryKind.ANALYSIS, mode, rule_ok=False))

    def test_whitelist_does_not_engage_unknown(self):
        self.assertFalse(should_engage(QueryKind.UNKNOWN, "whitelist", rule_ok=False))

    def test_full_engages_unknown(self):
        self.assertTrue(should_engage(QueryKind.UNKNOWN, "full", rule_ok=False))

    def test_known_with_rule_ok_never_engages(self):
        for mode in ("full", "analysis_key", "whitelist"):
            with self.subTest(mode=mode):
                self.assertFalse(should_engage(QueryKind.KNOWN, mode, rule_ok=True))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_ai_pipeline -v`  
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `voice-assistant/src/ai_pipeline.py`**

`should_engage` 实现 spec 5.3 的矩阵：

- `SAFETY` → 恒 False
- `ANALYSIS` → 除 `whitelist` 需要白名单意图外，都 True（`ANALYSIS` 分类在 C 档也放行）
- `UNKNOWN` → 仅 `full`
- `KNOWN` → `rule_ok=True` 时 False；`rule_ok=False` 时仅白名单/`full`

`handle` 顺序：学习库匹配 → 命中且模板可填则返回（`source="learn"`）→ 未命中则调 `LlmClient.ask` → `answer_checks.run_all` → 通过则返回（`source="model"`，可选 TTS）→ 任一失败返回 `ok=False`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_ai_pipeline -v`  
Expected: PASS（5 tests）

- [ ] **Step 5: Commit**

```bash
git add voice-assistant/src/ai_pipeline.py voice-assistant/tests/test_ai_pipeline.py
git commit -m "feat(ai): 编排层，档位矩阵与学习库优先"
```

---

## Task 11: 配置接入与钳制

**Files:**

- Modify: `voice-assistant/src/config.py`（`DEFAULTS`、`load`、`validate`）
- Modify: `voice-assistant/config.yaml`
- Test: `voice-assistant/tests/test_ai_config.py`

**Interfaces:**

- [ ] Consumes: `ai_constants`
- [ ] Produces: `cfg["ai"]` 段，字段见 spec 第十四节；`cfg["_ai_learn_dir"]` 指向 `data_dir()/ai_learn`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_config.py
import os, pathlib, tempfile, unittest, yaml
from unittest.mock import patch
from src import config

class AiConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "config.yaml").write_text(yaml.safe_dump({}), encoding="utf-8")
    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_are_disabled(self):
        cfg = config.load(self.root / "config.yaml")
        self.assertFalse(cfg["ai"]["enabled"])
        self.assertEqual(cfg["ai"]["mode"], "whitelist")

    def test_timeout_is_clamped_to_hard_max(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"timeout_s": 10}}), encoding="utf-8")
        cfg = config.load(self.root / "config.yaml")
        self.assertEqual(cfg["ai"]["timeout_s"], 2.0)

    def test_max_answer_chars_is_clamped(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"max_answer_chars": 200}}), encoding="utf-8")
        cfg = config.load(self.root / "config.yaml")
        self.assertEqual(cfg["ai"]["max_answer_chars"], 60)

    def test_env_key_overrides_file(self):
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"api_key": "fromfile"}}), encoding="utf-8")
        with patch.dict(os.environ, {"LITTLE_NAVIGATOR_AI_KEY": "fromenv"}):
            cfg = config.load(self.root / "config.yaml")
        self.assertEqual(cfg["ai"]["api_key"], "fromenv")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_ai_config -v`  
Expected: FAIL（`KeyError: 'ai'`）

- [ ] **Step 3: Implement**

`DEFAULTS` 加 `"ai"` 段（全部字段带默认值）。`load()` 在合并后、`validate()` 前调用 `ai_constants` 的钳制函数；`installed_mode()` 下把 `_ai_learn_dir` 指向 `data_dir()/ai_learn`。环境变量 `LITTLE_NAVIGATOR_AI_KEY` 优先级最高。

`config.yaml` 追加 `ai:` 段（**不含 api_key**，注释说明放 `config.local.yaml`）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_ai_config -v`  
Expected: PASS（4 tests）

- [ ] **Step 5: Confirm existing config tests still pass**

Run: `cd voice-assistant && python -m unittest tests.test_installation -v`  
Expected: PASS（9 tests，含 ai 段不破坏既有行为）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/config.py voice-assistant/config.yaml voice-assistant/tests/test_ai_config.py
git commit -m "feat(ai): 配置段与硬上限钳制，密钥走 local/环境变量"
```

---

## Task 12: 引擎接入（模型调用移出锁）

**Files:**

- Modify: `voice-assistant/src/engine.py`（`Assistant.__init__`、`ask`）
- Test: `voice-assistant/tests/test_engine_ai.py`

**Interfaces:**

- [ ] Consumes: `ai_pipeline`、`query_class`
- [ ] Produces: `Assistant.ask()` 在 AI 分支返回的 `rec` 增加 `ai_source` / `ai_text` / `ai_checks`；`Assistant.ai_status()` 供界面轮询
- [ ] **Step 1: Write the failing test**

```python
# tests/test_engine_ai.py
import io, pathlib, tempfile, unittest
from unittest.mock import MagicMock, patch
from src.engine import Assistant
from src.query_class import QueryKind

class AiBranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()   # 跨平台，不用 /tmp

    def _assistant(self, ai_enabled=True):
        cfg = {
            "voice_pack": {"dir": "x", "extra_dir": None, "telemetry_dir": None,
                           "cooldown": 0,
                           "state_file": str(self.root / "cooldown.json")},
            "telemetry": {"provider": "sim", "poll_hz": 1,
                          "ring_buffer": {"keep_laps": 5}},
            "intent": {"l1_keyword": {"enabled": False}, "voice_strict": True,
                       "miss_log": {"enabled": False,
                                    "path": str(self.root / "miss.jsonl")}},
            "playback": {"enabled": False, "volume": 1.0,
                         "ducking": {"enabled": False}},
            "logging": {"dir": str(self.root / "logs"),
                        "qa_log": str(self.root / "logs" / "qa.jsonl")},
            "asr": {"model": "sense-voice", "num_threads": 1, "models_dir": "m"},
            "ptt": {"vk": 0xA3, "poll_ms": 10, "min_hold_ms": 250, "max_hold_s": 8},
            "ai": {"enabled": ai_enabled, "mode": "full", "base_url": "http://x",
                   "model": "m", "api_key": "k", "timeout_s": 2.0,
                   "max_tokens": 300, "max_answer_chars": 60,
                   "rate_limit_per_min": 6, "whitelist_intents": [],
                   "analysis_key": None},
            "_root": str(self.root),
            "_local_path": str(self.root / "l.json"),
        }
        with patch("src.engine.VoicePack.load_many", return_value=MagicMock(
                variants={},
                stats=lambda: {"msgIds": 0, "clips": 0, "avg_variants": 0},
                sample_rate=lambda: 16000)):
            return Assistant(cfg, no_play=True, out=io.StringIO())   # 不写文件

    def test_ai_disabled_keeps_old_behavior(self):
        a = self._assistant(ai_enabled=False)
        self.assertFalse(a.ai_enabled)

    def test_should_engage_is_safety_first(self):
        a = self._assistant(ai_enabled=True)
        self.assertFalse(a._ai_should_engage(QueryKind.SAFETY, "为什么被罚"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_engine_ai -v`  
Expected: FAIL（`TypeError` 或缺 `ai_enabled`）

- [ ] **Step 3: Implement**

`Assistant.__init__` 构造 `AiPipeline`（仅在 `cfg["ai"]["enabled"]` 时）；加 `ai_enabled` 属性与 `_ai_should_engage(kind, text)` 辅助方法。

`ask()` 改动（关键）：

- [ ] 在 `_resolve` 之后、播报之前插入分类与转交判断
- [ ] **模型调用不能在 `@exclusive` 内**：把 AI 分支拆成 `_ask_ai(question, snap, kind)` 无锁方法，由 `ask()` 调用；进入模型等待前先 `self.busy = False`，结束后恢复
- [ ] 模型返回后：有 TTS 则合成播放；无 TTS 则播 `AST_AI_SEE_SCREEN`；文本写入 `rec["ai_text"]`
- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_engine_ai -v`  
Expected: PASS（2 tests）

- [ ] **Step 5: Confirm no regression in engine tests**

Run: `cd voice-assistant && python -m unittest tests.test_regressions -v 2>&1 | tail -3`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/engine.py voice-assistant/tests/test_engine_ai.py
git commit -m "feat(ai): 引擎接入，模型调用移出 exclusive 锁"
```

---

## Task 13: 分析键第二输入源

**Files:**

- Modify: `voice-assistant/src/listener.py`
- Test: `voice-assistant/tests/test_analysis_key.py`

**Interfaces:**

- [ ] Produces: `PttListener(..., analysis_vk=None, on_analysis_result=None)`；按住分析键录音后走 `on_analysis_result`
- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_key.py
import unittest
from unittest.mock import MagicMock
from src.listener import PttListener

class AnalysisKeyTests(unittest.TestCase):
    def test_analysis_key_defaults_to_none(self):
        l = PttListener(MagicMock(), {"ptt": {"vk": 0xA3, "poll_ms": 10,
                                             "min_hold_ms": 250, "max_hold_s": 8}},
                        lambda *a: None)
        self.assertIsNone(l.analysis_vk)

    def test_analysis_vk_is_stored(self):
        l = PttListener(MagicMock(), {"ptt": {"vk": 0xA3, "poll_ms": 10,
                                             "min_hold_ms": 250, "max_hold_s": 8}},
                        lambda *a: None, analysis_vk=0xA5)
        self.assertEqual(l.analysis_vk, 0xA5)
```


- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_analysis_key -v`  
Expected: FAIL（`unexpected keyword argument`）

- [ ] **Step 3: Implement**

`_loop` 里额外检测 `analysis_vk` 按下；按下时置 `self.pending_source = "analysis"`，录音流程复用现有 `_capture_lock`（两键互斥，避免同时录）。`_process` 依据 `pending_source` 选择回调。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_analysis_key -v`  
Expected: PASS（2 tests）

- [ ] **Step 5: Commit**

```bash
git add voice-assistant/src/listener.py voice-assistant/tests/test_analysis_key.py
git commit -m "feat(ai): 分析键作为第二输入源"
```

---

## Task 14: 服务端接口与密钥隔离

**Files:**

- Modify: `voice-assistant/app/server.py`
- Test: `voice-assistant/tests/test_ai_server.py`

**Interfaces:**

- [ ] Produces:
  - `Service.state()` 的 `pack`/`ai` 字段：`{"enabled","mode","status","last_text","api_key_set","analysis_key_bound"}`
  - `POST /api/ai/config`：写入 `config.local.yaml`，密钥只写不读
  - `POST /api/ai/test`：连通性测试
  - `GET /api/ai/review`：返回本场 AI 问答列表
  - `POST /api/ai/review/mark`：标记正确/错误
  - `export_logs()` 排除密钥
- [ ] **Step 1: Write the failing test**

测试必须打到真实代码路径，不能只验证自己构造的字典。

```python
# tests/test_ai_server.py
import json, pathlib, tempfile, unittest, yaml
from unittest.mock import patch
from src import config
from src.engine import Assistant

class KeyIsolationTests(unittest.TestCase):
    """密钥不得出现在 config_view / export_logs 的任何输出里。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name).resolve()
        (self.root / "config.yaml").write_text(yaml.safe_dump({}), encoding="utf-8")
        (self.root / "config.local.yaml").write_text(
            yaml.safe_dump({"ai": {"api_key": "sk-SECRET-123",
                                   "base_url": "http://x", "model": "m"}}),
            encoding="utf-8")

    def test_config_load_keeps_key_but_view_masks_it(self):
        cfg = config.load(self.root / "config.yaml")
        self.assertEqual(cfg["ai"]["api_key"], "sk-SECRET-123")   # 加载后内部有
        view = Assistant.config_view_for_test(cfg)                # 对外视图
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("sk-SECRET-123", text)                   # 视图里没有

    def test_export_payload_excludes_key(self):
        cfg = config.load(self.root / "config.yaml")
        payload = Assistant.export_payload_for_test(cfg)
        self.assertNotIn("sk-SECRET-123", json.dumps(payload, ensure_ascii=False))

    def test_state_reports_key_presence_not_value(self):
        cfg = config.load(self.root / "config.yaml")
        state = Assistant.ai_state_for_test(cfg)
        self.assertTrue(state["api_key_set"])
        self.assertNotIn("api_key", state)
```

> 说明：`config_view_for_test` / `export_payload_for_test` / `ai_state_for_test` 是**薄封装**（见 Step 3），目的是让密钥隔离这一条能被无 UI 地测试到；生产代码里 `Service` 调用的就是同一批函数。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_ai_server -v`  
Expected: FAIL with `AttributeError: type object 'Assistant' has no attribute 'config_view_for_test'`

- [ ] **Step 3: Implement**

在 `engine.py`（或 `server.py`，选模块更内聚的一侧）实现三个薄封装，**生产路径与测试路径共用**：

- `config_view_for_test(cfg) -> dict`：返回 `cfg` 的深拷贝视图，把 `ai.api_key` 替换为 `api_key_set: bool`
- `export_payload_for_test(cfg) -> dict`：同上，供 `export_logs()` 写入 zip 前调用
- `ai_state_for_test(cfg) -> dict`：`{"enabled","mode","api_key_set","model","base_url"}`

然后 `Service.state()` 用 `ai_state_for_test`，`export_logs()` 用 `export_payload_for_test`，`config_view()` 用 `config_view_for_test`。

同时构造 `AiPipeline` 与 `LearnStore`，新增四个路由（`POST /api/ai/config`、`POST /api/ai/test`、`GET /api/ai/review`、`POST /api/ai/review/mark`）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_ai_server -v`  
Expected: PASS（3 tests）

- [ ] **Step 5: Commit**

```bash
git add voice-assistant/app/server.py voice-assistant/tests/test_ai_server.py
git commit -m "feat(ai): 服务端接口与密钥隔离"
```

---

## Task 15: 赛后复盘入库流程

**Files:**

- Modify: `voice-assistant/app/server.py`（复盘路由）
- Modify: `voice-assistant/src/ai_learn.py`（补 `add_from_review`）
- Modify: `voice-assistant/src/llm_engine.py`（提示词要求输出 `template`）
- Test: `voice-assistant/tests/test_ai_review.py`

**Interfaces:**

- Consumes: `LearnStore`、`validate_template`、`AnswerChecks`
- Produces:
  - `LearnStore.add_from_review(record: dict) -> str`（返回 `"knowledge"` / `"template"` / `"rejected"`）
  - `POST /api/ai/review/mark` 的 `ok=True` 分支调用 `add_from_review`

**背景（spec 17.3 / 17.5）**：复盘界面标「正确」后，必须**分类入库**——知识型存完整问答+语音，动态型只存条件模板。这一步在 Task 9（学习库）和 Task 14（接口）之间是空的，必须单独实现，否则「标记正确」只是改了个计数，下次仍然要调模型。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_review.py
import pathlib, tempfile, unittest
from src.ai_learn import LearnStore

GOOD_TEMPLATE = {
    "params": ["laps_of_fuel", "laps_remain"],
    "rules": [
        {"when": "laps_of_fuel >= laps_remain * 1.15", "say": "够 {laps_of_fuel} 圈"},
        {"when": "true", "say": "得进站"},
    ],
}

class AddFromReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LearnStore(pathlib.Path(self.tmp.name))

    def test_dynamic_record_with_template_becomes_template(self):
        rec = {"question": "我该不该进站", "answer": "够 6.2 圈",
               "template": GOOD_TEMPLATE, "dynamic": True}
        kind = self.store.add_from_review(rec)
        self.assertEqual(kind, "template")
        self.assertEqual(len(self.store.stats()["templates"]), 1)

    def test_dynamic_record_without_template_is_rejected(self):
        # 动态型但没有模板 → 绝不能存成品答案（会播报过期数值）
        rec = {"question": "我该不该进站", "answer": "够 6.2 圈",
               "template": None, "dynamic": True}
        self.assertEqual(self.store.add_from_review(rec), "rejected")
        self.assertEqual(self.store.stats()["templates"], [])
        self.assertEqual(self.store.stats()["knowledge"], [])

    def test_static_record_becomes_knowledge(self):
        rec = {"question": "蓝旗什么意思", "answer": "后方有快车要放行",
               "template": None, "dynamic": False, "clip_path": "clips/a.wav"}
        self.assertEqual(self.store.add_from_review(rec), "knowledge")
        self.assertEqual(len(self.store.stats()["knowledge"]), 1)

    def test_invalid_template_is_rejected(self):
        bad = {"params": ["not_a_field"], "rules": [{"when": "true", "say": "x"}]}
        rec = {"question": "q", "answer": "a", "template": bad, "dynamic": True}
        self.assertEqual(self.store.add_from_review(rec), "rejected")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd voice-assistant && python -m unittest tests.test_ai_review -v`  
Expected: FAIL with `AttributeError: 'LearnStore' object has no attribute 'add_from_review'`

- [ ] **Step 3: Implement `add_from_review`**

```python
def add_from_review(self, record: dict) -> str:
    """复盘确认正确后入库。动态型必须有合法模板，否则拒绝。"""
```

判定顺序：

1. `record.get("dynamic")` 为真 → 必须有 `template` 且 `validate_template` 通过 → `add_template` → `"template"`；否则 `"rejected"`
2. 为假 → `add_knowledge`（带上 `clip_path`）→ `"knowledge"`

`Service` 的 `POST /api/ai/review/mark` 在 `ok=True` 时调用它，并把结果写进响应，让界面能显示「已存为模板 / 已存为知识 / 未通过校验」。

- [ ] **Step 4: 让模型输出模板（llm_engine 提示词）**

`llm_engine` 的系统提示词追加：要求返回 JSON 里带 `template` 字段（结构见 spec 17.3），并对不需要动态数据的知识型问题返回 `"dynamic": false`。

- [ ] **Step 5: Run test to verify it passes**

Run: `cd voice-assistant && python -m unittest tests.test_ai_review -v`  
Expected: PASS（4 tests）

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/src/ai_learn.py voice-assistant/src/llm_engine.py \
        voice-assistant/app/server.py voice-assistant/tests/test_ai_review.py
git commit -m "feat(ai): 复盘入库分类——动态型必须带合法模板"
```

---

## Task 16: 前端 AI 设置页与复盘区

**Files:**

- Modify: `voice-assistant/app/static/index.html`（nav 按钮 + 容器）
- Modify: `voice-assistant/app/static/app.js`（`renderAiPanel`、`renderAiReview`、主面板只读显示）
- Test: 手动验证（前端无自动化测试框架）

**Interfaces:**

- [ ] Consumes: Task 14 的接口
- [ ] Produces: 设置页「AI 分析」；主面板状态标签
- [ ] **Step 1: 加 nav 按钮与容器**

在 `index.html` 的 nav 里加「AI 分析」按钮，`data-view="ai"`；加 `<div data-view-panel="ai">` 容器（与现有四页同构）。

- [ ] **Step 2: 实现 `renderAiPanel()`**

字段：启用开关、档位下拉（A/B/C）、预设下拉、`base_url`、`model`、`api_key`（`type="password"`，显示 `api_key_set` 状态而非值）、测试连接按钮、分析键采集按钮、TTS 适配器与凭据、「清空学习库」按钮。

- [ ] **Step 3: 实现复盘区**

列表渲染 `GET /api/ai/review`；每条三个按钮（正确/错误/没帮助）调 `POST /api/ai/review/mark`。

- [ ] **Step 4: 主面板只读显示**

在现有主面板加一块：`st.ai.last_text` 文字 + 状态标签。**不要加输入框。**

- [ ] **Step 5: 手动验证**

Run: `cd voice-assistant && python -m app.desktop --sim`  
Expected: 窗口显示新 nav 项；点击进入设置页各控件可交互；密钥框显示为掩码且不回显原值；主面板出现 AI 状态区

- [ ] **Step 6: Commit**

```bash
git add voice-assistant/app/static/index.html voice-assistant/app/static/app.js
git commit -m "feat(ai): AI 设置页与赛后复盘界面"
```

---

## Task 17: 新增语音素材

**Files:**

- Modify: `content/assistant-telemetry-v1.2.json`
- Modify: `voice-assistant/config.yaml`（无改动，仅确认路径）
- Test: `voice-assistant/tests/test_ai_assets.py`

**Interfaces:**

- [ ] Produces: msgId `AST_AI_SEE_SCREEN`，文本「答案在屏幕上。」
- [ ] **Step 1: Write the failing test**

```python
# tests/test_ai_assets.py
import pathlib, unittest
from src.voicepack import VoicePack

class AiAssetTests(unittest.TestCase):
    def test_msgid_exists_in_pack(self):
        cfg = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
        # 该测试只在有素材的环境下有意义的断言；缺素材时跳过
        pack_dir = pathlib.Path(__file__).resolve().parents[2] / "build" / "assistant-telemetry"
        if not (pack_dir / "spmsg.txt").exists():
            self.skipTest("未导入运行时素材")
        pack = VoicePack.load(pack_dir)
        self.assertTrue(pack.has("AST_AI_SEE_SCREEN"))
```

- [ ] **Step 2: Run test to verify it fails or skips**

Run: `cd voice-assistant && python -m unittest tests.test_ai_assets -v`  
Expected: 无素材时 SKIP；有素材时 FAIL

- [ ] **Step 3: 追加文案并合成**

在 `content/assistant-telemetry-v1.2.json` 追加：

```json
{
  "msgId": "AST_AI_SEE_SCREEN",
  "cat": "assistant",
  "variants": ["答案在屏幕上。", "答案显示在屏幕上。", "请看屏幕上的答案。"]
}
```

然后按 `DEVELOPMENT.md` 的流程合成并重建 `assistant-telemetry` 包（需要豆包凭据，属人工步骤）。

- [ ] **Step 4: 验证素材数量更新**

Run: `cd voice-assistant && python tools/check_pack.py`  
Expected: msgId 数与音频数各 +1；无缺失

- [ ] **Step 5: Commit**

```bash
git add content/assistant-telemetry-v1.2.json voice-assistant/tests/test_ai_assets.py
git commit -m "feat(ai): 新增 AST_AI_SEE_SCREEN 语音素材"
```

---

## Task 18: 全量回归与量化门槛

**Files:**

- Test: `voice-assistant/tests/`（全部）
- Create: `voice-assistant/tools/ai_bench.py`

**Interfaces:**

- [ ] Produces: 误杀率与安全零漏放的可复算脚本
- [ ] **Step 1: 跑全量测试**

Run: `cd voice-assistant && python -m unittest discover -s tests -v 2>&1 | tail -5`  
Expected: 全部通过

- [ ] **Step 2: 跑离线审计**

Run: `cd voice-assistant && python tools/audit_offline.py`  
Expected: 0 errors，无缺失素材

- [ ] **Step 3: 写 `tools/ai_bench.py`**

脚本读一组问句（内置 50 条白名单问句 + 30 条越界句），调真实模型，统计：数字闸误杀率、安全类是否零漏放。输出 JSON 报告。

- [ ] **Step 4: 跑基准并确认门槛**

Run: `cd voice-assistant && python tools/ai_bench.py --config config.local.yaml`  
Expected: `false_reject_rate <= 0.20` 且 `safety_leaks == 0`

- [ ] **Step 5: Commit**

```bash
git add voice-assistant/tools/ai_bench.py
git commit -m "test(ai): 基准脚本，量化误杀率与安全漏放"
```

---

## 完成标准

- [ ] 全部 18 个任务完成，`python -m unittest discover -s tests -v` 全绿
- [ ] `tools/audit_offline.py` 0 错误
- [ ] `tools/ai_bench.py` 达标（误杀 ≤20%、安全零漏放）
- [ ] 未配置 AI 时，软件行为与 v1.4.2 完全一致
- [ ] 密钥不出现在任何日志、导出包、界面回显
- [ ] `VERSION` 升到 `1.4.3`，`RELEASE_NOTES.md` / `README.md` / `CHANGELOG.md` 同步
