<# 設定ファイル（.env）を管理者としてメモ帳で開く（Program Files 内なので管理者でないと保存できない） #>
$EnvFile = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path ".env"
Start-Process notepad.exe -Verb RunAs -ArgumentList "`"$EnvFile`""
