<# .env を書き換えた後に、設定を反映するためサービスを再起動する #>
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}
Restart-Service "jenkins-scheduler"
Get-Service "jenkins-scheduler" | Format-Table -AutoSize
Read-Host "Enter で閉じます"
