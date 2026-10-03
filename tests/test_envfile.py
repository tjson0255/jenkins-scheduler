"""インストーラが .env に設定を書き込む処理。"""

from app import envfile
from app.auth.passwords import verify_password
from app.config import Settings


def test_set_values_keeps_comments_and_other_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# コメント\nAPP_PORT=8080\nADMIN_PASSWORD=平文\nJENKINS_URL=https://j\n", encoding="utf-8")
    envfile.set_values(env, {"APP_PORT": "8090", "ADMIN_PASSWORD": None, "ADMIN_PASSWORD_HASH": "pbkdf2_sha256$1$aa$bb"})
    text = env.read_text(encoding="utf-8")
    assert "# コメント" in text and "APP_PORT=8090" in text and "JENKINS_URL=https://j" in text
    assert "ADMIN_PASSWORD=" not in text.replace("ADMIN_PASSWORD_HASH", "")
    assert text.rstrip().endswith("ADMIN_PASSWORD_HASH=pbkdf2_sha256$1$aa$bb")


def test_init_and_password_via_cli(tmp_path, monkeypatch):
    from app import __main__ as entry
    from app import config

    (tmp_path / ".env.example").write_text("AUTH_MODE=shared_admin\nADMIN_PASSWORD_HASH=\nAPP_PORT=8080\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    pw = tmp_path / "pw.txt"
    pw.write_text("long-enough-password\n", encoding="utf-8-sig")  # インストーラは BOM 付きで書く
    assert entry.main(["--init-env", "--set-admin-password-file", str(pw), "--set-env", "APP_PORT=8090"]) == 0
    s = Settings(_env_file=tmp_path / ".env")
    assert s.app_port == 8090 and s.effective_auth_mode == "shared_admin"
    assert verify_password("long-enough-password", s.admin_password_hash)
    short = tmp_path / "short.txt"
    short.write_text("short", encoding="utf-8")
    assert entry.main(["--set-admin-password-file", str(short)]) == 1


def test_read_values_quotes_comments_and_export(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "﻿# コメント\n"
        "APP_PORT = 8090\n"
        "export JENKINS_USER=bot\n"
        "JENKINS_URL=https://j.example # 社内\n"
        "ADMIN_PASSWORD='a #b c '\n"
        'LDAP_BASE_DN="DC=corp,DC=local"\n'
        "JENKINS_TOKEN=\n"
        "not a setting line\n",
        encoding="utf-8",
    )
    assert envfile.read_values(env) == {
        "APP_PORT": "8090", "JENKINS_USER": "bot", "JENKINS_URL": "https://j.example",
        "ADMIN_PASSWORD": "a #b c ", "LDAP_BASE_DN": "DC=corp,DC=local", "JENKINS_TOKEN": "",
    }


def test_settings_precedence_and_empty_values(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("APP_PORT=8090\nLOG_LEVEL=DEBUG\nSESSION_HOURS=\nADMIN_PASSWORD=\n", encoding="utf-8")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    s = Settings(_env_file=env)
    assert s.app_port == 8090  # .env
    assert s.log_level == "WARNING"  # 環境変数が .env より優先
    assert s.session_hours == 10  # 数値の空の値は既定値
    assert s.admin_password == ""
    assert Settings(_env_file=env, app_port=9000).app_port == 9000  # 引数が最優先
    assert Settings(_env_file=None).app_port == 8080
