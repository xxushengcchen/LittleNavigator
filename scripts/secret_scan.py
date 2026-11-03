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
# 固定测试夹具：只按“完整字面值”豁免，绝不做 sk-test* 之类前缀放行，
# 否则真实泄漏只要长得像测试值就会被一起放过。新增夹具必须逐值加入。
ALLOW_EXACT = frozenset({
    "sk-test-SECRET-1234567890",   # test_ai_models.py 的假 Key
    "sk-env-only-placeholder",     # test_audit_config_security_v161.py 的环境变量夹具
    "synthetic-source-key",        # test_audit_config_transaction_chain.py 的合成配置
    "audit-fake-key",              # test_audit_dynamic_learning_chain.py
    "http://user:pw@",             # test_http_transport.py 的非法 URL 夹具
})


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
            if match.group(0) in ALLOW_EXACT:
                continue  # 精确豁免固定测试夹具
            line = text.count("\n", 0, match.start()) + 1
            findings.append((path, rule_name, match.group(0), line))

    for match in UUID_WITH_LABEL.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        findings.append((path, "uuid-shaped key with label", match.group(1), line))

    for match in GENERIC.finditer(text):
        value = match.group(2)
        if value in ALLOW_EXACT or PLACEHOLDER.match(value) or ALLOW_VALUE.match(value):
            continue
        # 代码属性访问（args.api_key / os.environ.get）不是字面量密钥，可以跳过；
        # 但 `abc.def.123456` 这种带数字的点分值更像密钥，必须继续扫。
        if ("." in value
                and not any(ch.isdigit() for ch in value)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", value)):
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
    # -z：git 默认会把中文等路径转成 C 风格转义（quotePath），一旦按行解析就
    # 再也还原不出真实路径，git show 失败还被静默跳过，等于漏扫。用 NUL 分隔
    # 并按文件系统编码还原原始字节。
    listed = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    if listed.returncode != 0:
        print(listed.stderr.decode("utf-8", "replace"), file=sys.stderr)
        return 2
    paths = [os.fsdecode(p) for p in listed.stdout.split(b"\x00") if p]
    findings: list[tuple[str, str, str, int]] = []
    scanned = 0
    errors = 0
    for path in paths:
        blob = git("show", f":{path}")
        if blob.returncode != 0:
            # 暂存文件读不出来时不能当作“没问题”：宁可运行失败（2），
            # 也不让 pre-commit 在漏扫的情况下放行。
            print(f"无法读取暂存内容：{path}: "
                  f"{blob.stderr.decode('utf-8', 'replace').strip()}",
                  file=sys.stderr)
            errors += 1
            continue
        if is_binary(blob.stdout):
            continue
        scanned += 1
        findings.extend(
            scan_text(blob.stdout.decode("utf-8", "replace"), path)
        )
    code = report(findings, scanned)
    return 2 if errors else code


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
            # 审计复审：cat-file 提前结束/报错时旧代码照样 report() 并返回 0，
            # 等于“没扫完也绿”。这里 fail-closed。
            if proc.wait() != 0:
                print("FAIL: git cat-file --batch 读取失败，扫描不完整", file=sys.stderr)
                return 2
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
