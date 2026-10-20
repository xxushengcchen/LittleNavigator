# 知识库页面（v1.4.5 之后新增）

## 它是什么

软件里的第十一个页面：把「问法 → 意图 → 遥测字段 → 播报方式」这条链路画出来，
并提供两种看法：

| 模式 | 回答的问题 |
|---|---|
| 意图图谱 | 这句话会连到哪里：意图节点、字段组、播报方式三列连线 |
| 问法矩阵 | 哪类问法多、哪里还空着：14 意图 × 6 类问法的计数 |

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

## 验证

```
# 后端（含新增的 8 项问法边界测试）
cd voice-assistant && python -m pytest tests -q
# → 547 passed, 2157 subtests passed

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
```

专项覆盖：导航与标题、统计来自接口、14/8/3 个节点、选中只高亮相关连线、切模式保留选择、
矩阵单元格联动详情、过滤、Esc 清除、只读（浏览期间无 POST）、三档窗口无横向溢出。

截图（v1.5.0，模拟遥测）：[意图图谱](ui-preview/v1.5.0/knowledge-graph.png)、
[选中详情](ui-preview/v1.5.0/knowledge-detail.png)、[问法矩阵](ui-preview/v1.5.0/knowledge-matrix.png)。

## 已知边界

- 没有在 WebView2 真机窗口里目视验证，只在同版本 Edge 内核渲染。
- 分类计数是关键词启发式，只用于看分布，不参与匹配。
- 冲突检测只覆盖固定探针与规范问句，不代表全部 1,791 条问法都已检查。
- 链路里的遥测来自模拟快照；真实连接时同样的问法可能有不同结果。
- 「胎压多少」缺对象属于词库覆盖面，未改；补问法时说清左前/右前/左后/右后即可。
