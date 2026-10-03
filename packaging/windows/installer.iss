;  Jenkins Scheduler のインストーラ（Inno Setup 6）
;  build.ps1 から ISCC.exe /DAppVersion=... /DSourceDir=... /DOutputDir=... で呼ばれる
;
;  サイレントインストール（自動化用）:
;    JenkinsScheduler-Setup-x.y.z.exe /VERYSILENT /ADMINPASSWORD=<12文字以上> /PORT=8090

#define AppName "Jenkins Scheduler"
#define ServiceName "jenkins-scheduler"

[Setup]
AppId={{8C7B9E1E-5A4D-4C1E-9F3B-2D6A7E1C4B90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=tjson0255
DefaultDirName={autopf}\Jenkins Scheduler
DefaultGroupName=Jenkins Scheduler
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=JenkinsScheduler-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile={#SourceDir}\LICENSE
UninstallDisplayName={#AppName}
CloseApplications=no

[Languages]
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
Name: "{commonappdata}\jenkins-scheduler"

[INI]
Filename: "{group}\Jenkins Scheduler を開く.url"; Section: "InternetShortcut"; Key: "URL"; String: "http://localhost:{code:GetPort}/"

[Icons]
Name: "{group}\設定ファイル（.env）を開く"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""{app}\scripts\open-settings.ps1"""
Name: "{group}\管理者パスワードを変更"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\set-admin-password.ps1"""
Name: "{group}\サービスを再起動（設定の反映）"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\service-restart.ps1"""
Name: "{group}\データフォルダを開く"; Filename: "{commonappdata}\jenkins-scheduler"
Name: "{group}\アンインストール"; Filename: "{uninstallexe}"

[Run]
Filename: "http://localhost:{code:GetPort}/"; Description: "Jenkins Scheduler を開く"; Flags: postinstall shellexec nowait skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\service-uninstall.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[UninstallDelete]
; 実行時に作られるキャッシュ。設定（.env）とデータ（ProgramData）は残す
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\lib"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\alembic"

[Code]
var
  PasswordPage: TInputQueryWizardPage;
  PortPage: TInputQueryWizardPage;

function IsUpgrade(): Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\.env'));
end;

function Port(): String;
begin
  if WizardSilent() then
    Result := ExpandConstant('{param:PORT|8090}')
  else
    Result := Trim(PortPage.Values[0]);
end;

function GetPort(Param: String): String;
begin
  Result := Port();
end;

function AdminPassword(): String;
begin
  if WizardSilent() then
    Result := ExpandConstant('{param:ADMINPASSWORD|}')
  else
    Result := PasswordPage.Values[0];
end;

procedure InitializeWizard();
begin
  PasswordPage := CreateInputQueryPage(wpSelectDir,
    '管理者パスワード',
    'Jenkins を操作する管理者アカウント（ユーザー名 admin）のパスワードを決めてください。',
    '12文字以上。Jenkins を操作する人だけで共有します。ログインしない人も、閲覧と自由記入の編集はできます。' + #13#10 +
    '上書きインストールでパスワードを変えない場合は、空のままにしてください。');
  PasswordPage.Add('パスワード:', True);
  PasswordPage.Add('パスワード（確認）:', True);

  PortPage := CreateInputQueryPage(PasswordPage.ID,
    '待ち受けポート',
    'Jenkins Scheduler の画面を開くポート番号です。',
    'Jenkins 本体が 8080 を使っていることが多いので、既定は 8090 です。');
  PortPage.Add('ポート番号:', False);
  PortPage.Values[0] := GetPreviousData('Port', '8090');
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'Port', Port());
end;

function ValidPort(const Value: String): Boolean;
var
  p: Integer;
begin
  p := StrToIntDef(Trim(Value), 0);
  Result := (p >= 1) and (p <= 65535);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  // サイレントインストールでも各ページの「次へ」は呼ばれる。そのときは画面の入力欄ではなく
  // コマンドラインの /ADMINPASSWORD と /PORT を確かめる（メッセージは /SUPPRESSMSGBOXES で自動的に閉じるものだけ使う）
  if CurPageID = PasswordPage.ID then begin
    if WizardSilent() then begin
      if (AdminPassword() = '') and IsUpgrade() then Exit;
      if Length(AdminPassword()) < 12 then begin
        Log('/ADMINPASSWORD が無いか、12文字未満です');
        SuppressibleMsgBox('/ADMINPASSWORD に12文字以上のパスワードを指定してください。', mbError, MB_OK, IDOK);
        Result := False;
      end;
      Exit;
    end;
    if (PasswordPage.Values[0] = '') and IsUpgrade() then Exit;
    if Length(PasswordPage.Values[0]) < 12 then begin
      SuppressibleMsgBox('パスワードは12文字以上にしてください。', mbError, MB_OK, IDOK);
      Result := False;
    end else if PasswordPage.Values[0] <> PasswordPage.Values[1] then begin
      SuppressibleMsgBox('確認のパスワードが一致しません。', mbError, MB_OK, IDOK);
      Result := False;
    end;
  end else if CurPageID = PortPage.ID then begin
    if not ValidPort(Port()) then begin
      Log('ポート番号が不正です: ' + Port());
      SuppressibleMsgBox('ポート番号は 1〜65535 で指定してください。', mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

function RunHidden(const FileName, Params: String): Integer;
var
  rc: Integer;
begin
  if not Exec(FileName, Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, rc) then
    rc := -1;
  Result := rc;
end;

function PowerShell(const Script: String): Integer;
begin
  Result := RunHidden(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\') + Script + '"');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
begin
  // 上書きインストールでは、ファイルを置き換える前にサービスを止める
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -Command "Stop-Service ' + '{#ServiceName}' + ' -ErrorAction SilentlyContinue"',
    '', SW_HIDE, ewWaitUntilTerminated, rc);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  py, pwFile: String;
  rc: Integer;
  lines: TArrayOfString;
begin
  if CurStep <> ssPostInstall then Exit;
  py := ExpandConstant('{app}\python\python.exe');

  // .env を作り（既にあれば残す）、ポートと認証の方式を書き込む
  rc := RunHidden(py, '-m app --init-env --set-env APP_PORT=' + Port() + ' --set-env AUTH_MODE=shared_admin');
  if rc <> 0 then
    SuppressibleMsgBox('設定ファイル（.env）の作成に失敗しました（コード ' + IntToStr(rc) + '）。', mbError, MB_OK, IDOK);

  // 管理者パスワードは一時ファイル経由で渡し、ハッシュにして .env に書く（コマンドラインに平文を残さない）
  if AdminPassword() <> '' then begin
    pwFile := ExpandConstant('{tmp}\admin-password.txt');
    SetArrayLength(lines, 1);
    lines[0] := AdminPassword();
    SaveStringsToUTF8File(pwFile, lines, False);
    rc := RunHidden(py, '-m app --set-admin-password-file "' + pwFile + '"');
    DeleteFile(pwFile);
    if rc <> 0 then
      SuppressibleMsgBox('管理者パスワードの設定に失敗しました。スタートメニューの「管理者パスワードを変更」から設定してください。', mbError, MB_OK, IDOK);
  end;

  // DB の準備とサービスの登録・起動
  rc := PowerShell('service-install.ps1');
  if rc <> 0 then
    SuppressibleMsgBox('サービスの登録・起動に失敗しました。' + #13#10 +
      'ログ: ' + ExpandConstant('{commonappdata}') + '\jenkins-scheduler\logs\install.log', mbError, MB_OK, IDOK);
end;
