<#
.SYNOPSIS
  開発用の起動（--reload、モックモード）。http://127.0.0.1:8080/ を開く。

.EXAMPLE
  .\scripts\dev.ps1            # モックモード
  .\scripts\dev.ps1 -Real      # .env の実 Jenkins に接続
#>
param(
    [switch]$Real,
    [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    py -3.11 -m venv .venv
    & $VenvPy -m pip install -r requirements-dev.txt
}
$env:PYTHONUTF8 = "1"
if (-not $Real) { $env:JENKINS_MOCK = "true" }
if (-not $env:SEED_FILE -and (Test-Path "seed.yaml")) { $env:SEED_FILE = "seed.yaml" }
if (-not $env:APP_DATA_DIR) { $env:APP_DATA_DIR = Join-Path $Root "var" }

& $VenvPy -m app --migrate-only
# --reload は開発時のみ。本番は必ず python -m app（単一プロセス）
& $VenvPy -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload --reload-dir app --reload-dir static --workers 1
