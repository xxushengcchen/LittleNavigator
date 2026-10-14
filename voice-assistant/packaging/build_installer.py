"""生成适合 GitHub Releases 的完整安装包与 SHA-256 校验文件。"""
import argparse,hashlib,pathlib,subprocess,sys,os,shutil
VA=pathlib.Path(__file__).resolve().parents[1]
ROOT=VA.parent

def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--compiler',type=pathlib.Path,default=ROOT/'build/tooling/inno/ISCC.exe')
    ap.add_argument('--webview-installer',type=pathlib.Path,default=ROOT/'build/tooling/MicrosoftEdgeWebView2RuntimeInstallerX64.exe')
    ap.add_argument('--skip-app-build',action='store_true')
    args=ap.parse_args()
    version=(VA/'VERSION').read_text(encoding='utf-8').strip()
    dist=ROOT/'build'/('dist-v'+version)
    source=dist/'小小领航员'
    output=ROOT/'build/releases'/('v'+version)
    for path in (args.compiler,args.webview_installer):
        if not path.is_file():raise FileNotFoundError(path)
    # 依赖下载后必须由微软签名；阻止把非官方安装组件带入成品。
    subprocess.run([shutil.which('pwsh') or 'powershell','-NoProfile','-Command',
        "$sig=Get-AuthenticodeSignature -LiteralPath $env:LN_WEBVIEW_INSTALLER; if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike '*O=Microsoft Corporation*') {exit 1}"],
        env={**os.environ,'LN_WEBVIEW_INSTALLER':str(args.webview_installer.resolve())},check=True)
    if not args.skip_app_build:
        subprocess.run([sys.executable,str(VA/'packaging/build_dist.py'),'--dist-root',str(dist)],cwd=ROOT,check=True)
    if (source/'VERSION').read_text(encoding='utf-8').strip()!=version:raise ValueError('发行版本不一致')
    output.mkdir(parents=True,exist_ok=True)
    subprocess.run([str(args.compiler.resolve()),'/DAppVersion='+version,'/DAppSource='+str(source),
        '/DOutputRoot='+str(output),'/DWebViewInstaller='+str(args.webview_installer.resolve()),
        str(VA/'packaging/installer.iss')],check=True)
    exe=output/f'LittleNavigator-{version}-Setup-x64.exe'
    digest=hashlib.sha256(exe.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(f'{digest}  {exe.name}\n',encoding='ascii')
    print(f'完成：{exe}\n{exe.stat().st_size/1024/1024:.1f} MiB\nSHA256 {digest}')

if __name__=='__main__':main()
