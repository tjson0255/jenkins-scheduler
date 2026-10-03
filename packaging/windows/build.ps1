<#
.SYNOPSIS
  Windows 用のインストーラ（Setup.exe）と、インストール不要の zip を作る（Windows 上で実行。GitHub Actions から呼ばれる）。

.DESCRIPTION
  1. アプリのファイルを build\windows\app に集める
  2. いま動いている Python と同じバージョンの「埋め込み用 Python」を同梱する（インストール先に Python は不要）
  3. 依存ライブラリを app\lib に入れる
  4. WinSW（サービス化）を同梱する
  5. 同梱した Python で起動できることを確かめる
  6. zip 版と、Inno Setup で Setup.exe を作る
#>
param(
    [string]$WinswVersion = "v2.12.0",
    [string]$OutDir = "dist",
    [string]$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Build = Join-Path $Root "build\windows"
$App = Join-Path $Build "app"
$Out = Join-Path $Root $OutDir
if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }
New-Item -ItemType Directory -Force $App, $Out | Out-Null

$pyVersion = (python -c "import platform; print(platform.python_version())").Trim()
$appVersion = (python -c "import sys; sys.path.insert(0, r'$Root'); import app; print(app.__version__)").Trim()
Write-Host "アプリ $appVersion / 同梱する Python $pyVersion"

# ---- 1. アプリのファイル ----
foreach ($item in "app", "static", "alembic", "deploy", "alembic.ini", "README.md", "LICENSE", "THIRD-PARTY-NOTICES.txt", "licenses", ".env.example", "seed.toml.example", "requirements.txt") {
    Copy-Item -Recurse -Force (Join-Path $Root $item) $App
}
# モックモード（JENKINS_MOCK=true）で使うジョブ定義
New-Item -ItemType Directory -Force (Join-Path $App "tests\fixtures") | Out-Null
Copy-Item -Recurse (Join-Path $Root "tests\fixtures\jenkins") (Join-Path $App "tests\fixtures")
Copy-Item -Recurse (Join-Path $PSScriptRoot "scripts") (Join-Path $App "scripts")
Get-ChildItem $App -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

# ---- 2. 埋め込み用 Python ----
$embedZip = Join-Path $Build "python-embed.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip" -OutFile $embedZip
Expand-Archive $embedZip (Join-Path $App "python")
$pth = Get-ChildItem (Join-Path $App "python") -Filter "python*._pth" | Select-Object -First 1
# アプリ本体（..）と依存ライブラリ（..\lib）を読み込めるようにする
Set-Content -Path $pth.FullName -Encoding ascii -Value @("$($pth.BaseName).zip", ".", "..", "..\lib", "import site")

# ---- 3. 依存ライブラリ ----
python -m pip install --disable-pip-version-check --only-binary=:all: --target (Join-Path $App "lib") -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE) { throw "依存ライブラリのインストールに失敗しました" }

# ---- 4. WinSW ----
$winsw = Join-Path $App "deploy\winsw"
Invoke-WebRequest "https://github.com/winsw/winsw/releases/download/$WinswVersion/WinSW-x64.exe" -OutFile (Join-Path $winsw "scheduler-service.exe")
Get-FileHash (Join-Path $winsw "scheduler-service.exe") -Algorithm SHA256 | Format-List
$xml = Join-Path $winsw "scheduler-service.xml"
# 同梱の Python で起動する（ソースから入れる場合は .venv を使う）
$content = [IO.File]::ReadAllText($xml).Replace('%BASE%\..\..\.venv\Scripts\python.exe', '%BASE%\..\..\python\python.exe')
[IO.File]::WriteAllText($xml, $content, (New-Object Text.UTF8Encoding $false))

# ---- 5. 同梱した Python で起動できるか ----
$bundledPy = Join-Path $App "python\python.exe"
& $bundledPy -c "import app.main, sqlite3, ssl; print('import ok')"
if ($LASTEXITCODE) { throw "同梱した Python でアプリを読み込めません" }
& $bundledPy -m compileall -q (Join-Path $App "app") (Join-Path $App "lib") | Out-Null

# ---- 6. zip 版と Setup.exe ----
$zip = Join-Path $Out "jenkins-scheduler-$appVersion-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $App "*") -DestinationPath $zip
& $Iscc "/DAppVersion=$appVersion" "/DSourceDir=$App" "/DOutputDir=$Out" (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE) { throw "Inno Setup でのビルドに失敗しました" }
Get-ChildItem $Out | Format-Table Name, Length
