"""Check local README/document links and the supported GitHub markup subset."""
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    'README.md', 'voice-assistant/README.md', 'THIRD_PARTY_NOTICES.md',
    'assets/readme/README.md', 'docs/guide/spotter-install.md',
    'docs/guide/capabilities-and-limits.md',
    'docs/guide/api-setup.md', 'docs/guide/v1.4.5-interface.md',
    'docs/guide/user-guide.md', 'docs/knowledge-base.md',
    'docs/releases/v1.5.0.md',
    'docs/releases/v1.5.1.md',
]

def slugs(text):
    values = set()
    for line in text.splitlines():
        match = re.match(r'^#{1,6}\s+(.+)', line)
        if match:
            heading = match[1].lower().replace('`', '')
            values.add(re.sub(r'[^\w -]', '', heading).replace(' ', '-'))
    return values

def main():
    errors, checked = [], 0
    for name in FILES:
        source = ROOT / name
        text = source.read_text(encoding='utf-8')
        links = re.findall(r'\[[^\]]*\]\(([^)]+)\)', text)
        links += re.findall(r'(?:src|srcset|href)="([^"]+)"', text)
        for raw in links:
            url = urlsplit(raw)
            if url.scheme or url.netloc:
                continue
            target = (source.parent / unquote(url.path)).resolve() if url.path else source
            if not target.is_relative_to(ROOT) or not target.exists():
                errors.append(f'{name}: missing local target {raw}')
                continue
            if url.fragment and target.suffix == '.md':
                if unquote(url.fragment) not in slugs(target.read_text(encoding='utf-8')):
                    errors.append(f'{name}: missing heading {raw}')
            checked += 1
    homepage = (ROOT / 'README.md').read_text(encoding='utf-8')
    if re.search(r'<(?:script|style|iframe)\b|\bstyle\s*=', homepage, re.I):
        errors.append('README.md: script, iframe or custom CSS is not allowed')
    for tag in re.findall(r'<img\b[^>]*>', homepage):
        if not re.search(r'\balt="[^"]+"', tag):
            errors.append('README.md: image missing alt text')
    if errors:
        print('\n'.join(errors))
        return 1
    print(f'PASS: {checked} local links/anchors in {len(FILES)} documents; no custom CSS or scripts.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
