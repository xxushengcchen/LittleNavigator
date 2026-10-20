"""同音色批量合成，凭据仅在环境或不回显输入中使用；完整后再打包。

输出包名/输出根目录可用 --name / --out 指定（默认 assistant-telemetry / build，
与旧行为一致）。多个包共用同一个 mp3 缓存目录 --audio，按 msgId 前缀天然隔离。
"""
import argparse,concurrent.futures,getpass,json,os,pathlib,subprocess,sys,threading,time
from tts_doubao import synthesize,DEFAULT_SPEAKER,DEFAULT_RESOURCE,TtsError
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from content_loader import load_items

ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--probe',action='store_true');ap.add_argument('--workers',type=int,default=2)
    ap.add_argument('--content',nargs='+',default=['content/assistant-telemetry-v1.1.json'],
                    help='文案檔（可多個；預設只讀 v1.1）')
    ap.add_argument('--version',default='1.1.0',help='寫進語音包頭部註解的版本號')
    ap.add_argument('--name',default='assistant-telemetry',
                    help='語音包資料夾名稱（輸出到 --out 之下；預設 assistant-telemetry）')
    ap.add_argument('--out',default='build',help='語音包輸出根目錄（預設 build）')
    ap.add_argument('--audio',default='build/telemetry-tts',
                    help='合成 mp3 快取目錄（預設 build/telemetry-tts，可跨包共用）')
    args=ap.parse_args();os.chdir(ROOT)
    items,instructs=load_items(ROOT,patterns=args.content)
    key=os.getenv('DOUBAO_API_KEY') or getpass.getpass('豆包语音 API Key（不回显）：').strip()
    if not key:raise SystemExit('未收到凭据，未调用 API')
    raw=ROOT/args.audio;raw.mkdir(parents=True,exist_ok=True)
    jobs=[(item,i,text) for item in items for i,text in enumerate(item['variants'],1)]
    def synth(job,force=False):
        item,i,text=job;stem=f"{item['msgId']}__{i:02d}";path=raw/(stem+'.mp3')
        if not force and path.exists() and path.stat().st_size>100:return stem,'cached'
        for attempt in range(3):
            try:
                data=synthesize(text,api_key=key,speaker=DEFAULT_SPEAKER,resource_id=DEFAULT_RESOURCE,
                    instruct=item.get('instruct') or instructs.get('normal'),speech_rate=item.get('speechRate'))
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
        '--content',*args.content,'--audio',args.audio,
        '--out',args.out,'--name',args.name,'--rate','24000','--display','simplified',
        '--no-null','--version',args.version],check=True)
    expected=len(jobs);actual=len(list((ROOT/args.out/args.name).glob('*.WAV')))
    if actual!=expected:raise SystemExit(f'打包未完整（{args.out}/{args.name}）：{actual}/{expected}')
    print(f'完成：{actual} 条 WAV（{args.out}/{args.name}），24 kHz/16-bit/单声道，原裁剪与 -17 dBFS RMS 流程',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
