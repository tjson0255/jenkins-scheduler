<#
.SYNOPSIS
  Jenkins Scheduler を Windows サービスとしてインストールする。

.DESCRIPTION
  1. venv (.venv) を作成
  2. 依存をインストール（wheels\ があればオフライン: --no-index --find-links wheels）
  3. .env が無ければ .env.example からコピーし、アクセス権を絞る
  4. DB マイグレーション
  5. WinSW でサービス登録（自動・遅延開始）、実行アカウントを設定
  6. データディレクトリの権限を設定してサービスを開始

.EXAMPLE
  # 管理者の PowerShell で
  .\scripts\install.ps1 -ServiceAccount ".\svc-scheduler"
#>
[CmdletBinding()]
param(
    [string]$Python = "py",
    [string]$PythonArgs = "-3.11",
    [string]$ServiceAccount = "",
    [string]$DataDir = "$env:ProgramData\jenkins-scheduler",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$Wheels = Join-Path $Root "wheels"
$WinswDir = Join-Path $Root "deploy\winsw"
$ServiceExe = Join-Path $WinswDir "scheduler-service.exe"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "管理者として実行してください"
    }
}
Assert-Admin
Set-Location $Root

# ---- 1. venv ----
if (-not (Test-Path $VenvPy)) {
    Write-Host "venv を作成します: $Venv"
    & $Python $PythonArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "venv の作成に失敗しました（Python 3.11 以上が必要です）" }
}
& $VenvPy -c "import sys; assert sys.version_info >= (3, 11), sys.version"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 以上が必要です" }

# ---- 2. 依存 ----
if (Test-Path $Wheels) {
    Write-Host "オフラインのホイールから依存をインストールします: $Wheels"
    & $VenvPy -m pip install --no-index --find-links $Wheels -r requirements.txt
} else {
    Write-Host "wheels\ が無いためインデックスからインストールします"
    & $VenvPy -m pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { throw "依存のインストールに失敗しました" }

# ---- 3. .env ----
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Write-Host ".env を作成しました。JENKINS_URL / JENKINS_USER / JENKINS_TOKEN を設定してください: $EnvFile" -ForegroundColor Yellow
}
# .env は管理者とサービス実行アカウントだけが読めるようにする
icacls $EnvFile /inheritance:r /grant:r "*S-1-5-32-544:(F)" "*S-1-5-18:(F)" | Out-Null
if ($ServiceAccount) { icacls $EnvFile /grant:r "${ServiceAccount}:(R)" | Out-Null }

# ---- データディレクトリ ----
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "data"), (Join-Path $DataDir "logs\service") | Out-Null
if ($ServiceAccount) {
    icacls $DataDir /grant "${ServiceAccount}:(OI)(CI)M" | Out-Null
}
$env:APP_DATA_DIR = $DataDir

# ---- 4. マイグレーション ----
& $VenvPy -m app --migrate-only
if ($LASTEXITCODE -ne 0) { throw "DB マイグレーションに失敗しました" }

# ---- 5. サービス登録 ----
if (-not (Test-Path $ServiceExe)) {
    $src = Get-ChildItem $WinswDir -Filter "WinSW*.exe" | Select-Object -First 1
    if (-not $src) { throw "deploy\winsw\ に WinSW-x64.exe がありません（scripts\download-wheels.ps1 で取得できます）" }
    Copy-Item $src.FullName $ServiceExe
}
if (Get-Service -Name "jenkins-scheduler" -ErrorAction SilentlyContinue) {
    Write-Host "サービスは登録済みです（再登録する場合は uninstall.ps1 を先に実行）"
} else {
    & $ServiceExe install
    if ($LASTEXITCODE -ne 0) { throw "サービス登録に失敗しました" }
}
# APP_DATA_DIR を既定以外にした場合はサービスの環境変数として渡す
if ($DataDir -ne "$env:ProgramData\jenkins-scheduler") {
    $key = "HKLM:\SYSTEM\CurrentControlSet\Services\jenkins-scheduler"
    New-ItemProperty -Path $key -Name Environment -PropertyType MultiString -Value @("APP_DATA_DIR=$DataDir") -Force | Out-Null
}

if ($ServiceAccount) {
    $cred = Get-Credential -UserName $ServiceAccount -Message "サービス実行アカウントのパスワード"
    $plain = $cred.GetNetworkCredential().Password
    & sc.exe config jenkins-scheduler obj= $ServiceAccount password= $plain | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "実行アカウントの設定に失敗しました" }
    Write-Host "実行アカウントを $ServiceAccount に設定しました。「サービスとしてログオン」権限が付与されていることを確認してください（secpol.msc）。"
} else {
    Write-Warning "ServiceAccount 未指定のため LocalSystem で動作します。本番では専用アカウントを指定してください。"
}

if (-not $NoStart) {
    Start-Service jenkins-scheduler
    Start-Sleep -Seconds 3
    Get-Service jenkins-scheduler | Format-Table -AutoSize
}
Write-Host "完了しました。ヘルスチェック: Invoke-RestMethod http://127.0.0.1:8080/api/health"
