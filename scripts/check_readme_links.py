"""Check local README/document links and the supported GitHub markup subset.

v1.6.2 起：除固定清单外，还递归跟随文档里的本地 .md 链接（README 链接到的
文档，其出链也会被检查），并覆盖当前发布说明与 DEVELOPMENT/CONTRIBUTING/
SECURITY；141 条 PASS 不再等于“全仓库链接都查过”。
"""
import argparse
from collections import deque
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SEED_FILES = [
    'README.md', 'voice-assistant/README.md', 'THIRD_PARTY_NOTICES.md',
    'LICENSE-CHANGE.md', 'COMMERCIAL-LICENSE.md', 'ASSETS-LICENSE.md',
    'TRADEMARK.md', 'INSTALLER-EULA.md',
    'DEVELOPMENT.md', 'CONTRIBUTING.md', 'SECURITY.md',
    'assets/readme/README.md', 'docs/guide/spotter-install.md',
    'docs/guide/capabilities-and-limits.md',
    'docs/guide/api-setup.md', 'docs/guide/v1.4.5-interface.md',
    'docs/guide/user-guide.md', 'docs/knowledge-base.md',
    'docs/releases/v1.5.0.md',
    'docs/releases/v1.5.1.md',
    'docs/releases/v1.5.2.md',
    'docs/releases/v1.5.3.md',
    'docs/releases/v1.5.4.md',
    'docs/releases/v1.5.5.md',
    'docs/releases/v1.6.0.md',
    'docs/releases/v1.6.1.md',
    'docs/releases/v1.6.2.md',
]
MAX_DOCS = 400


def slugs(text):
    values = set()
    for line in text.splitlines():
        match = re.match(r'^#{1,6}\s+(.+)', line)
        if match:
            heading = match[1].lower().replace('`', '')
            values.add(re.sub(r'[^\w -]', '', heading).replace(' ', '-'))
    return values


def main():
    ap = argparse.ArgumentParser(description='Check local markdown links recursively.')
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--seed', action='append', default=[],
                    help='额外的起始文档（可重复；给 --root 做 fixture 测试时用）')
    args = ap.parse_args()
    root = args.root.resolve()
    seeds = args.seed or SEED_FILES

    queue = deque()
    for name in seeds:
        path = (root / name).resolve()
        if not path.is_file():
            print(f'{name}: missing local target (seed file not found)')
            return 1
        queue.append(path)

    seen, errors, checked = set(), [], 0
    while queue and len(seen) < MAX_DOCS:
        source = queue.popleft()
        if source in seen:
            continue
        seen.add(source)
        name = source.relative_to(root).as_posix()
        text = source.read_text(encoding='utf-8')
        links = re.findall(r'\[[^\]]*\]\(([^)]+)\)', text)
        links += re.findall(r'(?:src|srcset|href)="([^"]+)"', text)
        for raw in links:
            url = urlsplit(raw)
            if url.scheme or url.netloc:
                continue
            target = (source.parent / unquote(url.path)).resolve() if url.path else source
            if not target.is_relative_to(root) or not target.exists():
                errors.append(f'{name}: missing local target {raw}')
                continue
            if url.fragment and target.suffix == '.md':
                if unquote(url.fragment) not in slugs(target.read_text(encoding='utf-8')):
                    errors.append(f'{name}: missing heading {raw}')
            checked += 1
            if target.suffix == '.md' and target not in seen and target.is_file():
                queue.append(target)

    homepage = (root / 'README.md').read_text(encoding='utf-8')
    if re.search(r'<(?:script|style|iframe)\b|\bstyle\s*=', homepage, re.I):
        errors.append('README.md: script, iframe or custom CSS is not allowed')
    for tag in re.findall(r'<img\b[^>]*>', homepage):
        if not re.search(r'\balt="[^"]+"', tag):
            errors.append('README.md: image missing alt text')
    if errors:
        print('\n'.join(errors))
        return 1
    print(f'PASS: {checked} local links/anchors in {len(seen)} documents '
          f'(recursive): ' + ', '.join(sorted(p.relative_to(root).as_posix() for p in seen)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
