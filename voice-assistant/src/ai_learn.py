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

# when 子句必须整体匹配：左值 [op] 右值 或 true
_WHEN_RE = re.compile(
    r"^\s*(?:true|"
    r"(?P<lhs>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<op>>=|<=|==|!=|>|<)\s*"
    r"(?P<rhs>[A-Za-z_][A-Za-z0-9_]*|-?\d+(?:\.\d+)?)"
    r"(?:\s*\*\s*(?P<mul>-?\d+(?:\.\d+)?))?"
    r")\s*$"
)

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 匹配阈值：规范化后字符相似度。0.82 是在「换个说法也能命中」与
# 「别把不同问题混在一起」之间取的折中。
_MATCH_RATIO = 0.82

# 连续错多少次自动禁用
_DISABLE_AFTER_ERRORS = 2


class LearnError(RuntimeError):
    pass


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _flatten_values(ctx: dict) -> dict[str, float]:
    """把上下文展平成 {字段名: 数值}，供模板求值。只收数值。"""
    out: dict[str, float] = {}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, dict):
                    walk(value)
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    out.setdefault(str(key), float(value))
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(ctx)
    return out


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
        if not isinstance(name, str) or name not in ALLOWED_FIELDS:
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

    if not has_fallback:
        return "缺少 when 为 true 的兜底分支"
    return None


def _eval_when(when: str, values: dict[str, float]) -> bool:
    """对一条 when 子句求值。无法解析或字段缺失都当不匹配。"""
    when = (when or "").strip()
    if when == "true":
        return True
    match = _WHEN_RE.match(when)
    if match is None:
        return False

    def resolve(token: str) -> float | None:
        token = token.strip()
        try:
            return float(token)
        except ValueError:
            return values.get(token)

    lhs = resolve(match.group("lhs"))
    if lhs is None:
        return False
    if match.group("rhs") is None:
        return float(lhs) != 0        # 极少数情况：只写了字段名
    rhs = resolve(match.group("rhs"))
    if rhs is None:
        return False
    if match.group("mul") is not None:
        rhs = rhs * float(match.group("mul"))

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
    return False


def fill_template(template: dict, ctx: dict) -> str | None:
    """按上下文填出要说的话。任一占位符缺值就返回 None（不许填半句）。"""
    if validate_template(template) is not None:
        return None
    values = _flatten_values(ctx)
    for rule in template["rules"]:
        if not _eval_when(rule["when"], values):
            continue
        say = rule["say"]
        placeholders = _PLACEHOLDER_RE.findall(say)
        missing = [p for p in placeholders if p not in values]
        if missing:
            return None
        try:
            return _PLACEHOLDER_RE.sub(
                lambda m: _format_number(values[m.group(1)]), say)
        except (KeyError, ValueError):
            return None
    return None


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
    def add_knowledge(self, question: str, answer: str,
                      clip_path: str | None = None) -> dict:
        rows = self._read(self._knowledge_path)
        entry = {"id": uuid.uuid4().hex[:12], "q": question, "a": answer,
                 "clip": clip_path, "ok": 0, "err": 0, "disabled": False}
        rows.append(entry)
        self._write(self._knowledge_path, rows)
        return entry

    def add_template(self, question: str, template: dict) -> dict:
        reason = validate_template(template)
        if reason is not None:
            raise LearnError(reason)
        rows = self._read(self._templates_path)
        entry = {"id": uuid.uuid4().hex[:12], "q": question,
                 "template": template, "ok": 0, "err": 0, "disabled": False}
        rows.append(entry)
        self._write(self._templates_path, rows)
        return entry

    def add_from_review(self, record: dict) -> str:
        """复盘确认正确后入库。返回 "knowledge" / "template" / "rejected"。

        动态型**必须**带合法模板：没有模板的动态回答只在那刻成立，
        存下来下次就是播报过期数值。
        """
        question = (record or {}).get("question") or ""
        answer = (record or {}).get("answer") or ""
        if not question.strip():
            return "rejected"
        if record.get("dynamic"):
            template = record.get("template")
            if not isinstance(template, dict):
                return "rejected"
            if validate_template(template) is not None:
                return "rejected"
            self.add_template(question, template)
            return "template"
        if not answer.strip():
            return "rejected"
        self.add_knowledge(question, answer, record.get("clip_path"))
        return "knowledge"

    # ------------------------------------------------------------------ 查询
    def _live(self, path: pathlib.Path) -> list[dict]:
        return [r for r in self._read(path) if not r.get("disabled")]

    def match(self, question: str) -> tuple[str, dict] | None:
        """模糊匹配学习库，模板优先（模板能重算数值，比固定答案可靠）。"""
        q = normalize(question or "")
        if not q:
            return None
        best: tuple[float, str, dict] | None = None
        for kind, path in (("template", self._templates_path),
                           ("knowledge", self._knowledge_path)):
            for row in self._live(path):
                ratio = _similar(q, normalize(row.get("q", "")))
                if ratio < _MATCH_RATIO:
                    continue
                if best is None or ratio > best[0]:
                    best = (ratio, kind, row)
        if best is None:
            return None
        return best[1], best[2]

    def mark(self, entry_id: str, ok: bool) -> bool:
        """标记一次复用结果。错两次自动禁用，正确则清零。"""
        for path in (self._templates_path, self._knowledge_path):
            rows = self._read(path)
            hit = False
            for row in rows:
                if row.get("id") != entry_id:
                    continue
                hit = True
                if ok:
                    row["ok"] = int(row.get("ok", 0)) + 1
                    row["err"] = 0
                else:
                    row["err"] = int(row.get("err", 0)) + 1
                    if row["err"] >= _DISABLE_AFTER_ERRORS:
                        row["disabled"] = True
            if hit:
                self._write(path, rows)
                return True
        return False

    def stats(self) -> dict:
        return {"templates": self._read(self._templates_path),
                "knowledge": self._read(self._knowledge_path)}

    def clear(self) -> None:
        self._write(self._templates_path, [])
        self._write(self._knowledge_path, [])
