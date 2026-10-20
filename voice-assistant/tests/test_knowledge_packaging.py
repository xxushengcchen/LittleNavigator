"""知识库的打包回归：安装版里不能依赖源码文件。

背景（2026-10-20）：v1.5.0 首版装完后「知识库」页显示「读不到知识库」。
根因是 `_output_modes()` 去读 `telemetry_answers.py` / `router.py` 的源码，
而 PyInstaller 只打包 `.pyc`，`_internal/src/` 下没有 `.py`，于是

    FileNotFoundError: .../_internal/src/telemetry_answers.py

整个 `/api/knowledge` 返回 500。开发期跑服务时源码就在旁边，所以现有
浏览器回归全部通过，只有装完才会暴露。

本文件锁住两类错误：
  1) 运行期（build）不允许读任何 `.py` 文件；
  2) 声明式的 OUTPUT_MODES 必须和路由源码的实际分支一致，防止改成
     只发表、两处悄悄对不上。
"""
import pathlib
import sys
import unittest

from src import knowledge


class NoSourceReadTests(unittest.TestCase):
    """打包后没有 .py 文件：build() 必须能在这种前提下跑完。"""

    def test_build_reads_no_python_sources(self):
        touched = []
        real_read_text = pathlib.Path.read_text

        def guarded_read_text(self, *args, **kwargs):
            if self.suffix.lower() in {".py", ".pyc"}:
                touched.append(str(self))
                raise FileNotFoundError(
                    f"知识库在运行期读了源码文件：{self}")
            return real_read_text(self, *args, **kwargs)

        pathlib.Path.read_text = guarded_read_text
        try:
            data = knowledge.build(force=True)
        finally:
            pathlib.Path.read_text = real_read_text

        self.assertEqual(touched, [], "运行期不应读取源码文件")
        self.assertGreater(data["totals"]["intents"], 0)
        self.assertGreater(data["totals"]["phrases"], 0)
        self.assertEqual(len(data["outputs"]), 3)

    def test_build_works_in_frozen_layout(self):
        """模拟安装版布局：exe 旁边有短语库，模块目录里没有任何 .py。"""
        import shutil
        import tempfile

        real_library = knowledge._library_path()
        real_file = knowledge.__file__

        with tempfile.TemporaryDirectory() as tmp:
            exe_dir = pathlib.Path(tmp)
            shutil.copy2(real_library, exe_dir / "intents_examples.yaml")

            old_frozen = getattr(sys, "frozen", None)
            old_exec = sys.executable
            sys.frozen = True
            sys.executable = str(exe_dir / "小小领航员.exe")
            # 模块路径也指到不存在的目录，模拟 _internal 里没有源码的情况。
            knowledge.__file__ = str(exe_dir / "_internal" / "src" / "__frozen__.pyc")
            try:
                data = knowledge.build(force=True)
            finally:
                knowledge.__file__ = real_file
                sys.executable = old_exec
                if old_frozen is None:
                    del sys.frozen
                else:
                    sys.frozen = old_frozen

        self.assertGreater(data["totals"]["intents"], 0)
        self.assertGreater(data["totals"]["phrases"], 0)
        self.assertTrue(data["probes"])


class OutputModeParityTests(unittest.TestCase):
    """OUTPUT_MODES 是运行期唯一来源，必须与路由源码的分支保持一致。"""

    def _parse_sources(self):
        root = pathlib.Path(knowledge.__file__).resolve().parent
        modes = {}
        for filename in ("telemetry_answers.py", "router.py"):
            path = root / filename
            if not path.is_file():
                self.skipTest(f"找不到路由源码，跳过一致性检查：{path}")
            import re
            blocks = re.split(r"if intent=='(query_[a-z_]+)':", path.read_text(encoding="utf-8"))
            for i in range(1, len(blocks), 2):
                intent, body = blocks[i], blocks[i + 1]
                found = modes.setdefault(intent, set())
                if "numeric(" in body:
                    found.add("数值")
                if "timed(" in body or "duration(" in body:
                    found.add("时间")
                if "state(" in body or "Decision([" in body:
                    found.add("整句")
        return modes

    def test_declared_modes_cover_source_branches(self):
        parsed = self._parse_sources()
        self.assertTrue(parsed, "路由源码里没解析到任何 if intent== 分支")
        for intent, kinds in knowledge.OUTPUT_MODES.items():
            with self.subTest(intent=intent):
                missing = parsed.get(intent, set()) - set(kinds)
                self.assertEqual(missing, set(),
                                 f"{intent} 的实际分支比 OUTPUT_MODES 多了 {missing}，请同步这张表")

    def test_declared_modes_are_known_labels(self):
        for intent, kinds in knowledge.OUTPUT_MODES.items():
            with self.subTest(intent=intent):
                self.assertIn(intent, knowledge.INTENT_LABELS)
                self.assertTrue(kinds)
                self.assertTrue(set(kinds) <= {"整句", "数值", "时间"})


class LibraryResolutionTests(unittest.TestCase):
    """短语库要跟着匹配器走：打包后优先读 exe 旁边那份。"""

    def test_not_frozen_resolves_to_an_existing_file(self):
        self.assertTrue(knowledge._library_path().is_file())

    def test_frozen_prefers_exe_adjacent_library(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fake_exe_dir = pathlib.Path(tmp)
            target = fake_exe_dir / "intents_examples.yaml"
            target.write_text("query_fuel:\n  route: state_layer\n", encoding="utf-8")

            old_frozen = getattr(sys, "frozen", None)
            old_exec = sys.executable
            sys.frozen = True
            sys.executable = str(fake_exe_dir / "小小领航员.exe")
            try:
                self.assertEqual(knowledge._library_path().resolve(), target.resolve())
            finally:
                sys.executable = old_exec
                if old_frozen is None:
                    del sys.frozen
                else:
                    sys.frozen = old_frozen

            self.assertFalse(getattr(sys, "frozen", False))


if __name__ == "__main__":
    unittest.main()
