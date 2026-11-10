; Inno Setup script for AutoBloodWeb. Built by scripts/build_release.py, which passes
; AppVersion, SourceDir (the PyInstaller one-folder app) and OutputDir.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\dist\AutoBloodWeb"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\release"
#endif

[Setup]
AppId={{6F2C7E2B-4C1E-4E8B-9C8F-2B1A6D3E7A51}
AppName=AutoBloodWeb
AppVersion={#AppVersion}
AppVerName=AutoBloodWeb {#AppVersion}
AppPublisher=AutoBloodWeb contributors
DefaultDirName={autopf}\AutoBloodWeb
DefaultGroupName=AutoBloodWeb
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=AutoBloodWeb-{#AppVersion}-setup
SetupIconFile={#SourceDir}\assets\app_icon.ico
UninstallDisplayIcon={app}\AutoBloodWeb.exe
LicenseFile={#SourceDir}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AutoBloodWeb"; Filename: "{app}\AutoBloodWeb.exe"
Name: "{group}\{cm:UninstallProgram,AutoBloodWeb}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AutoBloodWeb"; Filename: "{app}\AutoBloodWeb.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AutoBloodWeb.exe"; Description: "{cm:LaunchProgram,AutoBloodWeb}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the "Start with DBD" logon task if the user enabled it.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""AutoBloodWeb Watcher"" /F"; Flags: runhidden; RunOnceId: "RemoveWatcherTask"

[UninstallDelete]
; Settings, logs and diagnostics the app wrote next to itself.
Type: filesandordirs; Name: "{app}\debug"
Type: filesandordirs; Name: "{app}\data"
