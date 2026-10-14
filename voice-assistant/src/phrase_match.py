"""受限短语模糊匹配：ASR 文本 → 意图（或明确拒绝）。

为什么不用通用意图分类：
  · 这个产品的红线是「绝不猜」。只认 intents_examples.yaml 里列出的问法，
    对不上任何一条就拒绝，宁可让用户重说一遍。
  · 询问范围是封闭的 10 个遥测意图，完全枚举得起。

评分三路取最高：
  · 字符编辑距离相似度        —— 认错字但字形相近的情况
  · 拼音（声母韵母，无声调）  —— 「油还购吗」/「油还够吗」这类同音字错误
  · 子串包含                  —— 短句落在长句里，比如「胎压」⊂「胎压多少」

命中条件：最佳意图得分 ≥ accept 且领先第二名 ≥ margin，否则拒绝。
阈值可以在 config 里调，测试脚本会给出推荐值。
"""
from __future__ import annotations

import pathlib
import re
import sys
from dataclasses import dataclass, field

import yaml
from opencc import OpenCC
from pypinyin import Style, lazy_pinyin
from .question_forms import canonicalize

DEFAULT_LIBRARY = pathlib.Path(__file__).resolve().parents[1] / "intents_examples.yaml"


def default_library() -> pathlib.Path:
    """短语库路径：打包后优先读 exe 旁边那份（用户可以自己加词），
    没有就退回内置那份。"""
    if getattr(sys, "frozen", False):
        ext = pathlib.Path(sys.executable).resolve().parent / "intents_examples.yaml"
        if ext.exists():
            return ext
    return DEFAULT_LIBRARY

_CC = OpenCC("t2s")
_KEEP = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
MIN_QUERY_CHARS = 3          # 更短的一律拒绝：多半是 ASR 听岔的半截话


def normalize(text: str) -> str:
    """繁→简、去标点空白、全角转半角、英文小写。"""
    t = _CC.convert(text or "")
    t = t.replace("　", "")
    out = []
    for ch in t:
        if "\uff01" <= ch <= "\uff5e":          # 全角字符
            ch = chr(ord(ch) - 0xFEE0)
        out.append(ch)
    clean = _KEEP.sub("", "".join(out).lower())
    return re.sub(r"([\u4e00-\u9fff])\1+", r"\1", clean)  # 不吞掉 11、100 等数值。


def _syllables(text: str) -> list[str]:
    return [s for s in lazy_pinyin(text, style=Style.NORMAL, errors="ignore") if s]


def _lev(a: list, b: list) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _sim(a: list, b: list) -> float:
    if not a and not b:
        return 1.0
    return 1.0 - _lev(a, b) / max(len(a), len(b))


@dataclass
class Phrase:
    intent: str
    text: str
    norm: str
    chars: list[str]
    syls: list[str]
    pystr: str


@dataclass
class Hit:
    intent: str | None
    score: float
    phrase: str
    margin: float
    reason: str
    ranked: list[tuple[str, float, str]] = field(default_factory=list)


def load_library(path: str | pathlib.Path = DEFAULT_LIBRARY) -> list[Phrase]:
    if path == DEFAULT_LIBRARY:
        path = default_library()
    data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
    out: list[Phrase] = []
    for intent, spec in (data or {}).items():
        if not isinstance(spec, dict):
            continue                      # refuse_phrases 这类非意图条目
        if (spec or {}).get("route") != "state_layer":
            continue                      # 只认能直接回答的意图
        for raw in (spec or {}).get("examples") or []:
            norm = normalize(str(raw))
            if len(norm) < MIN_QUERY_CHARS:
                continue
            syls = _syllables(norm)
            out.append(Phrase(intent, str(raw), norm, list(norm), syls, "".join(syls)))
    return out


def load_negatives(path: str | pathlib.Path = DEFAULT_LIBRARY) -> list[str]:
    """短语库里的显式拒绝清单：命中即拒绝，不看相似度。

    背景：拼接拼音串的相似度通道对「共享长拼音子串」很宽松（实测
    「现在几点了」对「abs现在几档」能拿到 0.80），这类越界句靠阈值堵不干净。
    """
    if path == DEFAULT_LIBRARY:
        path = default_library()
    data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}
    return [str(x) for x in (data.get("refuse_phrases") or []) if str(x).strip()]


class PhraseMatcher:
    def __init__(self, library: str | pathlib.Path = DEFAULT_LIBRARY,
                 accept: float = 0.66, margin: float = 0.05):
        self.phrases = load_library(library)
        self.negatives = [normalize(x) for x in load_negatives(library)]
        self.accept = accept
        self.margin = margin
        unique={}
        for p in self.phrases:
            norm=canonicalize(p.norm)
            syls=_syllables(norm)
            unique.setdefault((p.intent,norm),Phrase(p.intent,p.text,norm,list(norm),syls,''.join(syls)))
        self._canonical_candidates=list(unique.values())
        for p in self.phrases:unique.setdefault((p.intent,p.norm),p)
        self._candidates=list(unique.values())
        self._exact={}
        for p in self._canonical_candidates:self._exact.setdefault(p.norm,[]).append((p.intent,1.0,p.text))

    # ------------------------------------------------------------------
    def score_phrase(self, q_norm: str, q_chars: list[str], q_syls: list[str],
                     q_pystr: str, p: Phrase) -> float:
        if q_norm == p.norm:
            return 1.0
        sim = max(_sim(q_chars, p.chars), _sim(q_syls, p.syls), _sim(q_pystr, p.pystr))
        # 子串包含：短的完整落在长的里面，按长度比给 0.8~0.97
        if len(q_norm) >= MIN_QUERY_CHARS and (q_norm in p.norm or p.norm in q_norm):
            ratio = min(len(q_norm), len(p.norm)) / max(len(q_norm), len(p.norm))
            sim = max(sim, 0.80 + 0.17 * ratio)
        return sim

    def rank(self, text: str) -> list[tuple[str, float, str]]:
        """→ [(意图, 得分, 短语)] 按得分降序。"""
        q_norm = canonicalize(normalize(text))
        if len(q_norm) < MIN_QUERY_CHARS:
            return []
        if q_norm in self._exact:return self._exact[q_norm]
        q_chars, q_syls = list(q_norm), _syllables(q_norm)
        q_pystr = "".join(q_syls)
        scored = [(p.intent, self.score_phrase(q_norm, q_chars, q_syls, q_pystr, p), p.text)
                  for p in self._candidates]
        scored.sort(key=lambda x: x[1], reverse=True)
        # 同一意图只保留最高分那条
        best: dict[str, tuple[str, float, str]] = {}
        for intent, s, phrase in scored:
            if intent not in best:
                best[intent] = (intent, s, phrase)
        return sorted(best.values(), key=lambda x: x[1], reverse=True)

    def match(self, text: str) -> Hit:
        if normalize(text) in self.negatives:
            return Hit(None, 0.0, "", 0.0, "命中短语库的拒绝清单")
        ranked = self.rank(text)
        if not ranked:
            return Hit(None, 0.0, "", 0.0, f"「{text}」太短或为空，拒绝", ranked)
        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        margin = top[1] - (second[1] if second else 0.0)
        if top[1] < self.accept:
            return Hit(None, top[1], top[2], margin,
                       f"最高分 {top[1]:.2f} < 阈值 {self.accept:.2f}，拒绝", ranked)
        if second and margin < self.margin:
            return Hit(None, top[1], top[2], margin,
                       f"「{top[0]}」与「{second[0]}」只差 {margin:.2f}，分不清，拒绝", ranked)
        return Hit(top[0], top[1], top[2], margin, f"命中「{top[2]}」({top[1]:.2f})", ranked)


if __name__ == "__main__":     # 手动试几句：python -m src.phrase_match
    import sys

    m = PhraseMatcher()
    print(f"短语库 {len(m.phrases)} 条")
    tests = sys.argv[1:] or [
        "油还够吗", "油还購吗", "还剩多少圈", "还剩多少油",
        "胎压多少", "我上圈多少", "今天晚饭吃什么", "啊",
    ]
    for t in tests:
        h = m.match(t)
        print(f"{t:12s} → {h.intent or 'REFUSE':16s} {h.reason}")
