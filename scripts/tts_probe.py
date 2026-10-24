"""凭据只从 stdin 接收，不作为命令行参数或文件保存。"""
import sys,pathlib,getpass
from tts_doubao import synthesize,DEFAULT_SPEAKER,DEFAULT_RESOURCE,TtsError
print('等待进程输入凭据',flush=True)
key=getpass.getpass('语音凭据（不回显）：').strip()
if not key:raise SystemExit('未收到凭据，未调用 API')
try:
    audio=synthesize('現在的氣溫是',api_key=key,speaker=DEFAULT_SPEAKER,resource_id=DEFAULT_RESOURCE,
                     instruct='用台灣腔年輕女生的語氣，清楚輕快地唸出這個句子前綴，不要拖長音',speech_rate=30)
    path=pathlib.Path('build/tts-probe.mp3');path.write_bytes(audio)
    print('豆包语音测试成功，音频字节数',len(audio),flush=True)
except TtsError as exc:
    print('豆包语音测试失败：'+str(exc).replace(key,'[已隐藏]'),flush=True)
    raise SystemExit(2)
finally:key=''
