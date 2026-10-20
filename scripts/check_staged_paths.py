#!/usr/bin/env python3
"""阻止把「只属于某台机器/某个人」的路径提交进公开仓库。

这些路径在维护者的工作区里通过 `.git/info/exclude` 排除，但该规则不会随仓库
分发；换机器或复制工作区后，`git add -A` 可能把它们带进提交。本脚本配合
pre-commit 使用（见 `.pre-commit-config.yaml`），在提交前兜底拦截。

用法：python scripts/check_staged_paths.py
退出码：0 通过；1 发现被阻止的路径；2 运行环境错误。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

# pre-commit 在 Windows 上按 UTF-8 解析 hook 输出；显式固定编码，避免中文提示乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLOCKED = re.compile(
    r"^(?:dev/|reference/|release-records/|\.taste-skill/|AGENTS\.md$)"
)


def main() -> int:
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if staged.returncode != 0:
        print(staged.stderr.decode("utf-8", "replace"), file=sys.stderr)
        return 2

    paths = [
        line
        for line in staged.stdout.decode("utf-8", "replace").splitlines()
        if line
    ]
    blocked = [path for path in paths if BLOCKED.match(path)]
    if blocked:
        print("拒绝提交：以下路径是本地工作区专用，不应进入公开仓库：", file=sys.stderr)
        for path in blocked:
            print(f"  {path}", file=sys.stderr)
        print(
            "如果确实需要提交，请先确认其中不含个人路径/凭据，再用 git add -f 覆盖，"
            "并同步更新 .gitignore。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
