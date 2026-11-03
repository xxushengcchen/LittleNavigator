"""学习库：把赛后确认正确的问答沉淀下来，下次不必再调模型。

为什么不能直接缓存成品答案：
    模型回答是「你的油还够跑 6.2 圈，比赛还剩 5 圈，不用进站」——这段话只在
    那一刻成立。下次油量不同，直接播缓存就是播报过期数据，违反「绝不猜」。
    所以分两类：知识型（不随比赛变化）缓存完整问答 + 语音；动态型只缓存
    **条件化模板**，下次用当下数值重新求值。

安全底线：模板里的 `when` 表达式绝不使用 eval/exec。这里实现一个只认
`字段 op 数字|字段 [* 数字]` 的极小解析器——用户学习库里存的表达式来自模型，
把模型的输出交给 eval 等于把任意代码执行权交给它。
"""
from __future__ import annotations

import json
import pathlib
import re
import uuid
from difflib import SequenceMatcher

from .phrase_match import normalize
from .race_context import ALLOWED_FIELDS

# 允许的比较运算符。顺序无关，解析时按长度降序匹配避免 >= 被 > 吃掉。
_OPS = (">=", "<=", "==", "!=", ">", "<")

# 字段路径：审计复审 A19，数组叶子要能写成 segments.best_loss[0].delta_s。
_FIELD_PATH = r"[A-Za-z_][A-Za-z0-9_]*(?:\[\d+\]|\.[A-Za-z_][A-Za-z0-9_]*)*"
# when 子句必须整体匹配：左值 [op] 右值 或 true
_WHEN_RE = re.compile(
    r"^\s*(?:true|"
    r"(?P<lhs>" + _FIELD_PATH + r")\s*"
    r"(?P<op>>=|<=|==|!=|>|<)\s*"
    r"(?P<rhs>" + _FIELD_PATH + r"|-?\d+(?:\.\d+)?)"
    r"(?:\s*\*\s*(?P<mul>-?\d+(?:\.\d+)?))?"
    r")\s*$"
)

_PLACEHOLDER_RE = re.compile(r"\{(" + _FIELD_PATH + r")\}")

# 匹配阈值：规范化后字符相似度。0.82 是在「换个说法也能命中」与
# 「别把不同问题混在一起」之间取的折中。
_MATCH_RATIO = 0.82

# 连续错多少次自动禁用
_DISABLE_AFTER_ERRORS = 2

# 「语义护栏」（审计 A06）：字符相似度分不清前/后车、左右轮、否定与时间目标，
# 只靠 0.82 阈值会把「后车圈速」的答案播给「前车圈速」。这里从问句里抽
# 一组关键 token，两侧必须完全一致才允许命中；不一致一律当没命中退回模型——
# 多花一次调用换「不播错对象」是值得的。
_GUARD_TARGET_WORDS = (
    "前车", "后车", "前轮", "后轮", "左前", "右前", "左后", "右后",
    "前面", "后面", "前方", "后方", "头车", "领跑", "对手", "队友",
    "别人",
    # 审计复审：第一/二人称几乎出现在每个问题里，放进护栏会让
    # 「我上一圈圈速」永远匹配不上库里的「上一圈圈速」，白多一次模型调用。
    # 这里只保留真正会换对象的限定词。
    "最快", "最慢", "最好", "最差", "平均", "本场", "上一场", "上场比赛",
    "正赛", "排位", "练习", "本节", "本段", "上一段",
)
_GUARD_LAP_WORDS = (
    "上一圈", "下一圈", "上圈", "下圈", "这一圈", "那一圈", "这圈", "那圈",
    "本圈", "最近一圈", "当前圈", "前一圈", "后一圈",
    "上一段", "下一段", "上段", "下段", "本段", "该段",
)
_GUARD_NEGATIONS = ("不", "没", "别", "未", "无", "非")
_GUARD_UNITS = ("升", "圈", "秒", "分", "度", "公里", "米", "档", "名", "次")
_GUARD_SECTOR_RE = re.compile(r"第\s*([0-9]+|[一二两三四五六七八九十]+)\s*段")
_GUARD_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _guard_signature(question: str) -> frozenset[str]:
    """抽问句里的语义关键 token；空集合表示没有可换错的对象。"""
    q = normalize(question or "")
    sig: set[str] = set()
    for word in _GUARD_TARGET_WORDS:
        if word in q:
            sig.add("target:" + word)
    for word in _GUARD_LAP_WORDS:
        if word in q:
            sig.add("lap:" + word)
    for ch in _GUARD_NEGATIONS:
        if ch in q:
            sig.add("neg:" + ch)
    for word in _GUARD_UNITS:
        if word in q:
            sig.add("unit:" + word)
    for match in _GUARD_SECTOR_RE.finditer(q):
        sig.add("sector:" + match.group(1))
    for match in _GUARD_NUM_RE.finditer(q):
        sig.add("num:" + match.group(0))
    return frozenset(sig)


class LearnError(RuntimeError):
    pass


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _flatten_values(ctx: dict) -> dict[str, float | str]:
    """把上下文展平成 {字段名: 值}，供模板求值。

    审计 A19：
      · 数组里的对象叶子（如 segments.best_loss[].delta_s）原先被整个丢掉，
        改成递归 dict/list；
      · 白名单里的口语字符串（``*_spoken``）原先没有通路，改成显式允许——
        只认名字以 ``_spoken`` 结尾的字段，其它字符串（flags 之类）不参与填值；
      · 同一个叶子出现多个不同值（例如 best_loss 有两段的 delta_s）视为歧义，
        整份模板放弃，而不是随便挑第一个。
    """
    values: dict[str, float | str] = {}
    ambiguous: set[str] = set()

    def put(key: str, value: float | str) -> None:
        if key in values:
            if values[key] != value:
                ambiguous.add(key)
            return
        values[key] = value

    def walk(node, key: str | None = None, *,
             short: str | None = None, full: str = "") -> None:
        """short 是叶子短名（含 [i] 索引），full 是带父路径的全名。

        审计复审：只按叶子名去重会让「best_loss 有两段」整键丢弃，
        模板永远算不出来。这里同时登记带索引的键（delta_s[0] /
        segments.best_loss[0].delta_s），模板可以显式引用某一段；
        不带索引的短名只在所有数组元素一致时才保留。
        """
        if isinstance(node, dict):
            for child_key, value in node.items():
                child = str(child_key)
                walk(value, child,
                     short=(f"{short}.{child}" if short else child),
                     full=(f"{full}.{child}" if full else child))
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, key,
                     short=(f"{short}[{index}]" if short else f"{key}[{index}]"),
                     full=(f"{full}[{index}]" if full else f"{key}[{index}]"))
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)) and short:
            put(short, float(node))
            if full and full != short:
                put(full, float(node))
            if key and key != short:
                put(key, float(node))
        elif isinstance(node, str) and short and short.endswith("_spoken"):
            put(short, node)
            if full and full != short:
                put(full, node)
            # 保留不带父路径的叶子名（模板里通常直接写 lap_last_spoken）。
            if key and key != short:
                put(key, node)

    walk(ctx)
    for key in ambiguous:
        values.pop(key, None)
    return values


def _base_field(name: str) -> str:
    """``segments.best_loss[0].delta_s`` -> ``segments``（用于白名单判断）。"""
    return re.split(r"[\[.]", str(name or ""), maxsplit=1)[0]


def validate_template(template: dict) -> str | None:
    """校验模板结构。返回 None 表示通过，否则返回失败原因。

    五条规则（spec 17.3）：
      1. params 必须在 ALLOWED_FIELDS 内
      2. when 只能是简单比较，不允许函数调用 / 属性访问 / 括号
      3. 必须有 when == "true" 的兜底分支
      4. say 里的占位符必须都来自 params
      5. 规则非空
    """
    if not isinstance(template, dict):
        return "模板不是对象"
    params = template.get("params")
    rules = template.get("rules")
    if not isinstance(params, list) or not params:
        return "params 必须是非空列表"
    if not isinstance(rules, list) or not rules:
        return "rules 必须是非空列表"

    for name in params:
        # 审计复审 A19：允许带索引的路径（segments.best_loss[0].delta_s），
        # 白名单按第一段判断，其余段仍受 _FIELD_PATH 正则约束。
        if (not isinstance(name, str)
                or not re.fullmatch(_FIELD_PATH, name)
                or _base_field(name) not in ALLOWED_FIELDS):
            return f"params 含白名单外字段：{name!r}"

    has_fallback = False
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            return f"第 {i} 条规则不是对象"
        when = rule.get("when")
        say = rule.get("say")
        if not isinstance(when, str):
            return f"第 {i} 条规则缺 when"
        if not isinstance(say, str) or not say.strip():
            return f"第 {i} 条规则缺 say"
        match = _WHEN_RE.match(when)
        if match is None:
            return f"第 {i} 条规则的 when 无法解析：{when!r}"
        # 占位符检查对**所有**分支都要做，包括兜底分支——
        # 否则 `{"when": "true", "say": "{laps_of_fuel}"}` 会漏过 params 校验，
        # 下次填值时才发现缺字段。
        for placeholder in _PLACEHOLDER_RE.findall(say):
            if placeholder not in params:
                return f"第 {i} 条规则使用了 params 之外的占位符：{placeholder}"
        if when.strip() == "true":
            has_fallback = True
            continue
        for field in (match.group("lhs"), match.group("rhs")):
            if field and not field.replace("-", "").replace(".", "").isdigit():
                if field not in params:
                    return f"第 {i} 条规则引用了 params 之外的字段：{field}"
                # A19：口语字符串字段只能整段填进 say，不能拿来做大小比较。
                if field.endswith("_spoken"):
                    return f"第 {i} 条规则的 when 不能引用口语字符串字段：{field}"

    if not has_fallback:
        return "缺少 when 为 true 的兜底分支"
    return None


def _eval_when(when: str, values: dict[str, float]) -> bool | None:
    """对一条 when 子句求值：True / False / None（未知）。

    审计 A08：以前「字段缺失」和「条件不成立」都返回 False，于是缺数据的
    规则会被跳过，最后落到 ``when == "true"`` 的兜底分支——用缺条件的数据
    说出一句确定的话。现在缺字段返回 None（未知），由 fill_template
    整体放弃这个模板，而不是拿兜底句冒充结论。
    """
    when = (when or "").strip()
    if when == "true":
        return True
    match = _WHEN_RE.match(when)
    if match is None:
        return None               # 解析不了 = 不知道，不是「不匹配」

    def resolve(token: str) -> float | None:
        token = token.strip()
        try:
            return float(token)
        except ValueError:
            value = values.get(token)
            return float(value) if isinstance(value, (int, float)) else None

    lhs = resolve(match.group("lhs"))
    if lhs is None:
        return None
    if match.group("rhs") is None:
        return float(lhs) != 0        # 极少数情况：只写了字段名
    rhs = resolve(match.group("rhs"))
    if rhs is None:
        return None
    if match.group("mul") is not None:
        try:
            rhs = rhs * float(match.group("mul"))
        except (TypeError, ValueError):
            return None

    op = match.group("op")
    if op == ">=":
        return lhs >= rhs
    if op == "<=":
        return lhs <= rhs
    if op == ">":
        return lhs > rhs
    if op == "<":
        return lhs < rhs
    if op == "==":
        return abs(lhs - rhs) < 1e-9
    if op == "!=":
        return abs(lhs - rhs) >= 1e-9
    return None


def fill_template(template: dict, ctx: dict) -> str | None:
    """按上下文填出要说的话。任一占位符缺值就返回 None（不许填半句）。"""
    if validate_template(template) is not None:
        return None
    values = _flatten_values(ctx)
    for rule in template["rules"]:
        matched = _eval_when(rule["when"], values)
        if matched is None:
            # 这条分支需要的字段读不到：不能确定该不该说，整份模板放弃。
            return None
        if not matched:
            continue
        say = rule["say"]
        placeholders = _PLACEHOLDER_RE.findall(say)
        missing = [p for p in placeholders if p not in values]
        if missing:
            return None
        try:
            return _PLACEHOLDER_RE.sub(
                lambda m: _format_value(values[m.group(1)]), say)
        except (KeyError, ValueError):
            return None
    return None


def _format_value(value: float | str) -> str:
    """口语字符串原样使用；数值走 `_format_number`。"""
    if isinstance(value, str):
        return value
    return _format_number(value)


def _format_number(value: float) -> str:
    """整数值不带小数点，其余保留一位——播报里「6」比「6.0」自然。"""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


class LearnStore:
    """两类条目的持久化存储。

    文件布局（放在用户数据目录，不进安装目录也不进 git）：
        knowledge.jsonl   知识型问答
        templates.jsonl   动态型模板
    """

    def __init__(self, root: pathlib.Path | str):
        self.root = pathlib.Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._knowledge_path = self.root / "knowledge.jsonl"
        self._templates_path = self.root / "templates.jsonl"

    # ------------------------------------------------------------------ 读写
    @staticmethod
    def _read(path: pathlib.Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    @staticmethod
    def _write(path: pathlib.Path, rows: list[dict]) -> None:
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        path.write_text(text + ("\n" if text else ""), encoding="utf-8")

    # ------------------------------------------------------------------ 写入
    @staticmethod
    def _clean_review_id(value) -> str | None:
        """只接受非空字符串；其余一律当「没有来源记录」。"""
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
        return None

    def _find_by_review_id(self, path: pathlib.Path,
                           review_id: str) -> dict | None:
        for row in self._read(path):
            if row.get("review_id") == review_id:
                return row
        return None

    def add_knowledge(self, question: str, answer: str,
                      clip_path: str | None = None,
                      review_id: str | None = None) -> dict:
        rows = self._read(self._knowledge_path)
        entry = {"id": uuid.uuid4().hex[:12], "q": question, "a": answer,
                 "clip": clip_path, "ok": 0, "err": 0, "disabled": False}
        review_id = self._clean_review_id(review_id)
        if review_id is not None:
            entry["review_id"] = review_id
        rows.append(entry)
        self._write(self._knowledge_path, rows)
        return entry

    def add_template(self, question: str, template: dict,
                     review_id: str | None = None) -> dict:
        reason = validate_template(template)
        if reason is not None:
            raise LearnError(reason)
        rows = self._read(self._templates_path)
        entry = {"id": uuid.uuid4().hex[:12], "q": question,
                 "template": template, "ok": 0, "err": 0, "disabled": False}
        review_id = self._clean_review_id(review_id)
        if review_id is not None:
            entry["review_id"] = review_id
        rows.append(entry)
        self._write(self._templates_path, rows)
        return entry

    def add_from_review(self, record: dict) -> str:
        """复盘确认正确后入库。返回 "knowledge" / "template" / "rejected"。

        动态型**必须**带合法模板：没有模板的动态回答只在那刻成立，
        存下来下次就是播报过期数值。

        审计 A09：记录来源 history 的 ``review_id``。以前复盘「确认」时生成的新
        条目 id 与 history 里的 review_id 不同，之后标错永远找不到条目——
        现在同一条反馈再次确认是幂等的，标错则经 review_id 命中真正条目。
        """
        question = (record or {}).get("question") or ""
        answer = (record or {}).get("answer") or ""
        if not question.strip():
            return "rejected"
        review_id = self._clean_review_id((record or {}).get("review_id"))
        # 「确认」一条本来就是学习库命中的回答时，engine 会带上源条目 id；
        # 此时不要复制新条目，只把它当作一次成功复用（幂等）。
        source_id = self._clean_review_id((record or {}).get("source_id"))
        if source_id is not None:
            for kind, path in (("template", self._templates_path),
                               ("knowledge", self._knowledge_path)):
                for row in self._read(path):
                    if row.get("id") == source_id:
                        self.mark(source_id, ok=True)
                        return kind
        dynamic = (record or {}).get("dynamic")
        if dynamic is not None and not isinstance(dynamic, bool):
            # 说不清是不是动态的，就不能当静态知识存（审计 A05）。
            return "rejected"
        if dynamic is True:
            template = record.get("template")
            if not isinstance(template, dict):
                return "rejected"
            if validate_template(template) is not None:
                return "rejected"
            if (review_id is not None
                    and self._find_by_review_id(self._templates_path,
                                                review_id) is not None):
                return "template"          # 同一条确认重复提交：幂等
            self.add_template(question, template, review_id=review_id)
            return "template"
        if dynamic is None:
            # 缺这个字段时保守处理：静态知识可能依赖会过期的数值，
            # 除非明确标记 dynamic=false，否则不入库。
            return "rejected"
        if not answer.strip():
            return "rejected"
        if (review_id is not None
                and self._find_by_review_id(self._knowledge_path,
                                            review_id) is not None):
            return "knowledge"             # 同一条确认重复提交：幂等
        self.add_knowledge(question, answer, record.get("clip_path"),
                           review_id=review_id)
        return "knowledge"

    # ------------------------------------------------------------------ 查询
    def _live(self, path: pathlib.Path) -> list[dict]:
        return [r for r in self._read(path) if not r.get("disabled")]

    def match(self, question: str) -> tuple[str, dict] | None:
        """模糊匹配学习库，模板优先（模板能重算数值，比固定答案可靠）。

        审计 A06：字符相似度之外再加一道语义护栏——前/后车、左右轮、否定、
        时间目标与段号必须完全一致。护栏不同或抽不出时一律不命中。
        """
        q = normalize(question or "")
        if not q:
            return None
        q_signature = _guard_signature(question)
        best: tuple[float, str, dict] | None = None
        for kind, path in (("template", self._templates_path),
                           ("knowledge", self._knowledge_path)):
            for row in self._live(path):
                stored = row.get("q", "")
                if _guard_signature(stored) != q_signature:
                    continue
                ratio = _similar(q, normalize(stored))
                if ratio < _MATCH_RATIO:
                    continue
                if best is None or ratio > best[0]:
                    best = (ratio, kind, row)
        if best is None:
            return None
        return best[1], best[2]

    def mark(self, entry_id: str, ok: bool) -> bool:
        """标记一次复用结果。错两次自动禁用，正确则清零。

        审计 A09：``entry_id`` 既可以是入库条目自己的 id，也可以是复盘
        history 里的 ``review_id``（add_from_review 已把它记进入库条目）。
        以前只用随机 id 比对，bad 反馈永远找不到条目；现在两种都认。
        返回 False 表示库里没有这个 id（调用方可据此报 not_found）。
        """
        if not isinstance(entry_id, str) or not entry_id:
            return False
        hit = False
        for path in (self._templates_path, self._knowledge_path):
            rows = self._read(path)
            changed = False
            for row in rows:
                if (row.get("id") != entry_id
                        and row.get("review_id") != entry_id):
                    continue
                hit = True
                changed = True
                if ok:
                    row["ok"] = int(row.get("ok", 0)) + 1
                    row["err"] = 0
                else:
                    row["err"] = int(row.get("err", 0)) + 1
                    if row["err"] >= _DISABLE_AFTER_ERRORS:
                        row["disabled"] = True
            if changed:
                self._write(path, rows)
        return hit

    def stats(self) -> dict:
        return {"templates": self._read(self._templates_path),
                "knowledge": self._read(self._knowledge_path)}

    def clear(self) -> None:
        self._write(self._templates_path, [])
        self._write(self._knowledge_path, [])
