#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppSource
  #error AppSource is required
#endif
#ifndef OutputRoot
  #error OutputRoot is required
#endif
#ifndef WebViewInstaller
  #error WebViewInstaller is required
#endif
#ifndef WebViewExe
  #error WebViewExe is required
#endif

[Setup]
AppId={{A7A95DB9-1B7D-4A02-BC40-5230538E290C}
AppName=小小领航员
AppVersion={#AppVersion}
AppPublisher=xushengchen / Moondrop-Dox Racing
DefaultDirName={autopf}\LittleNavigator
DefaultGroupName=小小领航员
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19041
OutputDir={#OutputRoot}
OutputBaseFilename=LittleNavigator-{#AppVersion}-Setup-x64
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\小小领航员.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\LittleNavigator
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
InfoBeforeFile=安装说明.txt
UninstallDisplayName=小小领航员 {#AppVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Excludes: "\config.local.yaml,\logs\*,\state\*,\webview\*,\unins*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "installed-mode"; DestDir: "{app}"; Flags: ignoreversion
; WebView2 安装组件：默认是微软官方引导器（约 2 MB，缺运行时才联网补装），
; 也可指向离线 Standalone Installer。本行不落盘，仅在系统缺运行时时提取执行。
Source: "{#WebViewInstaller}"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{group}\小小领航员"; Filename: "{app}\小小领航员.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\小小领航员"; Filename: "{app}\小小领航员.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\卸载小小领航员"; Filename: "{uninstallexe}"

[InstallDelete]
; 升级时只清理旧版随包文档，不删除用户自建文件。
Type: files; Name: "{app}\v1.1更新与验收.md"
Type: files; Name: "{app}\v1.2更新与验收.md"
Type: files; Name: "{app}\比赛常用问法.md"
Type: files; Name: "{app}\安装与GitHub发布说明.md"
Type: files; Name: "{app}\豆包语音接入说明.md"
Type: files; Name: "{app}\六变体回复审阅.md"
Type: files; Name: "{app}\新增语音文案.json"

[Run]
Filename: "{app}\小小领航员.exe"; Description: "启动小小领航员"; Flags: nowait postinstall skipifsilent

[Code]
function HasWebView2: Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code, I: Integer;
begin
  Result := '';
  if not HasWebView2 then begin
    ExtractTemporaryFile('{#WebViewExe}');
    if not Exec(ExpandConstant('{tmp}\{#WebViewExe}'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := '无法启动 WebView2 安装程序。请确认网络可用，或手动安装 Microsoft Edge WebView2 Runtime 后重试。'
    else begin
      // 微软已知问题：引导器/离线安装器用 /silent /install 被别的安装器调用时会提前退出，
      // 真正的安装其实还在后台进行。这里最多轮询 3 分钟，避免把“还没装完”误判成“装失败”。
      I := 0;
      while (I < 90) and (not HasWebView2) do begin
        Sleep(2000);
        I := I + 1;
      end;
      if not HasWebView2 then
        Result := 'WebView2 Runtime 未完成安装。引导器需要联网下载，请检查网络后重试，或手动安装 Microsoft Edge WebView2 Runtime。';
    end;
  end;
end;
