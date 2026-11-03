"""同音色批量合成，凭据仅在环境或不回显输入中使用；完整后再打包。

输出包名/输出根目录可用 --name / --out 指定（默认 assistant-telemetry / build，
与旧行为一致）。多个包共用同一个 mp3 缓存目录 --audio，按 msgId 前缀天然隔离。
"""
import argparse,concurrent.futures,getpass,hashlib,json,os,pathlib,subprocess,sys,threading,time
from tts_doubao import synthesize,DEFAULT_SPEAKER,DEFAULT_RESOURCE,TtsError
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
from content_loader import load_items

ROOT=pathlib.Path(__file__).resolve().parents[1]


def cache_fingerprint(text, *, speaker, resource_id, instruct=None, speech_rate=None):
    """缓存键必须覆盖文本、音色与全部合成参数：改文案后不能复用旧录音。"""
    payload={'text':text,'speaker':speaker,'resource_id':resource_id,
             'instruct':instruct,'speech_rate':speech_rate}
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _cache_sidecar(path):
    return path.with_name(path.name+'.fp.json')


def remember_cache(path, fingerprint):
    _cache_sidecar(path).write_text(
        json.dumps({'fingerprint':fingerprint},ensure_ascii=False),encoding='utf-8')


def cache_is_current(path, fingerprint, content_mtime=None):
    """缓存是否仍对应当前文本/音色/参数。

    新缓存靠 .fp.json 指纹判断；旧版本留下的缓存没有 sidecar：
    - 若文案文件比缓存新（改过文案）→ 视为过期，必须重合成（修复 Q07）；
    - 若文案没动过（缓存不早于文案）→ 一次性收养为当前版本并补写指纹，
      避免升级后无谓地把整包重新计费合成。
    """
    if not path.exists() or path.stat().st_size<=100:
        return False
    side=_cache_sidecar(path)
    if not side.is_file():
        if content_mtime is None:
            return False
        try:
            return path.stat().st_mtime >= content_mtime
        except OSError:
            return False
    try:
        return json.loads(side.read_text(encoding='utf-8')).get('fingerprint')==fingerprint
    except (OSError,ValueError):
        return False


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--probe',action='store_true');ap.add_argument('--workers',type=int,default=2)
    ap.add_argument('--force',action='store_true',
                    help='忽略缓存，按当前文案/音色/参数全部重合成')
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
    content_mtime=0.0
    for pat in args.content:
        hits=sorted(ROOT.glob(pat)) if any(c in pat for c in "*?[") else [ROOT/pat]
        for h in hits:
            try:content_mtime=max(content_mtime,h.stat().st_mtime)
            except OSError:pass
    raw=ROOT/args.audio;raw.mkdir(parents=True,exist_ok=True)
    jobs=[(item,i,text) for item in items for i,text in enumerate(item['variants'],1)]
    def synth(job,force=False):
        item,i,text=job;stem=f"{item['msgId']}__{i:02d}";path=raw/(stem+'.mp3')
        instruct=item.get('instruct') or instructs.get('normal');rate=item.get('speechRate')
        fingerprint=cache_fingerprint(text,speaker=DEFAULT_SPEAKER,
            resource_id=DEFAULT_RESOURCE,instruct=instruct,speech_rate=rate)
        if not force and cache_is_current(path,fingerprint,content_mtime):
            if not _cache_sidecar(path).is_file():remember_cache(path,fingerprint)
            return stem,'cached'
        for attempt in range(3):
            try:
                data=synthesize(text,api_key=key,speaker=DEFAULT_SPEAKER,resource_id=DEFAULT_RESOURCE,
                    instruct=instruct,speech_rate=rate)
                tmp=path.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(path)
                remember_cache(path,fingerprint)
                return stem,'ok'
            except TtsError as exc:
                error=str(exc).replace(key,'[已隐藏]')
                if '401' in error or '403' in error or '45000010' in error:raise RuntimeError(error)
                if attempt==2:raise RuntimeError(error)
                time.sleep(1+attempt*2)
    first=synth(jobs[0],force=args.force or args.probe);print('首条原音色素材就绪：',first,flush=True)
    if args.probe:return 0
    print(f'准备合成 {len(jobs)} 条素材，使用原小何 2.0 音色',flush=True)
    completed=0;failed=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:
        futures={pool.submit(synth,job,args.force):job for job in jobs[1:]}
        for future in concurrent.futures.as_completed(futures):
            try:future.result();completed+=1
            except Exception as exc:failed.append(str(exc));print('单条失败：',str(exc).replace(key,'[已隐藏]'),flush=True)
            if completed%20==0:print(f'已完成 {completed+1}/{len(jobs)}，失败 {len(failed)}',flush=True)
    key=''
    if failed:raise SystemExit(f'有 {len(failed)} 条未完成；保留已完成音频，请重新运行补齐')
    pack_cmd=[sys.executable,'-X','utf8',str(ROOT/'scripts/build_pack.py'),
        '--content',*args.content,'--audio',args.audio,
        '--out',args.out,'--name',args.name,'--rate','24000','--display','simplified',
        '--no-null','--version',args.version]
    if args.force:pack_cmd.append('--force')
    subprocess.run(pack_cmd,check=True)
    expected=len(jobs);actual=len(list((ROOT/args.out/args.name).glob('*.WAV')))
    if actual!=expected:raise SystemExit(f'打包未完整（{args.out}/{args.name}）：{actual}/{expected}')
    print(f'完成：{actual} 条 WAV（{args.out}/{args.name}），24 kHz/16-bit/单声道，原裁剪与 -17 dBFS RMS 流程',flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
