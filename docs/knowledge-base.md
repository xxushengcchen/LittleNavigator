# 知识库页面（v1.4.5 之后新增）

## 它是什么

软件里的第十一个页面：把「问法 → 意图 → 遥测字段 → 播报方式」这条链路画出来，
并提供两种看法：

| 模式 | 回答的问题 |
|---|---|
| 意图图谱 | 这句话会连到哪里：意图节点、字段组、播报方式三列连线 |
| 问法矩阵 | 哪类问法多、哪里还空着：15 意图 × 6 类问法的计数 |

两种模式共用右侧详情（示例问法、问法分布、读取字段、播报方式），选中状态跨模式保留。
页面只读，不播报、不改车、不调模型。

## 文件

| 文件 | 作用 |
|---|---|
| `src/knowledge.py` | 组装数据：词库统计、字段分组、播报方式、真实链路探针、规则问题检测 |
| `app/server.py` | 新增 `GET /api/knowledge`；`Service.knowledge()` 调上面的模块 |
| `app/static/index.html` | 导航新增「知识库」（进阶组），新增面板与控件 |
| `app/static/app.js` | 图谱/矩阵渲染、过滤、快捷键、详情 |
| `app/static/app.css` | `.kb-*` 样式，沿用现有 token |
| `tools/test_knowledge_ui.cjs` | 12 项浏览器回归 |

## 数据从哪来

全部在请求时现算，没有第二份清单：

- 意图与示例：`intents_examples.yaml` 里 `route: state_layer` 的分节。
- 短语数与规范候选：真实 `PhraseMatcher` 载入结果。
- 规范问句与别名：`question_forms.GROUPS / ALIASES`。
- 播报方式：读 `telemetry_answers.py` 与 `router.py` 的分支（`numeric` / `timed` / `state` / `Decision`）。
- 字段组：`FIELD_GROUPS` 里的名字逐条对照 `Snapshot` 字段，写错会在请求时直接抛错。
- 链路探针：固定问法 + 每个意图的第一条规范问句，最多 75 条；用模拟快照跑 `router.route()`。
- 结果缓存 30 秒，词库文件改动后自动失效。

界面上的几处显示规则：

- 意图节点在窄窗口里用单行（名称在左、条数在右），因为 1180px 窗口下图谱只占约
  541px 宽，缩放后两行文字会挤到 10px 以下。
- 选中意图后，它的连线和目标字段/播报保持高亮，其余节点压到 26%。
- 矩阵比图谱宽，所以矩阵模式下详情自动落到下方，表格拿满内容宽度。

## 「可疑样本」怎么判定

只报规则问题，不把数据缺失算成缺陷：

- **误判**：命中了 A（A 的规则分支答不了），但排第二的 B 的规则分支受理这类问法。
  判定用 `_intent_accepts()`，只看分支、不看当前遥测值，避免「没连游戏」被误报。
- **缺对象**：意图命中正确，但问法本身缺必要信息（如「胎压多少」没说哪个轮）。
- **数据缺失**：拒绝原因是「缺少 / 没读到 / 尚未采集」等，单独计入 `data_gaps`，不列出。

当前本机结果（修复后）：

| 问法 | 类型 | 说明 |
|---|---|---|
| 胎压多少 | 缺对象 | 需要说明左前/右前/左后/右后，属于词库覆盖面，不是错误 |

## 已修复：「现在几档」误判（2026-10-20）

这个页面第一次跑起来时就暴露了一个真实缺陷：**「现在几档」被判成 ABS 设置**，
命中 `abs现在几档`（1.00），真正的挡位意图 `query_engine` 只有 0.81，结果答不了。

两条根因缺一不可：

1. 词库里没有「现在几档 / 现在几挡」原句，只有「abs现在几档」这类带前缀的；
2. `pypinyin` 的 `errors="ignore"` 把 `abs` 整个丢掉，于是两句话的拼音串完全相同，
   短问法靠拼音通道白拿 1.00。

两处都已修：

- `intents_examples.yaml` 的 `query_engine` 补入「现在几档」「现在几挡」「现在什么挡位」；
- `phrase_match._syllables()` 保留拉丁字母和数字，`abs` / `tc` 不再被删除。

修复后实测：

| 问法 | 修复前 | 修复后 |
|---|---|---|
| 现在几档 | query_car_settings（1.00，答不了） | query_engine（1.00，可答） |
| abs几档 | query_car_settings | query_car_settings（不变） |
| tc几档 | query_car_settings | query_car_settings（不变） |
| 现在几点了 | 拒绝 | 拒绝（不变） |
| 油还購吗（同音错字） | query_fuel | query_fuel（不变） |

新增 `tests/test_phrase_boundaries.py`（8 项）锁住这个边界：短挡位问法走引擎、
ABS/TC 仍走设置、拉丁前缀会改变拼音串、同音错字仍可匹配、越界句仍被拒。

## 界面计数口径

图谱节点上的条数与矩阵的「示例合计」不是同一个数，这是刻意的：

- **图谱节点** 用 `matcher.phrases` 里该意图去重后载入的短语数（本机合计 1,772）；
- **矩阵** 把 `intents_examples.yaml` 的原始示例按 6 类语义归类，行尾是原始示例数（本机合计 1,791）。

同一意图两处可能差几条，例如「燃油」是 156 条短语 / 158 条原始示例。为避免被误读成错误，
矩阵列头写作「示例合计」，表下另有一行说明。早期列头只写「合计」，容易与图谱对不上。

## 修复：安装版「读不到知识库」（v1.5.0 发布当天）

首版 v1.5.0 装完之后，知识库页显示「读不到知识库」，开发期跑服务却一切正常。

根因：`_output_modes()` 为了「改代码后页面自动跟着变」，直接读
`telemetry_answers.py` 与 `router.py` 的**源码**。PyInstaller 只打包 `.pyc`，
安装目录 `_internal/src/` 下根本没有 `.py`，于是

```
FileNotFoundError: ...\_internal\src\telemetry_answers.py
```

`/api/knowledge` 直接返回 500。开发机源码就在旁边，所以那套浏览器回归全部通过，
只有装完才会暴露——测试环境与发布形态不一致，这类问题只能靠「在打包产物上跑」发现。

两处都改了：

- `_output_modes()` 改为声明式 `OUTPUT_MODES` 表，运行期只读这张表；不再触碰任何 `.py`。
- 短语库路径改用 `phrase_match.default_library()`，和匹配器共用同一份解析规则
  （打包后优先读 exe 旁边那份），不再用 `Path(__file__).parents[1]`。

代价是「改路由代码要记得改表」，所以新增 `tests/test_knowledge_packaging.py`（6 项）：

- `build()` 在运行期读任何 `.py` / `.pyc` 就失败（用 `Path.read_text` 探针拦截）；
- 模拟安装版布局（`sys.frozen`、exe 旁有短语库、模块目录无源码）跑完整 `build()`；
- 重新解析两个源码文件的 `if intent=='query_*':` 分支，与 `OUTPUT_MODES` 逐项比对，
  漏改这张表测试就会失败；
- 短语库解析在冻结/非冻结两种情形下都指向预期文件。

修复后用打包产物实测：`/api/knowledge` 返回 200，数据与开发期完全一致
（15 意图 / 1,772 短语 / 75 探针 / 1 可疑 / 16 数据缺口）；对着打包后的服务再跑
一遍知识库专项，仍是 12 项全过。

## 验证

```
# 后端（含 8 项问法边界 + 6 项打包回归）
cd voice-assistant && python -m pytest tests -q
# → 553 passed, 2185 subtests passed

# 启动服务
python -m app.server --sim --no-listen --no-browser --port 8793

# 知识库专项（12 项）
set LN_UI_URL=http://127.0.0.1:8793
node voice-assistant/tools/test_knowledge_ui.cjs
# → DONE 12 checks passed

# 全量 UI 回归（16 项，含十一个页面）
set LN_UI_OUT=tmp/ui-qa-v1.5.0
node voice-assistant/tools/test_ui.cjs
# → {"passed":true,"tests":16}

# 打包产物实测（先启动 dist 里的 exe）
.\小小领航员.exe --sim --no-listen --no-window --port 8796
set LN_UI_URL=http://127.0.0.1:8796
node voice-assistant/tools/test_knowledge_ui.cjs
# → DONE 12 checks passed
```

专项覆盖：导航与标题、统计来自接口、14/8/3 个节点、选中只高亮相关连线、切模式保留选择、
矩阵单元格联动详情、过滤、Esc 清除、只读（浏览期间无 POST）、三档窗口无横向溢出。

截图（v1.5.0，模拟遥测）：[意图图谱](ui-preview/v1.5.0/knowledge-graph.png)、
[选中详情](ui-preview/v1.5.0/knowledge-detail.png)、[问法矩阵](ui-preview/v1.5.0/knowledge-matrix.png)。

## 已知边界

- 打包产物已验证接口与十二项交互（用同版本 Edge 内核驱动打包后的服务），但没有在
  WebView2 原生窗口里逐页目视检查渲染细节。
- 分类计数是关键词启发式，只用于看分布，不参与匹配。
- 冲突检测只覆盖固定探针与规范问句，不代表全部 1,791 条问法都已检查。
- 链路里的遥测来自模拟快照；真实连接时同样的问法可能有不同结果。
- 「胎压多少」缺对象属于词库覆盖面，未改；补问法时说清左前/右前/左后/右后即可。
- `OUTPUT_MODES` 是手工维护的表；漏改不会在运行期报错，只能在跑测试时发现。
