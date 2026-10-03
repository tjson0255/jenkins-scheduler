<#
  インストーラから呼ばれる: DB を準備し、Windows サービスを登録して起動し、応答するまで待つ。
  ログ: %ProgramData%\jenkins-scheduler\logs\install.log
#>
$ErrorActionPreference = "Stop"
$App = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Py = Join-Path $App "python\python.exe"
$Svc = Join-Path $App "deploy\winsw\scheduler-service.exe"
$EnvFile = Join-Path $App ".env"
$ServiceName = "jenkins-scheduler"
$env:PYTHONUTF8 = "1"
# Python が出す UTF-8 をそのまま受け取り、ログが文字化けしないようにする
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
$LogDir = Join-Path $env:ProgramData "jenkins-scheduler\logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
Start-Transcript -Path (Join-Path $LogDir "install.log") -Append | Out-Null
try {
    & $Py -m app --migrate-only
    if ($LASTEXITCODE) { throw "DB の準備（マイグレーション）に失敗しました" }

    # .env は管理者と SYSTEM（サービス）だけが読めるようにする
    icacls $EnvFile /inheritance:r /grant:r "*S-1-5-32-544:(F)" "*S-1-5-18:(F)" | Out-Null

    if (-not (Get-Service $ServiceName -ErrorAction SilentlyContinue)) {
        Write-Host "サービスを登録します"
        & $Svc install
        if ($LASTEXITCODE) { throw "サービスの登録に失敗しました" }
    }
    # 起動を待ち続けて固まらないよう、sc.exe で起動要求だけ出して状態は自分で確かめる
    Write-Host "サービスを起動します"
    & sc.exe stop $ServiceName | Out-Null
    for ($i = 0; $i -lt 30 -and (Get-Service $ServiceName).Status -ne "Stopped"; $i++) { Start-Sleep 1 }
    & sc.exe start $ServiceName | Write-Host

    $port = 8080
    $m = Select-String -Path $EnvFile -Pattern '^\s*APP_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($m) { $port = [int]$m.Matches[0].Groups[1].Value }
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $h = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 3
            Write-Host "起動しました: $($h | ConvertTo-Json -Compress)"
            exit 0
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "サービスが応答しません（ポート $port）。$LogDir を確認してください"
} catch {
    Write-Error $_
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
