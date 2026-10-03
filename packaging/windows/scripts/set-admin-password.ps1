<# 管理者アカウントのパスワードを変更する（ハッシュにして .env に書き、サービスを再起動する） #>
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}
$App = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Py = Join-Path $App "python\python.exe"
function Plain($secure) { [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)) }
$p1 = Plain (Read-Host "新しい管理者パスワード（12文字以上）" -AsSecureString)
$p2 = Plain (Read-Host "もう一度" -AsSecureString)
if ($p1 -ne $p2) { Write-Host "一致しません。" -ForegroundColor Red; Read-Host "Enter で閉じます"; exit 1 }
$tmp = New-TemporaryFile
try {
    icacls $tmp.FullName /inheritance:r /grant:r "*S-1-5-32-544:(F)" | Out-Null
    [IO.File]::WriteAllText($tmp.FullName, $p1, (New-Object Text.UTF8Encoding $false))
    & $Py -m app --set-admin-password-file $tmp.FullName
    if ($LASTEXITCODE) { Read-Host "Enter で閉じます"; exit 1 }
} finally {
    Remove-Item $tmp.FullName -Force -ErrorAction SilentlyContinue
}
Restart-Service "jenkins-scheduler"
Write-Host "変更しました。ログイン中の管理者は、そのまま使えます（次のログインから新しいパスワード）。" -ForegroundColor Green
Read-Host "Enter で閉じます"
