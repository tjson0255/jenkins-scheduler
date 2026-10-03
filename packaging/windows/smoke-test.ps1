<#
  GitHub Actions 上で、作った Setup.exe を実際にインストールして動作を確かめる。
  サービスの起動・応答、管理者ログイン、二重起動の防止、再起動後の復帰、アンインストールまで。
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$setup = Get-ChildItem (Join-Path $Root "dist") -Filter "JenkinsScheduler-Setup-*.exe" | Select-Object -First 1
$Port = 8090
$Password = "ci-only-password-1234"   # CI 専用の値
$AppDir = Join-Path $env:ProgramFiles "Jenkins Scheduler"
$Base = "http://127.0.0.1:$Port"
$Headers = @{ "X-Requested-With" = "jenkins-scheduler" }

function Step($name) { Write-Host "`n=== $name ===" -ForegroundColor Cyan }

Step "サイレントインストール"
$p = Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/ADMINPASSWORD=$Password", "/PORT=$Port", "/LOG=$Root\install-log.txt" -Wait -PassThru
Get-Content (Join-Path $env:ProgramData "jenkins-scheduler\logs\install.log") -ErrorAction SilentlyContinue | Select-Object -Last 20
if ($p.ExitCode) { Get-Content "$Root\install-log.txt" | Select-Object -Last 40; throw "インストールに失敗しました（$($p.ExitCode)）" }

Step "サービスと応答"
Get-Service "jenkins-scheduler" | Format-List Name, Status, StartType
if ((Get-Service "jenkins-scheduler").Status -ne "Running") { throw "サービスが動いていません" }
$h = Invoke-RestMethod "$Base/api/health"
$h | ConvertTo-Json
if ($h.db -ne "ok" -or $h.dispatcher -ne "ok") { throw "ヘルスチェックが NG です" }
if ((Invoke-WebRequest "$Base/" -UseBasicParsing).StatusCode -ne 200) { throw "画面が開けません" }

Step "ログインなしの権限と管理者ログイン"
$me = Invoke-RestMethod "$Base/api/auth/me"
if ($me.auth_mode -ne "shared_admin" -or $me.can.admin) { throw "ログインなしの権限が想定と違います" }
try { Invoke-RestMethod "$Base/api/categories" -Method Post -Headers $Headers -ContentType "application/json" -Body '{"name":"x"}'; throw "ログインなしで変更できてしまいました" } catch { if ($_.Exception.Response.StatusCode.value__ -ne 403) { throw } }
$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-RestMethod "$Base/api/auth/login" -Method Post -WebSession $s -Headers $Headers -ContentType "application/json" -Body (@{ username = "admin"; password = $Password } | ConvertTo-Json) | Out-Null
if (-not (Invoke-RestMethod "$Base/api/auth/me" -WebSession $s).can.admin) { throw "管理者でログインできません" }
Invoke-RestMethod "$Base/api/categories" -Method Post -WebSession $s -Headers $Headers -ContentType "application/json" -Body '{"name":"CI"}' | Out-Null
$envText = Get-Content (Join-Path $AppDir ".env") -Raw
if ($envText -match [regex]::Escape($Password)) { throw ".env に平文のパスワードが残っています" }

Step "二重起動の防止"
$env:PYTHONUTF8 = "1"
& (Join-Path $AppDir "python\python.exe") -m app 2>&1 | Write-Host
if ($LASTEXITCODE -ne 2) { throw "二重起動が防げていません（終了コード $LASTEXITCODE）" }

Step "サービスの再起動"
Restart-Service "jenkins-scheduler"
for ($i = 0; $i -lt 30; $i++) { try { $h = Invoke-RestMethod "$Base/api/health" -TimeoutSec 3; break } catch { Start-Sleep 1 } }
if ($h.db -ne "ok") { throw "再起動後に応答しません" }
$cats = Invoke-RestMethod "$Base/api/categories"
if (-not ($cats | Where-Object { $_.name -eq "CI" })) { throw "再起動でデータが消えました" }

Step "アンインストール"
$p = Start-Process (Join-Path $AppDir "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
Start-Sleep 3
if (Get-Service "jenkins-scheduler" -ErrorAction SilentlyContinue) { throw "サービスが残っています" }
if (-not (Test-Path (Join-Path $env:ProgramData "jenkins-scheduler\data\scheduler.db"))) { throw "データが消えています（残す仕様）" }
Write-Host "`nすべて OK" -ForegroundColor Green
