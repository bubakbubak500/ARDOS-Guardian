#define MyAppName "Guardian"
#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif
#define MyAppPublisher "OK7PS"
#define MyAppExeName "Guardian.exe"

[Setup]
AppId={{CF48D1B9-ABC0-4DC5-A97E-00334B9DF040}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/bubakbubak500/ARDOS-Guardian
AppSupportURL=https://github.com/bubakbubak500/ARDOS-Guardian/issues
AppUpdatesURL=https://github.com/bubakbubak500/ARDOS-Guardian/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=Guardian-{#MyAppVersion}-setup-win-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern dynamic
SetupIconFile=..\guardian\assets\guardian.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} Windows installer
#ifdef EnableSigning
SignTool=guardiansign
SignedUninstaller=yes
#endif
; Operator data lives in %APPDATA%\Guardian and is deliberately preserved.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"

[CustomMessages]
english.DependencyTitle=External radio software
english.DependencyDescription=Guardian includes Python and its libraries. Check the separately licensed radio tools below.
english.DependencyResults=Detected tools:
english.Found=Found
english.Missing=Not found
english.HamlibLabel=Hamlib rigctld
english.VaraFmLabel=VARA FM
english.VaraHfLabel=VARA HF
english.DependencyHint=Missing tools are not installed silently. After setup, Station readiness can download verified Hamlib and pinned official VARA FM/HF archives only after confirmation.
english.OpenHamlib=Open official Hamlib releases
english.OpenVara=Open official VARA downloads
english.UpgradeDetected=Existing version %1 was detected. Setup will update it to %2 and preserve Guardian data and settings.
czech.DependencyTitle=Externí rádiový software
czech.DependencyDescription=Guardian již obsahuje Python a své knihovny. Zkontrolujte samostatně licencované rádiové nástroje.
czech.DependencyResults=Zjištěné nástroje:
czech.Found=Nalezeno
czech.Missing=Nenalezeno
czech.HamlibLabel=Hamlib rigctld
czech.VaraFmLabel=VARA FM
czech.VaraHfLabel=VARA HF
czech.DependencyHint=Chybějící nástroje se neinstalují bez souhlasu. Po instalaci může Připravenost stanice po potvrzení stáhnout ověřený Hamlib a připnuté oficiální archivy VARA FM/HF.
czech.OpenHamlib=Otevřít oficiální Hamlib releases
czech.OpenVara=Otevřít oficiální VARA downloads
czech.UpgradeDetected=Byla nalezena verze %1. Instalátor ji aktualizuje na %2 a zachová data i nastavení Guardianu.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Guardian\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\guardian\assets\guardian.ico"; DestDir: "{app}"; DestName: "Guardian.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\Guardian.ico"; AppUserModelID: "OK7PS.ARDOSGuardian"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\Guardian.ico"; AppUserModelID: "OK7PS.ARDOSGuardian"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DependencyPage: TOutputMsgMemoWizardPage;
  HamlibMissing: Boolean;
  VaraFmMissing: Boolean;
  VaraHfMissing: Boolean;
  ExistingVersion: String;
  HamlibLink: TNewStaticText;
  VaraLink: TNewStaticText;

function ExecutableInPath(const Name: String): Boolean;
begin
  Result := FileSearch(Name, GetEnv('PATH')) <> '';
end;

function VersionedHamlibInstalled(const BaseDir: String): Boolean;
var
  FindRec: TFindRec;
begin
  Result := False;
  if FindFirst(AddBackslash(BaseDir) + 'hamlib-w64-*', FindRec) then
  begin
    try
      repeat
        if ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
          FileExists(AddBackslash(BaseDir) + FindRec.Name + '\bin\rigctld.exe') then
        begin
          Result := True;
          Exit;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function HamlibInstalled: Boolean;
begin
  Result :=
    ExecutableInPath('rigctld.exe') or
    FileExists(ExpandConstant('{pf}\Hamlib\bin\rigctld.exe')) or
    VersionedHamlibInstalled(ExpandConstant('{pf}')) or
    FileExists(ExpandConstant('{localappdata}\Programs\Hamlib\bin\rigctld.exe')) or
    FileExists(ExpandConstant('{userappdata}\Guardian\hamlib\bin\rigctld.exe')) or
    VersionedHamlibInstalled(ExpandConstant('{userappdata}\Guardian\hamlib'));
end;

function VaraFmInstalled: Boolean;
begin
  Result :=
    ExecutableInPath('VARAFM.exe') or
    FileExists(ExpandConstant('{pf}\VARA FM\VARAFM.exe')) or
    FileExists(ExpandConstant('{localappdata}\Programs\VARA FM\VARAFM.exe')) or
    FileExists(ExpandConstant('{sd}\VARA FM\VARAFM.exe'));
end;

function VaraHfInstalled: Boolean;
begin
  Result :=
    ExecutableInPath('VARA.exe') or
    FileExists(ExpandConstant('{pf}\VARA\VARA.exe')) or
    FileExists(ExpandConstant('{pf}\VARA HF\VARA.exe')) or
    FileExists(ExpandConstant('{localappdata}\Programs\VARA\VARA.exe')) or
    FileExists(ExpandConstant('{sd}\VARA\VARA.exe'));
end;

function DetectionText(const Missing: Boolean): String;
begin
  if Missing then
    Result := CustomMessage('Missing')
  else
    Result := CustomMessage('Found');
end;

function ExistingInstallationVersion(var Version: String): Boolean;
var
  Key: String;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{CF48D1B9-ABC0-4DC5-A97E-00334B9DF040}_is1';
  Result :=
    RegQueryStringValue(HKCU, Key, 'DisplayVersion', Version) or
    RegQueryStringValue(HKLM, Key, 'DisplayVersion', Version);
end;

procedure OpenHamlib(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('', 'https://github.com/Hamlib/Hamlib/releases/latest', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure OpenVara(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('', 'https://downloads.winlink.org/VARA%20Products/', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure InitializeWizard;
var
  Summary: String;
begin
  HamlibMissing := not HamlibInstalled;
  VaraFmMissing := not VaraFmInstalled;
  VaraHfMissing := not VaraHfInstalled;
  Summary :=
    CustomMessage('HamlibLabel') + ': ' + DetectionText(HamlibMissing) + #13#10 +
    CustomMessage('VaraFmLabel') + ': ' + DetectionText(VaraFmMissing) + #13#10 +
    CustomMessage('VaraHfLabel') + ': ' + DetectionText(VaraHfMissing) + #13#10#13#10 +
    CustomMessage('DependencyHint');
  if ExistingInstallationVersion(ExistingVersion) then
    Summary :=
      FmtMessage(CustomMessage('UpgradeDetected'), [ExistingVersion, '{#MyAppVersion}']) +
      Chr(13) + Chr(10) + Chr(13) + Chr(10) + Summary;

  DependencyPage := CreateOutputMsgMemoPage(
    wpSelectDir,
    CustomMessage('DependencyTitle'),
    CustomMessage('DependencyDescription'),
    CustomMessage('DependencyResults'),
    Summary);

  HamlibLink := TNewStaticText.Create(DependencyPage);
  HamlibLink.Parent := DependencyPage.Surface;
  HamlibLink.Caption := CustomMessage('OpenHamlib');
  HamlibLink.Cursor := crHand;
  HamlibLink.Font.Color := clBlue;
  HamlibLink.Font.Style := [fsUnderline];
  HamlibLink.Top := DependencyPage.RichEditViewer.Top + DependencyPage.RichEditViewer.Height + ScaleY(8);
  HamlibLink.OnClick := @OpenHamlib;

  VaraLink := TNewStaticText.Create(DependencyPage);
  VaraLink.Parent := DependencyPage.Surface;
  VaraLink.Caption := CustomMessage('OpenVara');
  VaraLink.Cursor := crHand;
  VaraLink.Font.Color := clBlue;
  VaraLink.Font.Style := [fsUnderline];
  VaraLink.Top := HamlibLink.Top + HamlibLink.Height + ScaleY(6);
  VaraLink.OnClick := @OpenVara;
end;
