#define MyAppName "Fenestra"
#define MyAppPublisher "Yusuf Qwareeq"
#define MyAppURL "https://github.com/qwareeq8/fenestra"
#define MyAppExeName "Fenestra.exe"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif

#ifndef PayloadArchitecture
  #error "PayloadArchitecture must be defined as x64."
#endif

; The x64 payload also installs on Windows 11 ARM64, which runs it through
; x64 emulation.
#if PayloadArchitecture == "x64"
  #define PayloadArchitecturesAllowed "x64compatible"
  #define PayloadArchitecturesInstallIn64BitMode "x64compatible"
  #define PayloadMinVersion "10.0.17763"
#else
  #error "Unsupported PayloadArchitecture. Use x64."
#endif

#define PayloadDirectory AddBackslash(SourcePath) + "..\dist\" + PayloadArchitecture + "\Fenestra"

#pragma message "FENESTRA_EFFECTIVE_ARCHITECTURE=" + PayloadArchitecture
#pragma message "FENESTRA_EFFECTIVE_ALLOWED=" + PayloadArchitecturesAllowed
#pragma message "FENESTRA_EFFECTIVE_64BIT_MODE=" + PayloadArchitecturesInstallIn64BitMode
#pragma message "FENESTRA_EFFECTIVE_MIN_VERSION=" + PayloadMinVersion
#pragma message "FENESTRA_EFFECTIVE_PAYLOAD=" + PayloadDirectory

#if !FileExists(PayloadDirectory + "\Fenestra.exe")
  #error "The architecture-qualified payload is missing Fenestra.exe. Build dist/<architecture>/Fenestra first."
#endif

[Setup]
AppId={{7E600AE0-DB72-4341-9111-BAC7D66D4FF7}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
OutputDir=dist
OutputBaseFilename={#MyAppName}Setup-{#MyAppVersion}-{#PayloadArchitecture}
OutputManifestFile={#MyAppName}Setup-{#MyAppVersion}-{#PayloadArchitecture}-manifest.txt
SetupIconFile={#SourcePath}\..\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
WizardImageFile={#SourcePath}\..\branding\installer-wizard.bmp,{#SourcePath}\..\branding\installer-wizard_2x.bmp
WizardSmallImageFile={#SourcePath}\..\branding\installer-header.bmp,{#SourcePath}\..\branding\installer-header_2x.bmp
WizardImageAlphaFormat=none
ArchitecturesAllowed={#PayloadArchitecturesAllowed}
ArchitecturesInstallIn64BitMode={#PayloadArchitecturesInstallIn64BitMode}
MinVersion={#PayloadMinVersion}
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
AppMutex=Fenestra_Mutex
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer ({#PayloadArchitecture})

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[InstallDelete]
; Remove the prior application payload before copying a different architecture.
; User settings, logs, and Explorer recovery backups live in
; per-user locations and are intentionally outside this per-machine installer.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"

[Files]
Source: "{#PayloadDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
; Autostart uses an elevated logon task; deleting it requires administrator
; rights, so this entry runs in the elevated uninstall context.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--remove-startup-task"; Flags: runhidden; RunOnceId: "RemoveStartupTask"
