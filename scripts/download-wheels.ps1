<#
.SYNOPSIS
  オフライン環境向けに、依存ホイールと WinSW を事前取得する（インターネットに出られる端末で実行）。

.DESCRIPTION
  wheels\ に Windows x64 / Python 3.11 用のホイールを保存し、deploy\winsw\ に WinSW-x64.exe を保存する。
  リポジトリごと（wheels\ と deploy\winsw\ を含めて）オフラインの Windows に持ち込み、install.ps1 を実行する。
#>
param(
    [string]$PythonVersion = "3.11",
    [string]$WinswVersion = "v2.12.0"
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
New-Item -ItemType Directory -Force -Path wheels | Out-Null

py -m pip download -r requirements.txt -d wheels `
    --only-binary=:all: --platform win_amd64 --python-version $PythonVersion --implementation cp
if ($LASTEXITCODE -ne 0) { throw "pip download に失敗しました" }
# pip / setuptools 自体も持ち込む（venv の pip を更新する場合）
py -m pip download pip setuptools wheel -d wheels --only-binary=:all: --platform win_amd64 --python-version $PythonVersion

$exe = Join-Path $Root "deploy\winsw\WinSW-x64.exe"
if (-not (Test-Path $exe)) {
    $url = "https://github.com/winsw/winsw/releases/download/$WinswVersion/WinSW-x64.exe"
    Write-Host "WinSW を取得します: $url"
    Invoke-WebRequest -Uri $url -OutFile $exe
}
Get-FileHash $exe -Algorithm SHA256 | Format-List
Write-Host "完了: wheels\ と deploy\winsw\WinSW-x64.exe を持ち込んでください。"
