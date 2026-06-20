; Inno Setup script for Burnmeter — produces a per-user Windows installer.
; Built by packaging/build_installer.ps1 (passes /DMyAppVersion=...).
; Per-user install (no admin/UAC) into %LOCALAPPDATA%\Programs\Burnmeter, like
; VS Code / Obsidian. Start-menu + optional desktop shortcut + uninstaller.

#define MyAppName "Burnmeter"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "Cihan Atak"
#define MyAppURL "https://burnmeter.dev"
#define MyAppExeName "Burnmeter.exe"

[Setup]
AppId={{B0F2E1A4-7C3D-4E9A-9F11-2A6C5D8E0B47}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\Burnmeter
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=BurnmeterSetup-{#MyAppVersion}
SetupIconFile=..\burnmeter\assets\burnmeter.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Auto-update: CloseApplications closes the running app (frees the files for an
; in-place upgrade). RestartApplications is OFF on purpose — the Restart Manager
; restarts EVERY process it closed (window + tray), and the tray re-opens a window,
; so it stacked multiple windows. Instead the [Run] entry below relaunches EXACTLY
; ONE instance after the upgrade (and the app's own single-instance guard dedupes
; any stragglers), so an update is a clean single-window restart.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\Burnmeter\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Relaunch EXACTLY ONE instance after the upgrade. Plain "nowait" (no postinstall,
; no skipifsilent) so it fires in a SILENT in-place update too — postinstall entries
; are gated to the Finished page and may be skipped in silent mode. The app's single-
; instance guard ensures this never stacks with anything still closing.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait

[UninstallDelete]
; the app's runtime data/caches live under the user profile (~/.burnmeter); leave
; them (sync config etc.) on uninstall — only the program files are removed.
