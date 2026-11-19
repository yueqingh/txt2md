; Inno Setup 脚本：由 GitHub Actions 自动调用，版本号通过 /DAppVersion=x.y.z 传入
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B6B3C8D2-6A0E-4C55-9E7B-0A6B1F4E2C11}
AppName=txt2md
AppVersion={#AppVersion}
AppPublisher=txt2md
DefaultDirName={autopf}\txt2md
DefaultGroupName=txt2md
PrivilegesRequired=lowest
OutputDir=..\release
OutputBaseFilename=txt2md-setup-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\txt2md.exe
ArchitecturesInstallIn64BitMode=x64

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\txt2md\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs

[Icons]
Name: "{autoprograms}\txt2md"; Filename: "{app}\txt2md.exe"
Name: "{autodesktop}\txt2md"; Filename: "{app}\txt2md.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\txt2md.exe"; Description: "Launch txt2md"; Flags: nowait postinstall skipifsilent
