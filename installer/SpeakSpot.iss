#ifndef Payload
  #error Build this installer with Build-Windows.ps1.
#endif
#ifndef AppVersion
  #define AppVersion "1.0.3"
#endif
#define DesktopAppId "Codingmosture.SpeakSpot.Desktop"

[Setup]
AppId={{D2461778-D8E2-489F-A8C1-727013E83EDC}
AppName=SpeakSpot
AppVersion={#AppVersion}
AppPublisher=SpeakSpot
AppPublisherURL=https://github.com/codingmosture/SpeakSpot
DefaultDirName={localappdata}\Programs\SpeakSpot
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0.17763
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
SetupArchitecture=x64
AppMutex=Global\SpeakSpotDesktop-27395
SetupMutex=Global\SpeakSpotDesktop-Setup
UninstallDisplayIcon={app}\SpeakSpot.exe
UninstallDisplayName=SpeakSpot
OutputBaseFilename=SpeakSpot-Setup
SetupIconFile={#Payload}\SpeakSpot.ico
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
ExtraDiskSpaceRequired=4500000000
SetupLogging=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
korean.SetupNotice=처음 설치할 때 인터넷으로 PDF 분석, 한글·영문 OCR과 BGE-M3 모델을 준비합니다. Google Chrome이나 Microsoft Visual C++ 런타임이 필요한 경우 설치 중 Windows에서 관리자 승인을 요청할 수 있습니다.
english.SetupNotice=The first installation downloads the PDF layout, Korean/English OCR, and BGE-M3 models. Windows may ask for administrator approval if Google Chrome or the Microsoft Visual C++ runtime needs to be installed.
korean.PreparingModels=AI 모델을 다운로드하고 검사하고 있습니다. 인터넷 속도에 따라 시간이 걸립니다.
english.PreparingModels=Downloading and checking AI models. This can take a while depending on your connection.
korean.PrerequisitesFailed=실행 환경을 준비하지 못했습니다. 인터넷 연결과 관리자 승인 여부를 확인한 후 설치를 다시 시도하세요.%n%n로그: %1
english.PrerequisitesFailed=The runtime could not be prepared. Check your internet connection and any administrator prompts, then retry.%n%nLog: %1
korean.PrerequisitesRestart=실행 환경 준비를 완료하려면 Windows를 다시 시작한 후 SpeakSpot 설치 프로그램을 다시 실행하세요.
english.PrerequisitesRestart=Restart Windows to finish preparing the runtime, then run SpeakSpot Setup again.
korean.ModelsFailed=AI 모델을 준비하지 못했습니다. 인터넷 연결과 디스크 공간을 확인한 후 설치를 다시 시도하세요.%n%n로그: %1
english.ModelsFailed=AI models could not be prepared. Check your internet connection and free disk space, then retry.%n%nLog: %1
korean.RemoveHeading=이 PC에서 함께 삭제할 SpeakSpot 데이터를 선택하세요.
english.RemoveHeading=Select the SpeakSpot data to delete from this PC.
korean.RemoveDocuments=저장한 PDF, 필기, 문서 색인 및 앱의 Chrome 설정
english.RemoveDocuments=Saved PDFs, notes, document indexes, and app Chrome settings
korean.RemoveModels=다운로드한 AI 모델
english.RemoveModels=Downloaded AI models
korean.RemoveContinue=제거 계속
english.RemoveContinue=Continue uninstalling
korean.RemovePartial=일부 데이터를 삭제하지 못했습니다. SpeakSpot 앱 창이 닫혀 있는지 확인하세요.%n%n경로: %1
english.RemovePartial=Some data could not be removed. Make sure the SpeakSpot app window is closed.%n%nPath: %1

[Files]
Source: "Prerequisites.ps1"; Flags: dontcopy
Source: "{#Payload}\SpeakSpot.exe"; DestName: "ChromeCheck.exe"; Flags: dontcopy
Source: "{#Payload}\SpeakSpot.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\SpeakSpot.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Payload}\web\*"; DestDir: "{app}\web"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Payload}\backend\*.py"; Excludes: "installed.py"; DestDir: "{app}\backend"; Flags: ignoreversion
Source: "installed.py"; DestDir: "{app}\backend"; Flags: ignoreversion; AfterInstall: PrepareModels

[Icons]
Name: "{userdesktop}\SpeakSpot"; Filename: "{app}\SpeakSpot.exe"; WorkingDir: "{app}"; IconFilename: "{app}\SpeakSpot.ico"; AppUserModelID: "{#DesktopAppId}"
Name: "{userprograms}\SpeakSpot"; Filename: "{app}\SpeakSpot.exe"; WorkingDir: "{app}"; IconFilename: "{app}\SpeakSpot.ico"; AppUserModelID: "{#DesktopAppId}"

[Run]
Filename: "{app}\SpeakSpot.exe"; Description: "{cm:LaunchProgram,SpeakSpot}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[Code]
var
  DeleteDocuments, DeleteModels: Boolean;

function DataDir: String;
begin
  Result := ExpandConstant('{localappdata}\SpeakSpotData');
end;

function Quote(const Value: String): String;
begin
  Result := '"' + Value + '"';
end;

procedure InitializeWizard;
var
  Notice: TOutputMsgWizardPage;
begin
  Notice := CreateOutputMsgPage(wpWelcome, 'SpeakSpot',
    SetupMessage(msgWizardPreparing), CustomMessage('SetupNotice'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  LogPath, Arguments: String;
begin
  Result := '';
  ExtractTemporaryFile('Prerequisites.ps1');
  ExtractTemporaryFile('ChromeCheck.exe');
  LogPath := DataDir + '\Logs\prerequisites.log';
  Arguments := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    Quote(ExpandConstant('{tmp}\Prerequisites.ps1')) + ' -Launcher ' +
    Quote(ExpandConstant('{tmp}\ChromeCheck.exe')) + ' -LogPath ' + Quote(LogPath);
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Arguments, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    Result := FmtMessage(CustomMessage('PrerequisitesFailed'), [LogPath])
  else if ExitCode = 3010 then
  begin
    NeedsRestart := True;
    Result := CustomMessage('PrerequisitesRestart');
  end
  else if ExitCode <> 0 then
    Result := FmtMessage(CustomMessage('PrerequisitesFailed'), [LogPath]);
end;

procedure PrepareModels;
var
  ExitCode: Integer;
  Arguments: String;
begin
  WizardForm.StatusLabel.Caption := CustomMessage('PreparingModels');
  WizardForm.FilenameLabel.Caption := '';
  Arguments := '-B ' + Quote(ExpandConstant('{app}\backend\installed.py')) +
    ' prepare ' + Quote(DataDir);
  if not Exec(ExpandConstant('{app}\python\python.exe'), Arguments,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) or
    (ExitCode <> 0) then
    RaiseException(FmtMessage(CustomMessage('ModelsFailed'), [DataDir + '\Logs\setup.log']));
end;

procedure SelectUninstallData;
var
  Form: TSetupForm;
  Heading: TNewStaticText;
  Documents, Models: TNewCheckBox;
  ContinueButton: TNewButton;
begin
  if UninstallSilent then
  begin
    DeleteDocuments := ExpandConstant('{param:DELETEDATA|0}') = '1';
    DeleteModels := ExpandConstant('{param:DELETEMODELS|0}') = '1';
    Exit;
  end;

  Form := CreateCustomForm(453, 161, True, True);
  try
    Form.Caption := 'SpeakSpot';
    Form.Position := poScreenCenter;

    Heading := TNewStaticText.Create(Form);
    Heading.Parent := Form;
    Heading.SetBounds(ScaleX(16), ScaleY(14), ScaleX(421), ScaleY(30));
    Heading.AutoSize := False;
    Heading.WordWrap := True;
    Heading.Caption := CustomMessage('RemoveHeading');

    Documents := TNewCheckBox.Create(Form);
    Documents.Parent := Form;
    Documents.SetBounds(ScaleX(16), ScaleY(50), ScaleX(421), ScaleY(26));
    Documents.Caption := CustomMessage('RemoveDocuments');

    Models := TNewCheckBox.Create(Form);
    Models.Parent := Form;
    Models.SetBounds(ScaleX(16), ScaleY(78), ScaleX(421), ScaleY(26));
    Models.Caption := CustomMessage('RemoveModels');

    ContinueButton := TNewButton.Create(Form);
    ContinueButton.Parent := Form;
    ContinueButton.SetBounds(ScaleX(279), ScaleY(116), ScaleX(157), ScaleY(29));
    ContinueButton.Caption := CustomMessage('RemoveContinue');
    ContinueButton.ModalResult := mrOk;
    ContinueButton.Default := True;

    if Form.ShowModal = mrOk then
    begin
      DeleteDocuments := Documents.Checked;
      DeleteModels := Models.Checked;
    end;
  finally
    Form.Free;
  end;
end;

procedure RemoveDataFolder(const Path: String);
begin
  if DirExists(Path) and not DelTree(Path, True, True, True) then
  begin
    Log(FmtMessage(CustomMessage('RemovePartial'), [Path]));
    SuppressibleMsgBox(FmtMessage(CustomMessage('RemovePartial'), [Path]),
      mbError, MB_OK, IDOK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    SelectUninstallData;
  if CurUninstallStep = usPostUninstall then
  begin
    if DeleteDocuments then
      RemoveDataFolder(DataDir + '\Documents');
    if DeleteModels then
      RemoveDataFolder(DataDir + '\Models');
    RemoveDataFolder(DataDir + '\Logs');
    RemoveDir(DataDir);
  end;
end;
