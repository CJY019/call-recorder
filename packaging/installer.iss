; UTF-8 source, compiled with the pinned Unicode Inno Setup compiler.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef RepoRoot
  #define RepoRoot SourcePath + ".."
#endif

[Setup]
AppId={code:GetApplicationId}
AppName=通话录音
AppVersion={#AppVersion}
AppVerName=通话录音 {#AppVersion}
AppPublisher=Call Recorder contributors
UsePreviousLanguage=no
DefaultDirName={localappdata}\Programs\CallRecorder
DefaultGroupName=通话录音
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
SetupIconFile={#RepoRoot}\call_recorder\recorder.ico
UninstallDisplayIcon={app}\CallRecorder.exe
UninstallDisplayName=通话录音 {#AppVersion}
OutputDir={#RepoRoot}\dist
OutputBaseFilename=CallRecorder-Setup-{#AppVersion}-x64
Compression=lzma2/ultra64
SolidCompression=yes
LZMANumBlockThreads=2
LicenseFile={#RepoRoot}\LICENSE
InfoAfterFile={#RepoRoot}\packaging\installed-help.txt
CloseApplications=yes
CloseApplicationsFilter=CallRecorder.exe
RestartApplications=no
RestartIfNeededByRun=no
SetupLogging=yes
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName=Call Recorder
VersionInfoDescription=通话录音安装程序

[Languages]
Name: "chinesesimp"; MessagesFile: "{#SourcePath}\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#RepoRoot}\dist\CallRecorder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\通话录音"; Filename: "{app}\CallRecorder.exe"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{autodesktop}\通话录音"; Filename: "{app}\CallRecorder.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\CallRecorder.exe"; Description: "{cm:LaunchProgram,通话录音}"; Flags: nowait postinstall skipifsilent

[Code]
function GetApplicationId(Param: String): String;
begin
  { A smoke installation gets a separate uninstall registry entry, never an
    existing user's installation. Only the verification runner sets this flag. }
  if ExpandConstant('{param:buildsmoke|0}') = '1' then
    Result := 'CallRecorder.BuildSmoke.' + ExpandConstant('{param:smoketag|default}')
  else
    Result := '{237A2B70-33B4-4BE6-8E61-5C86CDB851A4}';
end;

{ No [UninstallDelete] entries: uninstallation removes only installed files.
  User recordings, settings, logs and unrelated files are always retained. }
