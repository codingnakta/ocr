; Inno Setup script for 필기봇 OCR Studio (per-user install, no admin rights needed)
; Built by .github/workflows/build-windows.yml:  iscc installer.iss /DAppVersion=1.0.N

#define AppName "필기봇 OCR Studio"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{8C5E2F4B-1F7A-4C2E-9A3D-6B1E0F2C7D11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=codingnakta
DefaultDirName={localappdata}\Programs\Pilgibot
DefaultGroupName=Pilgibot
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Pilgibot-Setup
Compression=lzma2/fast
SolidCompression=yes
SetupIconFile=pilgibot.ico
UninstallDisplayIcon={app}\Pilgibot.exe
UninstallDisplayName={#AppName}
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "바로가기:"

[Files]
Source: "..\dist\Pilgibot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\필기봇 OCR Studio"; Filename: "{app}\Pilgibot.exe"
Name: "{autodesktop}\필기봇 OCR Studio"; Filename: "{app}\Pilgibot.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Pilgibot.exe"; Description: "필기봇 OCR Studio 실행"; Flags: nowait postinstall skipifsilent
