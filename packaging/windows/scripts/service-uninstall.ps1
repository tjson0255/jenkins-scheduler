<# アンインストーラから呼ばれる: サービスを止めて登録を外す（データと .env は残す） #>
$App = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Svc = Join-Path $App "deploy\winsw\scheduler-service.exe"
if (Get-Service "jenkins-scheduler" -ErrorAction SilentlyContinue) {
    Stop-Service "jenkins-scheduler" -ErrorAction SilentlyContinue
    if (Test-Path $Svc) { & $Svc uninstall } else { & sc.exe delete "jenkins-scheduler" }
}
exit 0
