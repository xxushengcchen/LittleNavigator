#!/usr/bin/env python3
"""仓库密钥扫描器（仅标准库，无第三方依赖）。

用法：
    python scripts/secret_scan.py --all       # 扫描所有 refs 可达的 git blob（CI 用）
    python scripts/secret_scan.py --staged    # 只扫描暂存区（pre-commit 用）

退出码：0 未发现疑似密钥；1 发现疑似密钥；2 运行环境错误。
输出只给脱敏片段，不会打印完整密钥。

覆盖范围说明：
- 标准格式：sk-、sk-ant-、GitHub token、AWS AKIA、Google AIza、Slack、Stripe、GitLab、
  Hugging Face、PEM 私钥、JWT、URL 内嵌密码。
- 本项目相关：DeepSeek/OpenAI 兼容接口的 sk- 长 key（20 字符以上）；
  火山引擎/豆包语音 API Key（UUID 形，且附近出现 api_key / 密钥 之类标签）。
- 另有一层「赋值式」兜底：api_key / 密钥 / password 等字段后跟 12 位以上高熵值。

这些规则是对 GitHub Secret Scanning 的补充：后者只认它支持的 provider 格式，
DeepSeek 与火山引擎的 key 不一定在清单内。
"""

from __future__ import annotations

import argparse
import math
import os
import re
import subprocess
import sys
import tempfile

# pre-commit 在 Windows 上按 UTF-8 解析 hook 输出；显式固定编码，避免中文提示乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STRONG_RULES = [
    ("deepseek/openai key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("github fine-grained pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("stripe live key", re.compile(r"\bsk_live_[A-Za-z0-9]{16,}\b")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("huggingface token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
    ),
    ("url with password", re.compile(r"https?://[^/\s:@]{1,64}:[^/\s@]{1,64}@")),
]

KEY_NAMES = (
    r"api[_-]?key|apikey|api\s*key|access[_-]?key|secret[_-]?key|"
    r"client[_-]?secret|auth[_-]?token|access[_-]?token|"
    r"password|passwd|密钥|密码|口令|令牌|凭据"
)
GENERIC = re.compile(
    rf"(?i)({KEY_NAMES})\s*[\"']?\s*[:=：]\s*[\"']?([A-Za-z0-9+/_.~!@#$%^&*-]{{12,}})"
)
UUID_WITH_LABEL = re.compile(
    rf"(?i)(?:{KEY_NAMES}|x-api-key)\s*[\"']?\s*[:=：]\s*[\"']?"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
PLACEHOLDER = re.compile(
    r"(?i)^(?:your|xxx+|todo|test|dummy|sample|example|placeholder|changeme|"
    r"none|null|true|false|redacted|fromfile|fromenv|draft-|<|\$\{|\.\.\.)"
)
ALLOW_VALUE = re.compile(r"^(?:sk-SECRET-123|sk-\.\.\.)$")


def entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def redact(value: str) -> str:
    value = value.strip().strip("\"'")
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:4]}***{value[-2:]}(len={len(value)})"


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def scan_text(text: str, path: str) -> list[tuple[str, str, str, int]]:
    findings: list[tuple[str, str, str, int]] = []
    for rule_name, pattern in STRONG_RULES:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append((path, rule_name, match.group(0), line))

    for match in UUID_WITH_LABEL.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        findings.append((path, "uuid-shaped key with label", match.group(1), line))

    for match in GENERIC.finditer(text):
        value = match.group(2)
        if PLACEHOLDER.match(value) or ALLOW_VALUE.match(value):
            continue
        if "." in value:  # 代码属性访问（args.api_key / os.environ.get），不是字面量密钥
            continue
        if len(set(value)) < 5 or entropy(value) < 3.0:
            continue
        line = text.count("\n", 0, match.start()) + 1
        findings.append((path, f"assigned value of {match.group(1)}", value, line))
    return findings


def report(findings: list[tuple[str, str, str, int]], scanned: int) -> int:
    unique = {}
    for path, rule_name, value, line in findings:
        unique[(path, rule_name, redact(value), line)] = value
    print(f"scanned: {scanned} file(s)/blob(s)")
    print(f"findings: {len(unique)}")
    for (path, rule_name, shown, line), _raw in sorted(unique.items()):
        print(f"  {path}:{line}  [{rule_name}]  {shown}")
    return 1 if unique else 0


def git(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        input=input_bytes,
        capture_output=True,
        check=False,
    )


def scan_staged() -> int:
    listed = git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if listed.returncode != 0:
        print(listed.stderr.decode("utf-8", "replace"), file=sys.stderr)
        return 2
    paths = [p for p in listed.stdout.decode("utf-8", "replace").splitlines() if p]
    findings: list[tuple[str, str, str, int]] = []
    scanned = 0
    for path in paths:
        blob = git("show", f":{path}")
        if blob.returncode != 0 or is_binary(blob.stdout):
            continue
        scanned += 1
        findings.extend(
            scan_text(blob.stdout.decode("utf-8", "replace"), path)
        )
    return report(findings, scanned)


def scan_all_refs() -> int:
    rev = git("rev-list", "--objects", "--all")
    if rev.returncode != 0:
        print(rev.stderr.decode("utf-8", "replace"), file=sys.stderr)
        return 2

    entries: list[tuple[str, str]] = []
    for line in rev.stdout.decode("utf-8", "replace").splitlines():
        oid, _, path = line.partition(" ")
        if path:
            entries.append((oid, path))

    oids = list(dict.fromkeys(oid for oid, _ in entries))
    checked = git("cat-file", "--batch-check", input_bytes="\n".join(oids).encode())
    if checked.returncode != 0:
        print(checked.stderr.decode("utf-8", "replace"), file=sys.stderr)
        return 2

    blob_oids: set[str] = set()
    for line in checked.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "blob":
            blob_oids.add(parts[0])

    paths_by_oid: dict[str, set[str]] = {}
    for oid, path in entries:
        if oid in blob_oids:
            paths_by_oid.setdefault(oid, set()).add(path)

    targets = sorted(paths_by_oid)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".oid", delete=False
    )
    try:
        handle.write("\n".join(targets) + "\n")
        handle.close()
        findings: list[tuple[str, str, str, int]] = []
        scanned = 0
        with open(handle.name, "rb") as stdin, subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=REPO_ROOT,
            stdin=stdin,
            stdout=subprocess.PIPE,
        ) as proc:
            assert proc.stdout is not None
            for _oid in targets:
                header = proc.stdout.readline().decode("utf-8", "replace").strip()
                if not header or header.endswith("missing"):
                    continue
                parts = header.split()
                try:
                    size = int(parts[2])
                except (IndexError, ValueError):
                    continue
                data = proc.stdout.read(size)
                proc.stdout.read(1)
                if is_binary(data):
                    continue
                scanned += 1
                text = data.decode("utf-8", "replace")
                for path in sorted(paths_by_oid[_oid]):
                    findings.extend(scan_text(text, path))
        return report(findings, scanned)
    finally:
        os.unlink(handle.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描仓库中的明文密钥")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="扫描全部 git 历史（所有 refs）")
    group.add_argument("--staged", action="store_true", help="只扫描暂存区")
    args = parser.parse_args()

    if git("rev-parse", "--show-toplevel").returncode != 0:
        print("当前目录不是 git 仓库", file=sys.stderr)
        return 2
    if args.staged:
        return scan_staged()
    return scan_all_refs()


if __name__ == "__main__":
    sys.exit(main())
