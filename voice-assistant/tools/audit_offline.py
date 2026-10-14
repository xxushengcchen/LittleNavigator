"""检查全部 WAV、拼装素材、路由覆盖与历史 ASR 转写；不录麦、不联网。"""
import argparse
import collections
import json
import pathlib
import sys
import wave
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src import config, numbers, router, telemetry
from src.phrase_match import PhraseMatcher
from src.voicepack import VoicePack
from tools.asr_models import load as load_asr
from tools.audio_io import read_wav


def main():
    config.fix_console_encoding()
    ap = argparse.ArgumentParser()
    ap.add_argument('--bench', type=pathlib.Path)
    ap.add_argument('--samples', type=pathlib.Path)
    ap.add_argument('--out',type=pathlib.Path,default=ROOT.parent/'build'/'offline-audit.json')
    args = ap.parse_args()
    cfg = config.load()
    pack = VoicePack.load_many([config.resolve(cfg,cfg['voice_pack'][key]) for key in ('dir','extra_dir','telemetry_dir') if cfg['voice_pack'].get(key)])
    result = {'pack':pack.stats(),'audio':{'count':0,'seconds':0,'formats':{},'errors':[]}}
    peaks = []
    for mid,variants in pack.variants.items():
        for v in variants:
            path = pack.path_of(v)
            try:
                with wave.open(str(path),'rb') as w:
                    fmt = f'{w.getframerate()}Hz/{w.getsampwidth()*8}bit/{w.getnchannels()}ch'
                    if w.getnchannels()!=1 or w.getsampwidth() not in (1,2) or w.getnframes()==0:
                        raise ValueError('无效音频格式')
                    raw = w.readframes(w.getnframes())
                    if len(raw)!=w.getnframes()*w.getsampwidth():
                        raise ValueError('WAV 内容截断')
                    x = np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768 if w.getsampwidth()==2 else (np.frombuffer(raw,dtype=np.uint8).astype(np.float32)-128)/128
                    if not np.any(x): raise ValueError('全静音')
                    peaks.append(float(np.max(np.abs(x))))
                    result['audio']['seconds'] += w.getnframes()/w.getframerate()
                    result['audio']['formats'][fmt] = result['audio']['formats'].get(fmt,0)+1
                    result['audio']['count'] += 1
            except Exception as exc:
                result['audio']['errors'].append({'id':mid,'file':str(path),'error':str(exc)})
    result['audio']['seconds'] = round(result['audio']['seconds'],1)
    result['audio']['peak_max'] = max(peaks)
    result['numbers_selftest_errors'] = numbers.selftest(pack,verbose=False)
    m = PhraseMatcher()
    coverage = collections.Counter()
    missing = []
    sim = telemetry.SimTelemetry().read()
    for phrase in m.phrases:
        decision = router.route(phrase.intent,sim,phrase.text)
        coverage['recognized'] += m.match(phrase.text).intent==phrase.intent
        coverage['answered' if decision.ok else 'refused'] += 1
        for mid in decision.plan:
            if not pack.has(mid):missing.append(mid)
    result['phrase_coverage_sim'] = {**dict(coverage),'missing_plan_ids':sorted(set(missing))}
    if args.bench:
        count = collections.Counter(); wrong = []
        for line in args.bench.read_text(encoding='utf-8').splitlines():
            r=json.loads(line); hit=m.match(r['text']).intent
            if r['neg']:
                count['negative_total']+=1;count['negative_rejected']+=hit is None
            else:
                count['positive_total']+=1;count['positive_correct']+=hit==r['intent']
                if hit != r['intent']:wrong.append({'text':r['text'],'expected':r['intent'],'got':hit})
        result['stored_asr_regression'] = {**dict(count),'failures':wrong}
    if args.samples:
        asr=load_asr(cfg['asr']['model'],cfg['asr']['num_threads'],config.resolve(cfg,cfg['asr']['models_dir']))
        asr.warmup();rows=[]
        grouped = collections.defaultdict(list)
        for path in sorted(args.samples.glob('*.wav')):
            parts = path.stem.split('__')
            if len(parts)>1:
                grouped[parts[1]].append(path)
        selected = [path for items in grouped.values() for path in items[:2]]
        for path in selected:
            x,sr=read_wav(path);res=asr.transcribe(x,sr)
            rows.append({'file':path.name,'text':res.text,'intent':m.match(res.text).intent,'decode_ms':round(res.decode_sec*1000,1)})
        result['live_asr_file_check']={'load_sec':round(asr.load_sec,2),'samples':rows}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(bool(result['audio']['errors'] or result['numbers_selftest_errors'] or missing))

if __name__=='__main__':raise SystemExit(main())
