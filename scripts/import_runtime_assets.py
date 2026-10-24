"""从已安装的发行版导入开发所需模型和语音，不复制个人配置或日志。"""
import argparse,pathlib,shutil
ROOT=pathlib.Path(__file__).resolve().parents[1]
PARTS=('models','build/pack/Mandarin-CNTW-Xusheng_Chen','build/assistant-extra','build/assistant-telemetry')
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from',dest='source',type=pathlib.Path,required=True)
    args=parser.parse_args()
    source=args.source.resolve()
    for rel in PARTS:
        if not (source/rel).is_dir():raise FileNotFoundError(source/rel)
    if not (source/'models/sense-voice/model.int8.onnx').is_file():raise FileNotFoundError('缺少 SenseVoice 模型')
    for rel in PARTS:
        for file in (source/rel).rglob('*'):
            if not file.is_file():continue
            dest=ROOT/rel/file.relative_to(source/rel)
            dest.parent.mkdir(parents=True,exist_ok=True)
            if file.resolve()!=dest.resolve():shutil.copy2(file,dest)
    print('模型和语音资源已导入；未复制用户配置、日志或状态。')
if __name__=='__main__':main()
