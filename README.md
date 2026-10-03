# Jenkins Scheduler（リリースカレンダー × Jenkins 定時キックツール）

Jenkins ジョブを **決まった時刻にパラメータ付きでキックするだけ** のツールです。
スケジュールはタイムライン（横軸＝日付、縦軸＝ジョブ）上で管理します。Jenkins 側の cron（定期実行）はすべてこのツールに移譲します。

- いつ・どのパラメータでキックするか → **このツール**
- 実行順序・依存・失敗時の制御 → **Jenkins**（ビルドセットごとの起点 Pipeline）
- パラメータ定義 → **Jenkins が正**（ツールは毎回取得する。ジョブ設定は一切変更しない）

1回のキックにつき1ジョブを起動し、その1ビルドの結果だけを追跡します。

---

## デモ

**デモサイト：** https://jenkins-scheduler-demo.onrender.com/

- Jenkins は架空のジョブ（モック）で、データは6時間ごとに初期化されます
- ログインしなくても、閲覧と、予定のアイテム・予定・カテゴリの編集ができます
- Jenkins のスケジュールなど全部を試すには、右上の「管理者ログイン」から **ユーザー `admin` ／ パスワード `demo-admin-2026`** でログインしてください（デモ専用）
- 無料枠のため、しばらく使われていないと休止します。最初の表示に 30 秒ほどかかることがあります

デモは `render.yaml`（Render の Blueprint）と `Dockerfile` で動かしています。`DEMO_MODE=true` にすると、Jenkins は必ずモックになり、起動時と `DEMO_RESET_HOURS` ごとにデモ用データを入れ直します。

## 目次

1. [まず動かす（モックモード）](#1-まず動かすモックモード)
2. [画面と操作](#2-画面と操作)
3. [構成](#3-構成)
4. [設定（.env）](#4-設定env)
5. [Windows へのインストールとサービス化](#5-windows-へのインストールとサービス化)
6. [実 Jenkins との疎通確認](#6-実-jenkins-との疎通確認)
7. [Jenkins cron からの移行手順](#7-jenkins-cron-からの移行手順)
8. [監視とアラート](#8-監視とアラート)
9. [運用上の注意](#9-運用上の注意)
10. [開発・テスト](#10-開発テスト)
11. [動作環境と使用ライブラリ](#11-動作環境と使用ライブラリ)

---

## 1. まず動かす（モックモード）

Jenkins に接続せず、`tests/fixtures/jenkins/jobs.json` の定義で動かします。キックは数秒後にランダムで成功／失敗／不安定になります。

**Windows（PowerShell）**

```powershell
copy seed.toml.example seed.toml
.\scripts\dev.ps1
```

**macOS / Linux**

```bash
cp seed.toml.example seed.toml
./scripts/dev.sh
```

ブラウザで <http://127.0.0.1:8080/> を開くと、ビルドセット7行とリリース関連の行が表示されます。

- 行の空き部分をドラッグ → スケジュール作成（ドラフトで保存 or 保存して有効化）
- バーをドラッグで移動、端をドラッグで期間変更（未実行の run が再生成されます）
- バーをクリック → 詳細パネル（基本情報・パラメータ・実行履歴・ドライラン）
- モックのジョブでは `MOCK_RESULT` パラメータを上書きすると結果を固定できます
- `tests/fixtures/jenkins/jobs.json` を書き換えると、次回の自動取得（5分おき）かアイテム画面の「Jenkins から再取得」で差分バッジ・警告が出ます
- `buildset/tools-pipeline` はモック上で「Jenkins 側 cron が残っている」状態になっており、⏰ の警告が出ます

---

## 2. 画面と操作

| 画面 | 内容 |
|---|---|
| `/` タイムライン | カテゴリ（折りたたみ可）ごとに、登録したすべてのアイテムの行を表示（予定が無くても表示）。土日は背景色。表示範囲が31日を超えると run の点は描かず、バーに件数サマリーを出します |
| 縦表示（既定） | 行=日付・列=アイテムの表。ツールバーの「縦表示」スイッチをオフにすると横表示（選択はブラウザに記憶）。スケジュールは縦の帯、run は時刻で表示。空きセルを縦にドラッグで作成、ダブルクリックでその日だけ作成、カテゴリ見出しのクリックで列を折りたたみ。左上の「⊟ 折りたたむ／⊞ 展開」でまとめて開閉（横表示も同じ位置）。期間の変更（バーの移動・リサイズ）は横表示か詳細パネルで行う |
| 詳細パネル | 基本情報（cron プリセットと次回5回のプレビュー）、パラメータ編集（Jenkins の最新のパラメータ定義からフォームを動的生成、変数（`{{run.date}}` など）の展開プレビュー、差分警告）、実行履歴（保留解除・スキップ・再実行・ビルドへのリンク）、ドライラン、今すぐ実行 |
| `/day` 1日の予定 | 上部に常に出ているカレンダーで日付を選んで、その日の Jenkins の実行（時刻・状態・ビルド）と予定を一覧表示。run を作る前の先の日付も、スケジュールから計算した予定を表示 |
| `/targets` | アイテム一覧（表示名・ジョブ・カテゴリ・有効／無効・前回ビルド実行中・並び替え・削除）・登録（Jenkins ジョブ検索）・カテゴリ管理。スケジュール一覧（スケジュール1件ずつ。警告（パラメータ定義の確認結果・保留・ジョブ・cron 残存・無効）・今すぐ実行。アイテムで絞り込み・追加・Jenkins から再取得） |
| `/audit` | ログ（操作の記録。種別・対象・操作・期間で絞り込み） |
| `/api/docs` | API ドキュメント（OpenAPI） |

### アイテムの種類

| 種類 | 内容 |
|---|---|
| Jenkins ジョブ | Jenkins のジョブを定時にキックする行。登録時にジョブの存在とパラメータ定義を確認する |
| 予定 | Jenkins には接続しない行。予定（タイトル・期間・本文）を書き込むだけで、cron・パラメータ・run・有効化は無い。作るとすぐ表示され、いつでも編集・削除できる。終了日を過ぎた予定は薄く表示する |

### スケジュールの状態

- `draft`：作成直後。run は表示されるが **実行しない**（タイムラインでは斜線・破線）
- `active`：**有効化は明示操作で、承認を兼ねます**。有効化時に Jenkins からパラメータ定義を取り直し、エラーがあれば有効化できません
- `paused`：一時停止（未実行の run を実行しない）。再開すると、停止中に過ぎた run は遅延時の扱いに従います
- `ended`：終了日を過ぎると自動で遷移。`cancelled`：キャンセル（未実行の run もキャンセル）

### パラメータの変数

`{{schedule.title}}`（スケジュールのタイトル。以前の書き方 `{{schedule.label}}` も可） `{{schedule.start_date}}` `{{schedule.end_date}}` `{{run.scheduled_at}}` `{{run.date}}` `{{target.job_path}}`

例：`VERSION = {{schedule.title}}-{{run.date}}` → `v2.3.0-2026-10-05`。日時は Asia/Tokyo（`{{run.scheduled_at}}` は `2026-10-05 03:00` の形式）。上書きした項目だけを保存し、それ以外は Jenkins のデフォルト値を使います。「JenkinsJobパラメータのデフォルト値変更に追従しない」をオンにすると、有効化した時点（以降はパラメータを保存した時点）のデフォルト値で実行し続けます。

### パラメータ定義の変更（Jenkins 側）

| 変化 | 判定 | 扱い |
|---|---|---|
| パラメータ追加 | 警告 | Jenkins のデフォルトで補完。パラメータを保存すると「確認済み」になる |
| パラメータ削除 | 警告 | 上書き値は残すが送信しない |
| デフォルト値変更 | 情報 | 固定していなければ新デフォルトに追従 |
| 選択肢変更で上書き値が選択肢外 | **エラー** | キックせず `holding` |
| 型変更 | **エラー** | キックせず `holding` |
| ジョブ無し・`buildable=false` | **エラー** | キックせず `holding` |

---

## 3. 構成

```
app/
  __main__.py        python -m app（単一プロセスの Uvicorn。多重起動ロック）
  main.py            FastAPI、定期処理の登録（dispatcher 30秒 / スキーマ取得 5分 / 日次補充 / バックアップ）
  config.py          設定（.env と環境変数、UTF-8）
  models.py, db.py   SQLAlchemy 2.x（SQLite WAL。PostgreSQL へ切替可能な書き方）
  seed.py            seed.toml の冪等な読み込み
  jenkins/           client.py（実 Jenkins）, mock.py（モック）
  schema/            normalize（正規化・SHA-256）, diff（差分分類）, validate（展開・検証）, service
  scheduler/         planner（run 生成）, dispatcher（状態機械）, poller（スキーマ・cron 残存）, cron（cron 式の解釈）, jobs（定期処理の実行）
  api/               ルーター
static/              素の HTML/JS（ビルド工程なし）。vis-timeline 8.5.4 を vendor/ に同梱
alembic/             マイグレーション
deploy/winsw/        WinSW のサービス定義
scripts/             install / uninstall / dev / download-wheels（PowerShell）
tests/               pytest（respx で Jenkins API をモック）
```

### run の流れ

```
scheduled ──(時刻到来)──▶ 遅延判定 ──▶ キック直前検証 ──▶ 重複チェック ──▶ キック ──▶ queued ──▶ running ──▶ success / unstable / failure / aborted
                          │              │                 │                 │
                          ▼              ▼                 ▼                 ▼
                        missed         holding           skipped           holding（POST 失敗。自動再試行しない）
```

- cron は直近14日分を先行生成し、tick と日次ジョブで補充します（予定の正は DB）
- 1回の tick 内でブロッキング待ちはしません（run ごとの状態を tick で1段ずつ進める）
- 起動時、`queued` / `running` の run は追跡を再開し、**再キックしません**
- 停止中に過ぎた run は遅延判定（`missed_policy` / `grace_minutes`）に従い、停止期間の件数と扱いを INFO ログと監査ログ（`startup_gap`）に記録します

### 排他と同時編集

- **二重キックの防止**：キックする前に、DB の条件付き更新で run を「確保」します（未実行で、まだ誰も確保していない run だけを確保できる）。確保できた処理だけがキックします。
  - 「今すぐ実行」「再実行」の run は作った時点で確保済みにし、「保留解除」は保留から確保済みへ1回の更新で切り替えるので、dispatcher と同時に動いても二重になりません
  - スキップ・キャンセル・期間変更による作り直しは、確保済み（キック処理中）の run には触りません。キック処理中の run をスキップしようとすると 409 になります
  - 確保したまま止まった run（キックの途中でプロセスが落ちた等）は、Jenkins に送ったか分からないため、起動時に保留にします
- **同時編集の検出**：スケジュールとアイテムは更新番号（`revision`）を持ちます。保存時に読み込んだ時点の番号を送り、他の人が先に保存していたら 409 で保存を止め、画面は最新の内容を読み込み直します（後から保存した人の内容で黙って上書きされることはありません）
- 画面は15秒ごとに最新の状態を読み直します（変更の即時配信はしていません）

### 遅延判定

| `missed_policy` | 予定時刻から1分以内 | 猶予（`grace_minutes`）以内 | 猶予超過 |
|---|---|---|---|
| `run_late`（既定） | 実行 | 実行 | missed |
| `skip` | 実行 | missed | missed |

---

## 4. 設定（.env）

`.env.example` をコピーして `.env` を作成します（**UTF-8 で保存**。日本語を含めても可）。主な項目：

| 変数 | 既定 | 説明 |
|---|---|---|
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8080` | 他端末から使う場合は `0.0.0.0` にし、**Basic 認証と TLS を必ず有効に** |
| `APP_DATA_DIR` | `%ProgramData%\jenkins-scheduler` | 配下に `data\`（SQLite・ロック）と `logs\` |
| `APP_BASIC_AUTH_USER` / `APP_BASIC_AUTH_PASSWORD` | なし | 両方設定で Basic 認証。監査ログの実行者に記録 |
| `APP_AUTH_EXEMPT_MONITORING` | `true` | `/metrics` と `/api/health` を認証対象外にする |
| `APP_TLS_CERT` / `APP_TLS_KEY` | なし | 指定すると HTTPS で待ち受け |
| `JENKINS_URL` / `JENKINS_USER` / `JENKINS_TOKEN` | | API トークンで Basic 認証 |
| `JENKINS_TOKEN_SOURCE` | `env` | `wincred` で Windows 資格情報マネージャーから読む（以前の `keyring` も可） |
| `JENKINS_CA_BUNDLE` | なし | 社内 CA の PEM。未指定なら OS の証明書（Windows では証明書ストア）を使う |
| `JENKINS_MOCK` | `false` | `true` でモック |
| `RUN_HORIZON_DAYS` | `14` | cron の先行生成日数 |
| `SCHEMA_POLL_MINUTES` | `5` | スキーマ・cron 残存のポーリング間隔 |
| `DEFAULT_OVERLAP_POLICY` | `skip` | 前回ビルド実行中/キュー中のときの既定 |
| `DEFAULT_MISSED_POLICY` / `DEFAULT_GRACE_MINUTES` | `run_late` / `10` | 遅延時の既定 |
| `SEED_FILE` | なし | 起動時に読み込む seed.toml（初期データ。書き方は `seed.toml.example`） |
| `BACKUP_ENABLED` / `BACKUP_TIME` / `BACKUP_KEEP` / `BACKUP_DIR` | `true` / `01:30` / `14` / `<データ>\backups` | 自動バックアップ（5.8） |

### トークンを資格情報マネージャーに置く（任意）

Windows 標準の `cmdkey` で登録します（`/pass` の後ろを空にすると、トークンを画面に出さずに入力できます）。

```powershell
cmdkey /generic:jenkins-scheduler /user:scheduler-bot /pass
```

`.env` に `JENKINS_TOKEN_SOURCE=wincred` を設定し、`JENKINS_TOKEN` は空にします。`/generic:` の名前は `JENKINS_KEYRING_SERVICE`（既定 `jenkins-scheduler`）、`/user:` は `JENKINS_USER` に合わせます。**サービス実行アカウントでログオンして登録してください**（資格情報はユーザーごと）。以前の版で keyring を使って登録したトークンも、そのまま読めます。

### アプリ用 Jenkins アカウントの権限

Overall/Read、Job/Read、Job/Build のみ。**Job/Configure は付与しない**。

---

## 5. Windows へのインストールとサービス化

前提：Windows 10/11 または Windows Server 2019 以降、Python 3.11 以上（`py` ランチャー）。

### 5.0 インストーラで入れる（おすすめ）

GitHub の Releases から `JenkinsScheduler-Setup-<バージョン>.exe` をダウンロードし、管理者として実行します。

- Python を同梱しているので、インストール先に Python は不要です（オフラインの PC にもそのまま入れられます）
- 途中で管理者パスワード（12文字以上）と待ち受けポート（既定 8090）を入力します。完了すると Windows サービスとして起動します
- スタートメニューに「Jenkins Scheduler を開く」「設定ファイル（.env）を開く」「管理者パスワードを変更」「サービスを再起動（設定の反映）」を作ります
- Jenkins の URL・ユーザー・API トークンは、インストール後に `.env` に書いて、サービスを再起動します
- 上書きインストールで更新できます（設定とデータは引き継ぎます）。アンインストールしても設定（.env）とデータ（`%ProgramData%\jenkins-scheduler`）は残ります
- 署名していないため、初回は SmartScreen の警告が出ることがあります
- 自動化用のサイレントインストール：`JenkinsScheduler-Setup-x.y.z.exe /VERYSILENT /ADMINPASSWORD=<パスワード> /PORT=8090`

インストーラは GitHub Actions（`.github/workflows/windows-installer.yml`）が Windows 上で作り、実際にインストールして、サービスの起動・管理者ログイン・二重起動の防止・再起動後の復帰・アンインストールまで確かめてから Releases に載せます。`v` で始まるタグ（例：`v0.1.0`）を送ると動きます。

以下の 5.1〜5.2 は、インストーラを使わずにソースから入れる方法です。

### 5.1 オフライン用の事前取得（インターネットに出られる端末）

```powershell
.\scripts\download-wheels.ps1
```

`wheels\`（依存ホイール）と `deploy\winsw\WinSW-x64.exe` が作られます。リポジトリごとオフライン端末に持ち込みます。

### 5.2 インストール（管理者 PowerShell）

```powershell
# 専用のローカルアカウントを作っておく（例: svc-scheduler。「サービスとしてログオン」権限のみ付与）
.\scripts\install.ps1 -ServiceAccount ".\svc-scheduler"
```

`install.ps1` は venv 作成 → `wheels\` からの依存インストール → `.env` 作成と権限設定 → DB マイグレーション → WinSW でサービス登録（自動・遅延開始、異常終了時は10秒後に再起動×3回、その後60秒間隔）→ 実行アカウント設定 → 起動 を行います。

- サービスは `python.exe -m app` を **単一プロセス** で起動します。`--workers` を増やしたり、複数台・複数サービスで同じ DB を使ったりしないでください（二重キックになります）
- `data\scheduler.lock` を排他ロックするので、サービス起動中にコンソールから `python -m app` を実行するとエラー（終了コード 2）で終了します
- 停止時は Uvicorn にシャットダウンを伝え、dispatcher の tick 完了を待ってから終了します（最大20秒）
- アンインストール：`.\scripts\uninstall.ps1`（データは残ります）

### 5.3 .env のアクセス権

`install.ps1` が自動で設定しますが、手動で行う場合：

```powershell
icacls .env /inheritance:r /grant:r "Administrators:(F)" "SYSTEM:(F)" ".\svc-scheduler:(R)"
```

### 5.4 時刻と電源（定時キックの前提）

- **スリープ・休止状態を無効にする**（スリープ中の run は missed になります）

  ```powershell
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  powercfg /hibernate off
  ```

- 時刻同期：`w32tm /query /status` で社内 NTP に同期していることを確認
- Windows Update：アクティブ時間・再起動時刻を定期ビルドの時間帯と重ならないように設定。再起動中に過ぎた run は `missed_policy` に従います

### 5.5 ファイアウォール（他端末・Prometheus から使う場合）

送信元は Prometheus サーバーと利用者のセグメントに限定します。

```powershell
New-NetFirewallRule -DisplayName "Jenkins Scheduler" -Direction Inbound -Protocol TCP -LocalPort 8080 `
  -RemoteAddress 10.0.10.0/24,10.0.20.15 -Action Allow -Profile Domain
```

### 5.6 TLS

`APP_TLS_CERT` / `APP_TLS_KEY` に社内 CA で発行した証明書・秘密鍵（PEM）を指定すると HTTPS で待ち受けます。Jenkins が社内 CA を使っている場合は Windows の証明書ストアを自動で信頼します（`JENKINS_CA_BUNDLE` で PEM を直接指定も可）。プロキシは `HTTPS_PROXY` / `NO_PROXY` を尊重します（Jenkins は通常 `NO_PROXY` に入れる）。

### 5.7 その他の推奨

- ウイルス対策ソフトのスキャン除外に `%ProgramData%\jenkins-scheduler\data\` を追加（SQLite WAL の性能と破損防止）
- ログは `logs\scheduler.log`（JSON Lines、日次ローテーション14世代）。Windows 版 Promtail / Grafana Alloy で Loki に送れます

---

### 5.8 バックアップと復元

毎日 `BACKUP_TIME`（既定 01:30）に自動でバックアップを取り、`BACKUP_KEEP`（既定14）世代を残します。保存先は `.env` の `BACKUP_DIR` に書きます（空にすると `%ProgramData%\jenkins-scheduler\backups\`。相対パスはツールのフォルダが基準）。変更後はツールの再起動が必要です。

| ファイル | 内容 | 用途 |
|---|---|---|
| `scheduler-<日時>.db` | DB 全体のスナップショット（アイテム・スケジュール・パラメータ・run 履歴・ログ） | 復元 |
| `settings-<日時>.json` | アイテム・カテゴリ・スケジュール・パラメータ上書き値の書き出し | 内容の確認・差分の比較 |

- 稼働中でも一貫したコピーを取ります（SQLite のオンラインバックアップ）
- `.env`（Jenkins のトークンを含む）はバックアップしません。別途、安全な場所に保管してください
- 同じディスクに置くとディスク故障で一緒に失われます。`BACKUP_DIR` に別ドライブか共有フォルダを指定するのを推奨します（サービス実行アカウントに書き込み権限が必要）
- 手動で取る場合：アイテム画面の「今すぐバックアップ」、または `.\.venv\Scripts\python.exe -m app --backup`

**復元（リストア）**

管理者でログインし、アイテム画面の「バックアップ」欄で、戻したい `.db` の行の「この時点に戻す」を押します（管理者以外には表示されず、サーバーでも拒否します）。

- 戻す直前に今の状態を自動でバックアップするので、間違えて戻してもそこから戻せます
- 古い版で取ったバックアップでも、戻したあとに DB の作りを今の版へ自動で合わせます
- 戻している管理者のログイン状態は引き継ぎます。戻している間は定時キックの処理を止めます

サービスが起動しないなどで画面が使えないときは、サービスを止めてからコマンドで戻します（動いている間は拒否します）。

```powershell
Stop-Service jenkins-scheduler
& "C:\Program Files\Jenkins Scheduler\python\python.exe" -m app --restore "C:\ProgramData\jenkins-scheduler\backups\scheduler-20261003-013000.db"
Start-Service jenkins-scheduler
```

復元すると、バックアップ時点以降に過ぎた予定の run は遅延時の扱い（`missed_policy`）に従います。

### 5.9 管理者アカウント（ログインの切り替え）

`AUTH_MODE=shared_admin`（`ADMIN_PASSWORD_HASH` を設定すると既定でこれになる）では、次の2段階になります。

| 状態 | できること |
|---|---|
| ログインなし（利用者） | すべて閲覧できる。予定のアイテム（行）と、そこに書いた予定、カテゴリの追加・変更・削除ができる。Jenkins アイテムは並び替えのみ |
| 管理者でログイン | すべての操作（Jenkins のスケジュール・アイテム・実行・バックアップなど） |

- 管理者アカウントは1つで、Jenkins を操作する人だけで共有します。画面右上の「管理者ログイン」で切り替え、「ログアウト」で利用者に戻ります
- パスワードはハッシュで `.env` に置きます。次のコマンドで作った1行を `.env` に書き、ツールを再起動します

  ```powershell
  .\.venv\Scripts\python.exe -m app --hash-password
  ```

- 共有アカウントなので、ログ画面の実行者は「admin」になります（誰が操作したかまでは分かりません）。ログインしていない人の操作は「guest@接続元IP」で記録します
- パスワードを知っている人が異動したら、作り直して `.env` を書き換えてください（ログイン中の人は `SESSION_HOURS` 後に切れます。すぐ切りたい場合はツールを再起動してから DB の `user_session` を空にする）

### 5.10 Active Directory でのログインと権限（任意）

`AUTH_MODE=ldap` にすると、ログイン画面で AD のユーザー名・パスワードを入力してログインします。権限は AD グループ（メーリングリストのアドレスでも指定可）で分けます。

| 権限 | AD グループ（.env） | できること |
|---|---|---|
| 1. フルコントロール | `LDAP_ADMIN_GROUPS` | すべての操作 |
| 2. 予定のみ編集 | `LDAP_MEMO_EDITOR_GROUPS` | 予定のアイテム（行）と、そこに書いた予定、カテゴリの追加・変更・削除。Jenkins アイテムは並び替えのみ |
| 3. 読み取り専用 | `LDAP_VIEWER_GROUPS` | 閲覧のみ |

- 本人の資格情報で LDAPS にバインドして確かめます（ツール用のサービスアカウントは不要）。空のパスワードは必ず拒否します
- 入れ子のグループにも対応します（`LDAP_NESTED_GROUPS=true`）。どのグループにも入っていない人はログインできません
- 権限はログインした時点の AD グループで決まります。グループを変えたら、その人に一度ログアウトしてもらってください（または `SESSION_HOURS` の経過を待つ）
- ブラウザにはランダムなトークンだけを Cookie（HttpOnly・SameSite=Strict、HTTPS では Secure）で渡し、DB にはそのハッシュを置きます。パスワードは保存しません
- 同じユーザー名でログインに5回失敗すると、5分間受け付けません（`LOGIN_MAX_FAILURES` / `LOGIN_LOCK_MINUTES`）。AD 自体のアカウントロックのポリシーも働きます
- 権限のチェックはサーバーで行います（画面のボタンを隠すだけではありません）。フルコントロール以外は、許可した変更操作以外を一律で拒否します
- 変更系の API には `X-Requested-With: jenkins-scheduler` ヘッダーが必要です（CSRF 対策）。スクリプトから API を呼ぶ場合も付けてください
- ログイン・ログアウト・ログイン失敗はログ画面に記録されます。操作の実行者は AD のアカウント名になります
- 開発時は `AUTH_MODE=mock` と `AUTH_MOCK_USERS=admin:パスワード:admin:表示名,...` で、AD 無しで権限ごとの動きを試せます（本番では使わない）
- 使う場合は `pip install -r requirements-ldap.txt` で `ldap3`（**LGPL-3.0**）と `pyasn1`（BSD）を追加で入れます。標準の構成には含めていません（ライセンス審査が必要なため）

## 6. 実 Jenkins との疎通確認

1. `.env` に `JENKINS_URL` / `JENKINS_USER` / `JENKINS_TOKEN` を設定し、`JENKINS_MOCK=false`
2. 疎通：

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8080/api/health
   # jenkins: ok, dispatcher: ok になること
   ```

3. `/targets` でジョブを検索して登録（存在とパラメータ定義を確認します）
4. タイムラインでテスト用ジョブのスケジュールを作り、詳細パネルの **ドライラン** で展開後のパラメータと `buildWithParameters` / `build` のどちらを使うかを確認
5. 「今すぐ実行」でキックし、実行履歴にビルド番号・リンク・結果が反映されることを確認
6. ログ画面（`/audit`）に操作が記録されていることを確認

トラブル時：`logs\scheduler.log` と `/api/health` の `jenkins` の内容を確認します。HTTP 403 の場合は crumb を取得して1回だけやり直します。証明書エラーは `JENKINS_CA_BUNDLE` を指定してください。

---

## 7. Jenkins cron からの移行手順

ジョブごとに次の順で行います。**cron の削除と有効化の間に二重実行・実行漏れが起きないよう、切り替えのタイミングを揃えます。**

1. ツールにアイテムを登録する
2. Jenkins の cron と同等のスケジュールを **draft** で作成する
   - `H` は具体的な分に置き換える（例：`H 3 * * *` → `17 3 * * *`）。このツールは `H` 記法を受け付けません
   - 次回予定のプレビューで時刻を確認する（Asia/Tokyo で評価）
3. 切り替え時刻を決める（次の定期ビルドの直前を避ける）
4. Jenkins 側の cron を削除する
   - フリースタイル：「定期的に実行」のチェックを外す
   - Pipeline：Jenkinsfile の `triggers { cron(...) }` を削除してコミットする。**`triggers` の変更はそのジョブが一度ビルドされるまで Jenkins に反映されません**。削除後に1回ビルドする（または手動実行の機会に合わせる）
5. ツールでスケジュールを **有効化** する
6. 数日間、⏰（Jenkins 側の cron 残存）警告が出ないこと、run が予定どおり実行されていることを確認する
   - 残存検出は直近7日（`TIMER_TRIGGER_LOOKBACK_DAYS`）のビルドの起動理由（`TimerTrigger`）で判定します。移行直前のビルドで警告が出る場合は、7日経過で消えるか確認してください

---

## 8. 監視とアラート

`/metrics`（Prometheus 形式）

| メトリクス | 種類 | 内容 |
|---|---|---|
| `jenkins_scheduler_dispatcher_last_tick_timestamp_seconds` | gauge | dispatcher の最終 tick |
| `jenkins_scheduler_runs_total{target,status}` | counter | run の状態遷移 |
| `jenkins_scheduler_runs_holding` | gauge | holding の run 数 |
| `jenkins_scheduler_runs_missed_total{target}` | counter | missed の run 数 |
| `jenkins_scheduler_jenkins_api_errors_total{kind}` | counter | Jenkins API エラー |
| `jenkins_scheduler_schema_drift{target,level}` | gauge | 未実行 run を持つスケジュールの警告/エラー件数 |
| `jenkins_scheduler_timer_trigger_detected{target}` | gauge | Jenkins 側 cron の残存（1=検出） |
| `jenkins_scheduler_backup_last_success_timestamp_seconds` | gauge | 最後にバックアップに成功した時刻 |
| `jenkins_scheduler_backup_failures_total` | counter | バックアップの失敗回数 |

### アラート例（Prometheus ルール）

```yaml
groups:
  - name: jenkins-scheduler
    rules:
      # 最重要: ツールが止まると定期ビルドが全停止する
      - alert: JenkinsSchedulerDispatcherStalled
        expr: time() - jenkins_scheduler_dispatcher_last_tick_timestamp_seconds > 300
        for: 1m
        labels: {severity: critical}
        annotations: {summary: "Jenkins Scheduler の dispatcher が5分以上止まっています"}
      - alert: JenkinsSchedulerDown
        expr: up{job="jenkins-scheduler"} == 0 or windows_service_state{name="jenkins-scheduler",state="running"} == 0
        for: 2m
        labels: {severity: critical}
      - alert: JenkinsSchedulerRunMissed
        expr: increase(jenkins_scheduler_runs_missed_total[1h]) > 0
        labels: {severity: warning}
        annotations: {summary: "{{ $labels.target }} の run が見逃されました"}
      - alert: JenkinsSchedulerRunHolding
        expr: jenkins_scheduler_runs_holding > 0
        for: 30m
        labels: {severity: warning}
        annotations: {summary: "保留（holding）の run が30分以上残っています"}
      - alert: JenkinsSchedulerSchemaError
        expr: jenkins_scheduler_schema_drift{level="error"} > 0
        for: 10m
        labels: {severity: warning}
      - alert: JenkinsSchedulerBackupStale
        expr: time() - jenkins_scheduler_backup_last_success_timestamp_seconds > 2 * 86400 or increase(jenkins_scheduler_backup_failures_total[1d]) > 0
        labels: {severity: warning}
        annotations: {summary: "Jenkins Scheduler のバックアップが2日以上取れていません"}
      - alert: JenkinsSchedulerJenkinsCronRemains
        expr: jenkins_scheduler_timer_trigger_detected > 0
        labels: {severity: warning}
        annotations: {summary: "{{ $labels.target }} に Jenkins 側の cron が残っています（二重実行の恐れ）"}
```

`windows_service_state`（windows_exporter）と dispatcher の最終 tick を組み合わせ、「サービスは動いているが tick が止まっている」状態も検知します。`/api/health` は dispatcher の最終 tick が5分以上前だと HTTP 503 を返します。

---

## 9. 運用上の注意

- **Jenkins はキュー内の同一パラメータのビルド要求を1つにまとめることがあります。** `overlap_policy=queue` でも、前のビルドがキューに残っている間に同じパラメータでキックすると吸収される（新しいビルドにならない）可能性があります。Jenkins が既存のキューアイテムの Location を返した場合、run はそのアイテム（＝前回と同じビルド）を追跡します
- キック（POST）は自動リトライしません（二重起動防止）。失敗した run は `holding` になるので、原因を確認して「保留解除」してください
- 1ジョブ＝1行のため、ジョブ間の順序制御は Jenkinsfile 側（起点 Pipeline）で行ってください
- 画面はブラウザのローカル時刻で表示します。利用端末のタイムゾーンは Asia/Tokyo を前提としています（cron の評価・DB 保存はサーバー側で Asia/Tokyo / UTC）
- 対象外：ジョブ間シーケンス制御、Jenkins ジョブ設定の変更、Active Choices 等の動的パラメータの完全対応（取得できた範囲で表示し、それ以外は自由入力）、複数ユーザーの権限管理、通知、祝日の自動スキップ

### 未確定事項の既定値

| 項目 | 既定 | 変更箇所 |
|---|---|---|
| 前回ビルド実行中の動作 | `skip` | `DEFAULT_OVERLAP_POLICY`、アイテムごとに変更可 |
| 遅延時の動作 | `run_late` / 10分 | `DEFAULT_MISSED_POLICY` / `DEFAULT_GRACE_MINUTES`、スケジュールごとに変更可 |
| 先行生成日数 | 14日 | `RUN_HORIZON_DAYS` |

---

## 10. 開発・テスト

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\pytest -q
```

- Jenkins API は respx でモック、時刻依存は Dispatcher/planner に `now` を渡して固定しています
- 主なテスト：スキーマ正規化・ハッシュの安定性、差分分類の全パターン、cron の Asia/Tokyo 評価・期間境界・無期限の先行生成・変更時の再生成、dispatcher（holding / missed / 重複 / 状態遷移 / 再起動時の再開 / キック失敗時に再試行しない）、`/build` と `/buildWithParameters` の使い分け、Location ヘッダのパース、キャンセルされたキューアイテム、TimerTrigger 検出、UTF-8 の seed.toml・.env 読み込み、ロックファイルによる二重起動検出、cron 式の解釈（範囲・間隔・英字名・日と曜日の組み合わせ）、定期処理の実行
- マイグレーションの追加：モデルを変更したら

  ```powershell
  .\.venv\Scripts\alembic revision --autogenerate -m "変更内容"
  ```

---

## 11. 動作環境と使用ライブラリ

### 動作環境

| 項目 | 内容 |
|---|---|
| OS（本番） | Windows 10 / 11、Windows Server 2019 以降（Windows サービスとして常駐） |
| OS（開発・デモ） | macOS / Linux でも動作（デモサイトは Docker の `python:3.13-slim`） |
| Python | 3.11 以上（インストーラには 3.13 の埋め込み版を同梱するので、別途のインストールは不要） |
| データベース | SQLite（Python に標準で含まれる。別途のインストールは不要） |
| ブラウザ | Microsoft Edge / Google Chrome / Firefox などの最新版 |
| Jenkins | REST API（`/api/json`、`/buildWithParameters`）が使えること。アプリ用アカウントの API トークン |
| ネットワーク | このツールから Jenkins へ HTTP(S) で接続できること。利用者のブラウザからこのツールのポート（既定 8080）へ接続できること |

### 外部ライブラリを最小限にしている理由

社内のセキュリティ審査の手間を減らすため、Python の標準ライブラリで実装できるものは外部ライブラリを使っていません。外部ライブラリは、Web サーバー・データベース・HTTP 通信という、自分で作るとかえって危険な土台の部分だけです。

| 標準ライブラリなどで置き換えたもの | 置き換え先 |
|---|---|
| cron 式の計算（croniter） | `app/scheduler/cron.py`（自前の実装。croniter と同じ結果になることを確認済み） |
| 定期処理（APScheduler） | `app/scheduler/jobs.py`（標準の `threading`） |
| 監視用メトリクス（prometheus-client） | `app/metrics.py`（Prometheus のテキスト形式を自前で出力） |
| `.env` の読み込み（pydantic-settings） | `app/envfile.py`・`app/config.py` |
| パラメータの変数の展開（Jinja2） | `app/schema/validate.py`（変数の置き換えだけ。式やコードは実行しない） |
| 初期データの読み込み（PyYAML） | 標準の `tomllib`（初期データは `seed.toml`） |
| 二重起動の防止（portalocker） | 標準の `msvcrt`（Windows）／`fcntl` |
| 資格情報マネージャー（keyring） | `app/wincred.py`（標準の `ctypes` で Windows の API を呼ぶ。登録は Windows 標準の `cmdkey`） |
| 社内 CA の証明書（truststore） | 標準の `ssl`（Windows では証明書ストアを読み込む） |
| タイムゾーンのデータ（tzdata） | 日本時間は固定の +09:00 で扱う（日本には夏時間が無いので結果は同じ） |

### 実行に使うライブラリ（`requirements.txt`）

バージョンは動作確認した時点のものです。`requirements.txt` には下限だけを書いているので、インストールした時期によって新しい版が入ります。

| ライブラリ | 確認した版 | ライセンス | 概要（このツールでの用途） |
|---|---|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) | 0.142.2 | MIT | Web API のフレームワーク。画面から呼ぶ API をすべてこれで作っている |
| [Uvicorn](https://www.uvicorn.org/) | 0.54.0 | BSD-3-Clause | FastAPI を動かす Web サーバー（ASGI サーバー） |
| [SQLAlchemy](https://www.sqlalchemy.org/) | 2.1.3 | MIT | データベース（SQLite）を Python から扱うためのライブラリ（ORM） |
| [Alembic](https://alembic.sqlalchemy.org/) | 1.20.0 | MIT | データベースの表の構造をバージョンアップに合わせて更新する（マイグレーション）。SQLAlchemy と同じ作者 |
| [HTTPX](https://www.python-httpx.org/) | 0.28.1 | BSD-3-Clause | Jenkins の REST API を呼ぶための HTTP クライアント |

#### 上のライブラリが内部で使うライブラリ（自動でインストールされる）

| ライブラリ | ライセンス | 概要 |
|---|---|---|
| [Starlette](https://www.starlette.io/) | BSD-3-Clause | FastAPI の土台の Web フレームワーク |
| [Pydantic](https://docs.pydantic.dev/) / [pydantic-core](https://github.com/pydantic/pydantic-core) | MIT | FastAPI が使うデータの型チェックと変換（このツールでは設定の読み込みにも使う） |
| [AnyIO](https://github.com/agronholm/anyio) | MIT | 非同期処理の共通基盤 |
| [httpcore](https://github.com/encode/httpcore) / [h11](https://github.com/python-hyper/h11) | BSD-3-Clause / MIT | HTTPX・Uvicorn が使う HTTP 通信の下回り |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause | 国際化ドメイン名の処理 |
| [certifi](https://github.com/certifi/python-certifi) | MPL-2.0 | Mozilla の CA 証明書の一覧（HTTPX が依存。このツールは OS の証明書を使うよう指定している） |
| [click](https://click.palletsprojects.com/) | BSD-3-Clause | Uvicorn・Alembic のコマンドライン処理 |
| [Mako](https://www.makotemplates.org/) / [MarkupSafe](https://github.com/pallets/markupsafe) | MIT / BSD-3-Clause | Alembic がマイグレーションファイルのひな形を作るのに使う |
| [opentelemetry-api](https://github.com/open-telemetry/opentelemetry-python) | Apache-2.0 | FastAPI が依存する計測用 API（このツールでは計測を有効にしていない） |
| [typing-extensions](https://github.com/python/typing_extensions) / [typing-inspection](https://github.com/pydantic/typing-inspection) / [annotated-types](https://github.com/annotated-types/annotated-types) / [annotated-doc](https://github.com/fastapi/annotated-doc) | PSF-2.0 / MIT | 型ヒントの補助 |

合計 21 個（直接 5 個 + 内部 16 個）です。正確な一覧は、インストールした環境で `pip list` で確認できます。

### 任意のライブラリ（`requirements-ldap.txt`、AD 認証を使うときだけ）

| ライブラリ | 確認した版 | ライセンス | 概要 |
|---|---|---|---|
| [ldap3](https://github.com/cannatag/ldap3) | 2.9.1 | LGPL-3.0 | Active Directory（LDAP）でのログインと、グループによる権限の判定 |
| [pyasn1](https://github.com/pyasn1/pyasn1) | 0.6.4 | BSD-2-Clause | ldap3 が使う LDAP の通信データの変換 |

標準の構成（共有の管理者アカウント）では使いません。

### 画面（ブラウザ）で使うライブラリ

| ライブラリ | 版 | ライセンス | 概要 |
|---|---|---|---|
| [vis-timeline](https://visjs.github.io/vis-timeline/) | 8.5.4 | Apache-2.0 または MIT | タイムライン（横表示）の描画、ドラッグでの作成・移動、拡大・縮小 |

`static/vendor/vis-timeline/` に同梱しているので、インターネットにつながらない環境でも動きます。ほかの画面部分はライブラリを使わない素の HTML / CSS / JavaScript で、ビルドの工程はありません（日付の選択もブラウザ標準のカレンダーを使っています）。

### テストに使うライブラリ（`requirements-dev.txt`、開発するときだけ）

| ライブラリ | 確認した版 | ライセンス | 概要 |
|---|---|---|---|
| [pytest](https://docs.pytest.org/) | 9.1.1 | MIT | テストの実行 |
| [RESPX](https://lundberg.github.io/respx/) | 0.23.1 | BSD-3-Clause | HTTPX の通信を差し替え、本物の Jenkins なしで Jenkins API のテストをする |

配布物（インストーラ）には含まれません。

### インストーラ・配布の作成に使うツール

| ツール | ライセンス | 概要 |
|---|---|---|
| [Python 埋め込み版（embeddable package）](https://www.python.org/downloads/windows/) | PSF-2.0 | インストーラに同梱する Python 本体（3.13） |
| [WinSW](https://github.com/winsw/winsw) v2.12.0 | MIT | Python のプログラムを Windows サービスとして動かす |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | Inno Setup License（無償） | Windows のインストーラ（Setup.exe）を作る。作成時だけ使い、配布物には含まれない |
| [GitHub Actions](https://docs.github.com/actions) | — | テスト、インストーラの作成、GitHub Releases への公開を自動で行う |
| [Docker](https://www.docker.com/) / [Render](https://render.com/) | — | デモサイトの実行環境（本番の社内運用では使わない） |

---

## ライセンス

[MIT License](LICENSE)

同梱しているサードパーティのソフトウェアは、それぞれのライセンスに従います。

| ソフトウェア | 場所 | ライセンス |
|---|---|---|
| vis-timeline 8.5.4（moment.js・Hammer.JS を含む） | `static/vendor/vis-timeline/` | Apache-2.0 または MIT（[LICENSE.md](static/vendor/vis-timeline/LICENSE.md)） |

リポジトリには Python の依存ライブラリ（`requirements.txt`）を入れておらず、インストール時に取得します。

Windows 用の配布物（Setup.exe・zip 版）には、Python 本体（埋め込み版）、依存ライブラリ、WinSW を同梱しています。同梱物とライセンスの一覧は [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)、WinSW のライセンスの全文は [licenses/WinSW-LICENSE.txt](licenses/WinSW-LICENSE.txt) にあり、どちらも配布物に入れています。

AD 認証用の `ldap3`（LGPL-3.0）は任意で、配布物には含めていません。
