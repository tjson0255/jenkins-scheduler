<#
.SYNOPSIS
  サービスを停止して登録を削除する（DB・ログなどのデータは残す）。
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ServiceExe = Join-Path $Root "deploy\winsw\scheduler-service.exe"

if (Get-Service -Name "jenkins-scheduler" -ErrorAction SilentlyContinue) {
    Stop-Service jenkins-scheduler -ErrorAction SilentlyContinue
    if (Test-Path $ServiceExe) {
        & $ServiceExe uninstall
    } else {
        & sc.exe delete jenkins-scheduler
    }
    Write-Host "サービスを削除しました。データは $env:ProgramData\jenkins-scheduler に残っています。"
} else {
    Write-Host "サービス jenkins-scheduler は登録されていません。"
}
