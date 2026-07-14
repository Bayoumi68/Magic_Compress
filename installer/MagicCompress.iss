; Inno Setup script for Magic Compress
; Build the exe first (pyinstaller MagicCompress.spec), then compile this with
; the Inno Setup Compiler (ISCC.exe) to produce dist\MagicCompress-Setup.exe.

#define AppName "Magic Compress"
#define AppVersion "0.1.0"
#define AppExe "MagicCompress.exe"
#define AppPublisher "Magic Compress"

[Setup]
AppId={{8F3A1C7E-2B4D-4E6A-9C1B-5D7E9F0A2B3C}}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; Detect a running Magic Compress (it holds this mutex) and close it during
; install/upgrade/uninstall instead of failing on the locked exe.
AppMutex=MagicCompressAppMutex
CloseApplications=yes
RestartApplications=yes
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=MagicCompress-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\MagicCompress.ico
UninstallDisplayName={#AppName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; Allow either an admin (all-users) or a per-user install.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "associate"; Description: "Register {#AppName} in Explorer's ""Open with"" menu for archives (.zip, .7z, .rar, .tar, …)"; GroupDescription: "Explorer integration:"

[Files]
; PyInstaller one-dir output: the exe plus its _internal dependency folder.
Source: "..\dist\MagicCompress\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Register file associations by reusing the app's own tested code.
Filename: "{app}\{#AppExe}"; Parameters: "--register-associations"; Tasks: associate; Flags: runhidden runasoriginaluser
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "--unregister-associations"; Flags: runhidden; RunOnceId: "UnregisterAssoc"
