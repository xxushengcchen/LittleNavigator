#!/usr/bin/env python
"""統一的文案載入器
讀取 content/messages*.json（主檔）與 content/variants-plus-*.json（變體擴充補充檔），
把補充檔的 add 陣列附加到同名 msgId 的 variants 之後（自動去重）。
主檔本身不會被改寫——變體擴充一律走補充檔，避免覆蓋手改內容。
"""
from __future__ import annotations

import json
import pathlib


def count_sound_variants(item: dict) -> int:
    """實際會發聲的變體數（排除刻意靜音的 NULL）。"""
    vs = item.get("variants") or ([item["text"]] if item.get("text") else [])
    return len([v for v in vs if v != "NULL"])


def load_items(root: pathlib.Path, patterns: list[str] | None = None,
               supplements: bool | None = None) -> tuple[list[dict], dict]:
    """回傳 (items, instructs)。
    items 依「主檔優先」排序；variants-plus-* 的內容附加在後。

    patterns 省略時讀 content/messages*.json；給了就只讀指定的檔案／萬用字元。
    """
    pats = patterns or ["content/messages*.json"]
    # 只有載入主檔時才套用變體擴充補充檔；載入特定檔案（如助手素材）時不套
    if supplements is None:
        supplements = any("messages" in p for p in pats)
    main: list[pathlib.Path] = []
    for pat in pats:
        hits = sorted(root.glob(pat)) if any(c in pat for c in "*?[") else [root / pat]
        main.extend(h for h in hits if h.exists())
    main.sort(key=lambda h: (0 if h.name == "messages.json" else 1, h.name))

    items: list[dict] = []
    index: dict[str, dict] = {}
    instructs: dict = {}

    for f in main:
        doc = json.loads(f.read_text(encoding="utf-8"))
        instructs.update(doc.get("meta", {}).get("instructs", {}))
        for it in doc["items"]:
            if it["msgId"] in index:
                continue
            it = dict(it)
            it.setdefault("variants", [it["text"]] if it.get("text") else [])
            index[it["msgId"]] = it
            items.append(it)

    added = 0
    for f in (sorted(root.glob("content/variants-plus-*.json")) if supplements else []):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for sup in doc.get("items", []):
            target = index.get(sup["msgId"])
            if target is None:
                print(f"  警告：{f.name} 的 {sup['msgId']} 在主檔中不存在，略過")
                continue
            for v in sup.get("add", []):
                if v not in target["variants"]:
                    target["variants"].append(v)
                    added += 1

    if added:
        print(f"變體擴充：從補充檔附加 {added} 條")
    return items, instructs
