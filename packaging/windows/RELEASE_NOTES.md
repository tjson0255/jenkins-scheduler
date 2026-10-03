## ダウンロード

| ファイル | 内容 |
|---|---|
| `JenkinsScheduler-Setup-*.exe` | **インストーラ（おすすめ）**。Python は同梱しているので、インストール先に入れる必要はありません |
| `jenkins-scheduler-*-windows-x64.zip` | インストール不要の zip 版（自分でサービス登録する場合） |

## インストール

1. `JenkinsScheduler-Setup-*.exe` を管理者として実行する
2. 管理者パスワード（12文字以上）と、待ち受けポート（既定 8090）を入力する
3. 完了すると Windows サービス「Jenkins Scheduler」が起動し、`http://localhost:8090/` で開けます
4. スタートメニューの「設定ファイル（.env）を開く」で Jenkins の URL・ユーザー・API トークンを書き、「サービスを再起動（設定の反映）」を実行する

- 署名していないため、初回は Windows SmartScreen の警告が出ることがあります（「詳細情報」→「実行」）
- 他の PC から開く場合は、Windows ファイアウォールでポートを許可し、HTTPS の設定（README 5.6）を行ってください
- アンインストールしても、設定（.env）とデータ（%ProgramData%\jenkins-scheduler）は残ります
