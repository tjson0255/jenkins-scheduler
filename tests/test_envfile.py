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
