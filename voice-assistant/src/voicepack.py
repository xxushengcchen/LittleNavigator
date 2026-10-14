"""语音包索引：解析 spmsg.txt，并按冷却策略抽取变体。

spmsg.txt 格式（官方 spmsg02）：
    首行 spmsg02，其后每行 `msgId, 檔名, "文字"`
    同一 msgId 多行 = 多變體，遊戲随机挑一条播
"""
from __future__ import annotations

import json
import pathlib
import random
import warnings
from dataclasses import dataclass, field


@dataclass
class Variant:
    file: str
    text: str
    # 这条变体所属的包目录。合并多个包（主包 + 助手素材）时必须记住，
    # 否则文件会被拿到别的包目录里去找 —— 实测踩过：AST_* 全部报「音频不存在」。
    dir: pathlib.Path | None = None


@dataclass
class VoicePack:
    root: pathlib.Path
    version: str = "unknown"
    variants: dict[str, list[Variant]] = field(default_factory=dict)

    # ---- 加载 ----

    @classmethod
    def load(cls, root: str | pathlib.Path) -> "VoicePack":
        root = pathlib.Path(root)
        spmsg = root / "spmsg.txt"
        if not spmsg.exists():
            raise FileNotFoundError(f"找不到 spmsg.txt：{spmsg}")
        pack = cls(root=root)
        lines = spmsg.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if not lines or lines[0].strip().lower() != "spmsg02":
            raise ValueError(f"语音包文件头无效：{spmsg}")
        for line in lines:
            s = line.strip()
            if not s:
                continue
            if s.startswith(";"):
                if "version:" in s and pack.version == "unknown":
                    pack.version = s.split("version:")[1].split()[0]
                continue
            if s.lower().startswith("spmsg"):
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) < 3:
                continue
            msg_id, wav = parts[0], parts[1]
            if pathlib.Path(wav).is_absolute() or ".." in pathlib.Path(wav).parts:
                raise ValueError(f"语音文件路径超出包目录：{wav}")
            text = ",".join(parts[2:]).strip().strip('"')
            if wav.upper() == "NULL" or text == "NULL":
                continue
            pack.variants.setdefault(msg_id, []).append(Variant(wav, text, root))
        return pack

    # ---- 查询 ----

    @classmethod
    def load_many(cls, roots: list[str | pathlib.Path]) -> "VoicePack":
        """合并多个包（主语音包 + 助手专用素材）。后面的包补前面的。"""
        merged: VoicePack | None = None
        for r in roots:
            if not r:
                continue
            p = pathlib.Path(r)
            part = cls.load(p)
            if merged is None:
                merged = part
            else:
                for mid, vs in part.variants.items():
                    merged.variants.setdefault(mid, []).extend(vs)
        if merged is None:
            raise FileNotFoundError(f"这些路径里都没有 spmsg.txt：{roots}")
        return merged

    def render(self, plan: list[str]) -> str:
        """把 msgId 序列拼成可读文字（取每条第一个变体，仅供预览）。"""
        out = []
        for mid in plan:
            vs = self.variants.get(mid)
            out.append(vs[0].text if vs else f"⟪缺{mid}⟫")
        return "".join(out)

    def has(self, msg_id: str) -> bool:
        return bool(self.variants.get(msg_id))

    def texts(self, msg_id: str) -> list[str]:
        return [v.text for v in self.variants.get(msg_id, [])]

    def path_of(self, variant: Variant) -> pathlib.Path:
        return (variant.dir or self.root) / variant.file

    def stats(self) -> dict:
        counts = [len(v) for v in self.variants.values()]
        return {
            "msgIds": len(self.variants),
            "clips": sum(counts),
            "avg_variants": round(sum(counts) / max(len(counts), 1), 2),
        }


class VariantPicker:
    """冷却加权随机：冷却期内的变体不进候选池，跨场次保持连续。

    冷却长度 = min(变体数 - 2, cooldown)。变体越多冷却越长；
    变体只有 1–2 条时退化为纯随机，不会因为没有候选而卡住。
    """

    def __init__(self, pack: VoicePack, cooldown: int = 4,
                 state_file: str | pathlib.Path | None = None):
        self.pack = pack
        self.cooldown = cooldown
        self.state_file = pathlib.Path(state_file) if state_file else None
        # msgId -> 最近用过的变体下标（新的在前）
        self.recent: dict[str, list[int]] = {}
        self._load_state()

    def _load_state(self) -> None:
        if self.state_file and self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.recent = {k: list(v) for k, v in data.get("recent", {}).items()}
            except Exception:
                self.recent = {}

    def save(self) -> None:
        if not self.state_file:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"recent": self.recent}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError as exc:
            warnings.warn(f"冷却状态未保存：{exc}", RuntimeWarning)

    def cooldown_len(self, n: int) -> int:
        return max(0, min(n - 2, self.cooldown))

    def pick_index(self, msg_id: str, save: bool = True) -> int:
        n = len(self.pack.variants.get(msg_id, []))
        if n == 0:
            raise KeyError(f"语音包里没有 {msg_id}")
        cd = self.cooldown_len(n)
        used = self.recent.get(msg_id, [])[:cd]
        pool = [i for i in range(n) if i not in used]
        if not pool:                      # 冷却长度异常时退化
            pool = list(range(n))
        idx = random.choice(pool)
        self.recent[msg_id] = [idx] + [i for i in used if i != idx][: max(cd - 1, 0)]
        if save:
            self.save()
        return idx

    def pick(self, msg_id: str, save: bool = True) -> Variant:
        vs = self.pack.variants.get(msg_id, [])
        if not vs:
            raise KeyError(f"语音包里没有 {msg_id}")
        return vs[self.pick_index(msg_id, save=save)]

    def preview(self, msg_id: str) -> Variant:
        """给界面预览用：随机挑一条，不动冷却状态。"""
        vs = self.pack.variants.get(msg_id, [])
        if not vs:
            raise KeyError(f"语音包里没有 {msg_id}")
        return random.choice(vs)
