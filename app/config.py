"""アプリケーション設定（.env / 環境変数）。"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(base) / "jenkins-scheduler"
    return PROJECT_ROOT / "var"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- アプリ ---
    app_host: str = "127.0.0.1"
    app_port: int = 8080
    app_data_dir: Path = _default_data_dir()
    app_tz: str = "Asia/Tokyo"
    app_basic_auth_user: str | None = None
    app_basic_auth_password: str | None = None
    app_auth_exempt_monitoring: bool = True  # /metrics と /api/health を認証対象外にする

    # --- 認証（AUTH_MODE） ---
    #   shared_admin : ログインなしで閲覧と自由記入の編集ができ、共有の管理者アカウントでログインすると全操作できる
    #   none         : 認証なし（127.0.0.1 での開発用。全員フルコントロール）
    #   basic        : APP_BASIC_AUTH_USER / PASSWORD の共有アカウント（フルコントロール）
    #   ldap         : Active Directory でログインし、AD グループで権限を分ける（別途 ldap3 が必要）
    #   mock         : 開発用。AUTH_MOCK_USERS の利用者でログインできる（本番では使わない）
    # 未指定なら、管理者パスワードがあれば shared_admin、Basic 認証の設定があれば basic、どちらも無ければ none
    auth_mode: str | None = None
    # 共有の管理者アカウント（AUTH_MODE=shared_admin）。パスワードは ADMIN_PASSWORD_HASH（推奨）か ADMIN_PASSWORD
    admin_username: str = "admin"
    admin_password_hash: str = ""  # python -m app --hash-password で作る
    admin_password: str = ""  # 平文（ADMIN_PASSWORD_HASH が無いときだけ使う）
    session_hours: int = 10  # ログインの有効時間
    cookie_secure: bool | None = None  # 未指定なら HTTPS（APP_TLS_CERT）のとき Secure 属性を付ける
    login_max_failures: int = 5  # この回数続けて失敗すると、しばらくログインを受け付けない
    login_lock_minutes: int = 5
    auth_mock_users: str = ""  # 例: admin:pass:admin:管理者,memo:pass:memo_editor,view:pass:viewer

    # --- Active Directory（AUTH_MODE=ldap） ---
    ldap_urls: str = ""  # 例: ldaps://dc01.corp.local,ldaps://dc02.corp.local（先頭から順に試す）
    ldap_upn_suffix: str = ""  # 例: corp.local（ユーザー名だけ入力されたら user@corp.local で照合）
    ldap_domain: str = ""  # 例: CORP（UPN の代わりに CORP\user で照合する場合）
    ldap_base_dn: str = ""  # 例: DC=corp,DC=local
    ldap_start_tls: bool = False  # ldap:// で StartTLS を使う（ldaps:// なら不要）
    ldap_ca_bundle: Path | None = None  # 社内 CA の PEM（未指定なら OS の証明書ストア）
    ldap_timeout_seconds: int = 10
    ldap_nested_groups: bool = True  # 入れ子のグループの中の人も対象にする
    # 権限ごとの AD グループ。DN・グループ名・メールアドレス（メーリングリスト）のどれでも可。複数はカンマ区切り
    ldap_admin_groups: str = ""  # 1. フルコントロール
    ldap_memo_editor_groups: str = ""  # 2. 自由記入テキストのみ編集可能
    ldap_viewer_groups: str = ""  # 3. 読み取り専用
    app_tls_cert: Path | None = None
    app_tls_key: Path | None = None
    app_lock_enabled: bool = True
    log_level: str = "INFO"
    log_to_file: bool = True

    database_url: str | None = None  # 未指定なら <data>/data/scheduler.db
    seed_file: Path | None = None

    # --- Jenkins ---
    jenkins_url: str = "http://localhost:8080"
    jenkins_user: str = ""
    jenkins_token: str = ""
    jenkins_token_source: str = "env"  # env | keyring
    jenkins_keyring_service: str = "jenkins-scheduler"
    jenkins_ca_bundle: Path | None = None
    jenkins_mock: bool = False
    jenkins_mock_fixtures: Path = PROJECT_ROOT / "tests" / "fixtures" / "jenkins"
    jenkins_mock_min_seconds: float = 3.0
    jenkins_mock_max_seconds: float = 10.0

    # --- スケジューラ ---
    dispatch_interval_seconds: int = 30
    run_horizon_days: int = 14
    catchup_max_days: int = 7  # 停止が長かった場合に遡って run を補完する上限
    schema_poll_minutes: int = 5
    timer_trigger_lookback_days: int = 7

    # --- バックアップ ---
    backup_enabled: bool = True
    backup_time: str = "01:30"  # 毎日この時刻（Asia/Tokyo）に取得
    backup_keep: int = 14  # 残す世代数（.db / .json それぞれ）
    backup_dir: Path | None = None  # 未指定なら <データ>/backups

    # --- 公開デモ（DEMO_MODE=true） ---
    # Jenkins は必ずモックにし、起動時にデモ用データを入れ、DEMO_RESET_HOURS ごとに初期化する
    demo_mode: bool = False
    demo_reset_hours: float = 6

    # --- 既定値 ---
    default_overlap_policy: str = "skip"
    default_missed_policy: str = "run_late"
    default_grace_minutes: int = 10

    @field_validator("backup_dir", "app_tls_cert", "app_tls_key", "jenkins_ca_bundle", "seed_file", "ldap_ca_bundle", mode="before")
    @classmethod
    def _empty_path_is_none(cls, v):
        # .env の `BACKUP_DIR=` のような空の値は「未設定」として扱う（Path("") はカレントフォルダになってしまう）
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def data_path(self) -> Path:
        return Path(self.app_data_dir) / "data"

    @property
    def logs_path(self) -> Path:
        return Path(self.app_data_dir) / "logs"

    @property
    def backup_path(self) -> Path:
        if self.backup_dir is None:
            return Path(self.app_data_dir) / "backups"
        p = Path(self.backup_dir)
        # 相対パスは起動したフォルダではなく、ツールのフォルダ（.env の場所）を基準にする
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def backup_hour_minute(self) -> tuple[int, int]:
        h, _, m = self.backup_time.partition(":")
        hour, minute = int(h), int(m or 0)
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f"BACKUP_TIME が不正です: {self.backup_time}")
        return hour, minute

    @property
    def lock_file(self) -> Path:
        return self.data_path / "scheduler.lock"

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return "sqlite:///" + (self.data_path / "scheduler.db").as_posix()

    @property
    def auth_enabled(self) -> bool:
        return self.effective_auth_mode != "none"

    @property
    def effective_auth_mode(self) -> str:
        if self.auth_mode:
            mode = self.auth_mode.strip().lower()
            if mode not in ("shared_admin", "none", "basic", "ldap", "mock"):
                raise ValueError(f"AUTH_MODE が不正です: {self.auth_mode}")
            return mode
        if self.admin_password_hash or self.admin_password:
            return "shared_admin"
        return "basic" if (self.app_basic_auth_user and self.app_basic_auth_password) else "none"

    @property
    def secure_cookie(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return bool(self.app_tls_cert and self.app_tls_key)

    def resolve_jenkins_token(self) -> str:
        if self.jenkins_token_source == "keyring":
            import keyring

            token = keyring.get_password(self.jenkins_keyring_service, self.jenkins_user)
            if not token:
                raise RuntimeError(
                    f"資格情報マネージャーにトークンがありません (service={self.jenkins_keyring_service}, user={self.jenkins_user})"
                )
            return token
        return self.jenkins_token

    def ensure_dirs(self) -> None:
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
