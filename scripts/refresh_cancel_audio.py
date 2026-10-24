"""补齐取消确认的六条语音；不把单项取消播成「全部清空」。

读取已有的 ai.tts 配置，凭据只在内存使用；默认仅生成补丁素材。
--apply 只更新本仓库的 build/assistant-telemetry，不修改安装目录。
"""
import argparse
import hashlib
import io
import json
import pathlib
import re
import sys
import wave

import yaml
from opencc import OpenCC

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'voice-assistant'))
from src.tts_engine import TtsEngine  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=pathlib.Path, required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    cfg = yaml.safe_load(args.config.read_text(encoding='utf-8-sig')) or {}
    tts = ((cfg.get('ai') or {}).get('tts') or {})
    if not tts.get('api_key') or tts.get('adapter', 'off') == 'off':
        raise SystemExit('缺少已配置的 TTS；未生成音频')
    engine = TtsEngine(tts['adapter'], tts)
    content = json.loads((ROOT / 'content/assistant-telemetry-v1.2.json').read_text(encoding='utf-8'))
    items = content if isinstance(content, list) else content['items']
    item = next(row for row in items if row['msgId'] == 'AST_CC_CLEAR')
    out = ROOT / 'build/cancel-audio-fix'
    out.mkdir(parents=True, exist_ok=True)
    converter = OpenCC('t2s')
    rows = []
    for i, text in enumerate(item['variants'], 1):
        try:
            blob = engine.synth(text)
        except Exception as exc:
            raise SystemExit(str(exc).replace(tts['api_key'], '[hidden]')) from None
        with wave.open(io.BytesIO(blob), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise SystemExit('取消素材必须是 16 kHz / 16-bit / 单声道 WAV')
            duration = wav.getnframes() / wav.getframerate()
        name = f'AST_CC_CLEAR_{i:02d}.WAV'
        (out / name).write_bytes(blob)
        rows.append({'file': name, 'text': converter.convert(text),
                     'seconds': round(duration, 3), 'sha256': hashlib.sha256(blob).hexdigest()})
    (out / 'manifest.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.apply:
        pack = ROOT / 'build/assistant-telemetry'
        msg = pack / 'spmsg.txt'
        original = msg.read_text(encoding='utf-8-sig')
        updated = original
        for row in rows:
            line = f'AST_CC_CLEAR, {row["file"]}, "{row["text"]}"'
            updated, count = re.subn(r'^AST_CC_CLEAR,\s*' + re.escape(row['file']) + r',.*$',
                                     lambda _: line, updated, flags=re.MULTILINE)
            if count != 1:
                raise SystemExit(f'{row["file"]} 在语音包中不是唯一条目，未替换')
        for row in rows:
            (pack / row['file']).write_bytes((out / row['file']).read_bytes())
        msg.write_text(updated, encoding='utf-8')
    print(f'取消确认素材：{len(rows)} 条已生成' + ('并应用到本地构建素材' if args.apply else ''))


if __name__ == '__main__':
    main()
