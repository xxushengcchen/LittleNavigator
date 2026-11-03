"""F03/F06 regression: unsaved AI drafts survive mode switch; stale model lists dropped.

前端没有可用的 DOM 运行环境时，这些断言校验真实控制流顺序（谁在 await 前后、
谁先作废代次），而不是只看函数是否存在。
"""
from __future__ import annotations

import pathlib
import re
import unittest

JS = (pathlib.Path(__file__).resolve().parents[1]
      / "app" / "static" / "app.js").read_text(encoding="utf-8")


def function_body(name: str) -> str:
    start = JS.index(f"function {name}(")
    depth = 0
    for index in range(start, len(JS)):
        char = JS[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return JS[start:index + 1]
    raise AssertionError(name)


class ModelListGenerationTests(unittest.TestCase):
    def test_generation_is_invalidated_on_provider_and_url_change(self):
        preset = JS[JS.index('preset.addEventListener("change"'):]
        preset = preset[:preset.index("});")]
        self.assertIn("bumpAiModelsGeneration()", preset,
                      "换服务商必须作废旧模型列表请求")
        url = JS[JS.index('urlInput.addEventListener("change"'):]
        url = url[:url.index("});")]
        self.assertIn("bumpAiModelsGeneration()", url,
                      "改接口地址必须作废旧模型列表请求")

    def test_stale_response_is_dropped_before_caching(self):
        click = JS[JS.index('modelsBtn.addEventListener("click"'):]
        click = click[:click.index("});\n    }")]
        guard = click.index("gen !== aiModelsGeneration")
        cache_write = click.index("aiModelCache = list")
        error_path = click.index("aiModelsError(e)")
        self.assertLess(guard, cache_write,
                        "旧响应必须在写入缓存之前被拦下")
        self.assertLess(guard, error_path)


class DraftPreservationTests(unittest.TestCase):
    def test_switch_snapshots_before_save_and_restores_after_reload(self):
        switch = JS[JS.index('globalSwitch.addEventListener("change"'):]
        switch = switch[:switch.index("});\n    }")]
        snap = switch.index("aiDraftSnapshot(panel)")
        save = switch.index("await save(patch")
        reload = switch.index("await loadSettings()")
        restore = switch.index("aiDraftRestore(document")
        self.assertLess(snap, save, "先抓草稿再保存")
        self.assertLess(reload, restore, "重绘之后再恢复草稿")

    def test_snapshot_covers_url_model_and_key(self):
        body = function_body("aiDraftSnapshot")
        for field in ("ira-ai-url", "ira-ai-model", "ira-ai-key"):
            self.assertIn(field, body, f"{field} 必须作为草稿保留")


if __name__ == "__main__":
    unittest.main()
