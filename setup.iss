[Setup]
AppName=WBLE Agent
AppVersion=1.2.0
AppPublisher=UTAR Student Developer
DefaultDirName={autopf}\WBLE Agent
DefaultGroupName=WBLE Agent
OutputDir=.\release
OutputBaseFilename=WBLE_Agent_Setup
Compression=lzma2/ultra64
SolidCompression=yes
SetupIconFile=.\utar_logo.ico
UninstallDisplayIcon={app}\wble_agent.exe
PrivilegesRequired=lowest

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: ".\dist\UTAR_WBLE_Agent.exe"; DestDir: "{app}"; DestName: "wble_agent.exe"; Flags: ignoreversion
Source: ".\utar_logo.png"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\WBLE Agent"; Filename: "{app}\wble_agent.exe"; IconFilename: "{app}\wble_agent.exe"
Name: "{autodesktop}\WBLE Agent"; Filename: "{app}\wble_agent.exe"; Tasks: desktopicon; IconFilename: "{app}\wble_agent.exe"

[Run]
Filename: "{app}\wble_agent.exe"; Description: "Launch WBLE Agent"; Flags: nowait postinstall skipifsilent
