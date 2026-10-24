"""生成适合 GitHub Releases 的完整安装包与 SHA-256 校验文件。

WebView2 组件默认用微软官方引导器（MicrosoftEdgeWebview2Setup.exe，约 2 MB）：
绝大多数 Windows 已随系统自带 WebView2 运行时，缺了才在安装时联网补装，
因此不再把 ~180 MB 的离线运行时打进安装包。
要出全离线版，把 --webview-installer 指向 Evergreen Standalone Installer
（MicrosoftEdgeWebView2RuntimeInstallerX64.exe）即可；installer.iss 按传入的文件名自动适配。
"""
import argparse,hashlib,pathlib,subprocess,sys,os,shutil
VA=pathlib.Path(__file__).resolve().parents[1]
ROOT=VA.parent

def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--compiler',type=pathlib.Path,default=ROOT/'build/tooling/inno/ISCC.exe')
    ap.add_argument('--webview-installer',type=pathlib.Path,default=ROOT/'build/tooling/MicrosoftEdgeWebview2Setup.exe')
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
        '/DWebViewExe='+args.webview_installer.resolve().name,
        str(VA/'packaging/installer.iss')],check=True)
    exe=output/f'LittleNavigator-{version}-Setup-x64.exe'
    digest=hashlib.sha256(exe.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(f'{digest}  {exe.name}\n',encoding='ascii')
    print(f'完成：{exe}\n{exe.stat().st_size/1024/1024:.1f} MiB\nSHA256 {digest}')

if __name__=='__main__':main()
