"""同音色批量合成，凭据仅在环境或不回显输入中使用；完整后再打包。"""
import argparse,concurrent.futures,getpass,json,os,pathlib,subprocess,sys,threading,time
from tts_doubao import synthesize,DEFAULT_SPEAKER,DEFAULT_RESOURCE,TtsError

ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--probe',action='store_true');ap.add_argument('--workers',type=int,default=2)
    args=ap.parse_args();os.chdir(ROOT)
    doc=json.loads((ROOT/'content/assistant-telemetry-v1.1.json').read_text(encoding='utf-8'))
    key=os.getenv('DOUBAO_API_KEY') or getpass.getpass('豆包语音 API Key（不回显）：').strip()
    if not key:raise SystemExit('未收到凭据，未调用 API')
    raw=ROOT/'build/telemetry-tts';raw.mkdir(exist_ok=True)
    jobs=[(item,i,text) for item in doc['items'] for i,text in enumerate(item['variants'],1)]
    def synth(job,force=False):
        item,i,text=job;stem=f"{item['msgId']}__{i:02d}";path=raw/(stem+'.mp3')
        if not force and path.exists() and path.stat().st_size>100:return stem,'cached'
        for attempt in range(3):
            try:
                data=synthesize(text,api_key=key,speaker=DEFAULT_SPEAKER,resource_id=DEFAULT_RESOURCE,
                    instruct=item.get('instruct') or doc['meta']['instructs']['normal'],speech_rate=item.get('speechRate'))
                tmp=path.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(path)
                return stem,'ok'
            except TtsError as exc:
                error=str(exc).replace(key,'[已隐藏]')
                if '401' in error or '403' in error or '45000010' in error:raise RuntimeError(error)
                if attempt==2:raise RuntimeError(error)
                time.sleep(1+attempt*2)
    first=synth(jobs[0],force=args.probe);print('首条原音色素材就绪：',first,flush=True)
    if args.probe:return 0
    print(f'准备合成 {len(jobs)} 条素材，使用原小何 2.0 音色',flush=True)
    completed=0;failed=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:
        futures={pool.submit(synth,job):job for job in jobs[1:]}
        for future in concurrent.futures.as_completed(futures):
            try:future.result();completed+=1
            except Exception as exc:failed.append(str(exc));print('单条失败：',str(exc).replace(key,'[已隐藏]'),flush=True)
            if completed%20==0:print(f'已完成 {completed+1}/{len(jobs)}，失败 {len(failed)}',flush=True)
    key=''
    if failed:raise SystemExit(f'有 {len(failed)} 条未完成；保留已完成音频，请重新运行补齐')
    subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/build_pack.py'),
        '--content','content/assistant-telemetry-v1.1.json','--audio','build/telemetry-tts',
        '--out','build','--name','assistant-telemetry','--rate','24000','--display','simplified',
        '--no-null','--version','1.1.0'],check=True)
    expected=len(jobs);actual=len(list((ROOT/'build/assistant-telemetry').glob('*.WAV')))
    if actual!=expected:raise SystemExit(f'打包未完整：{actual}/{expected}')
    print(f'完成：{actual} 条 WAV，24 kHz/16-bit/单声道，原裁剪与 -17 dBFS RMS 流程',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
