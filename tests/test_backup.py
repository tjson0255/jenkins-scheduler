"""設定の自動バックアップ。"""

import json
import sqlite3

from app.backup import backup_now, list_backups
from app.models import ParamOverride
from tests.conftest import make_schedule, make_target


def test_backup_creates_db_snapshot_and_json(db, session_factory, settings):
    t = make_target(db, display_name="コア（日本語）")
    s = make_schedule(db, t, label="v1.0.0")
    s.overrides.append(ParamOverride(param_name="VERSION", value_template="{{schedule.label}}"))
    db.commit()

    r = backup_now(settings, session_factory)
    assert len(r["created"]) == 2 and r["items"] == 1 and r["schedules"] == 1

    db_file = settings.backup_path / r["created"][0]
    con = sqlite3.connect(db_file)
    assert con.execute("select display_name from target").fetchone()[0] == "コア（日本語）"
    con.close()

    data = json.loads((settings.backup_path / r["created"][1]).read_text(encoding="utf-8"))
    assert data["items"][0]["display_name"] == "コア（日本語）"
    assert data["schedules"][0]["param_overrides"] == {"VERSION": "{{schedule.label}}"}


def test_backup_keeps_only_n_generations(db, session_factory, settings):
    settings.backup_keep = 3
    for _ in range(5):
        backup_now(settings, session_factory)
    names = [f["name"] for f in list_backups(settings)]
    assert len([n for n in names if n.endswith(".db")]) == 3
    assert len([n for n in names if n.endswith(".json")]) == 3


def test_backup_api_and_audit(app_client):
    r = app_client.post("/api/backups")
    assert r.status_code == 201
    listing = app_client.get("/api/backups").json()
    assert listing["enabled"] and len(listing["files"]) == 2
    actions = [a["action"] for a in app_client.get("/api/audit?type=system").json()]
    assert "backup.create" in actions
    assert "jenkins_scheduler_backup_last_success_timestamp_seconds" in app_client.get("/metrics").text


def test_backup_dir_from_env_file(tmp_path):
    from app.config import PROJECT_ROOT, Settings

    env = tmp_path / ".env"
    env.write_text(f"BACKUP_DIR={tmp_path / 'バックアップ'}\n", encoding="utf-8")
    assert Settings(_env_file=env).backup_path == tmp_path / "バックアップ"
    env.write_text("BACKUP_DIR=var/backups\n", encoding="utf-8")
    assert Settings(_env_file=env).backup_path == PROJECT_ROOT / "var" / "backups"
    env.write_text("BACKUP_DIR=\n", encoding="utf-8")
    s = Settings(_env_file=env, app_data_dir=tmp_path / "data")
    assert s.backup_path == tmp_path / "data" / "backups"


# ---------------------------------------------------------------- リストア
def test_restore_round_trip_keeps_safety_backup(app_client):
    c = app_client
    c.post("/api/categories", json={"name": "バックアップ時点"})
    made = c.post("/api/backups").json()
    db_name = next(n for n in made["created"] if n.endswith(".db"))
    c.post("/api/categories", json={"name": "あとから追加"})

    r = c.post(f"/api/backups/{db_name}/restore")
    assert r.status_code == 200, r.text
    names = [x["name"] for x in c.get("/api/categories").json()]
    assert "バックアップ時点" in names and "あとから追加" not in names
    # 戻す前の状態も自動で残っているので、もう一度戻せる
    before = next(n for n in r.json()["backup_before_restore"] if n.endswith(".db"))
    assert c.post(f"/api/backups/{before}/restore").status_code == 200
    assert "あとから追加" in [x["name"] for x in c.get("/api/categories").json()]
    actions = [a["action"] for a in c.get("/api/audit?type=system").json()]
    assert "backup.restore" in actions


def test_restore_rejects_bad_names(app_client):
    assert app_client.post("/api/backups/settings-20260101-000000.json/restore").status_code == 400
    assert app_client.post("/api/backups/scheduler-20260101-000000.db/restore").status_code == 400  # 存在しない
    assert app_client.post("/api/backups/..%2Fscheduler.db/restore").status_code in (400, 404)


def test_restore_is_admin_only_and_keeps_login(settings, mock_client):
    from fastapi.testclient import TestClient

    from app.auth.passwords import hash_password
    from app.main import create_app
    from tests.conftest import CSRF_HEADERS

    settings.admin_password_hash = hash_password("admin-password-123", iterations=1000)
    app = create_app(settings, start_scheduler=False, client=mock_client)
    with TestClient(app, headers=CSRF_HEADERS) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "admin-password-123"})
        db_name = next(n for n in c.post("/api/backups").json()["created"] if n.endswith(".db"))
        c.post("/api/auth/logout")
        assert c.post(f"/api/backups/{db_name}/restore").status_code == 403  # ログインなしでは戻せない

        c.post("/api/auth/login", json={"username": "admin", "password": "admin-password-123"})
        assert c.post(f"/api/backups/{db_name}/restore").status_code == 200
        assert c.get("/api/auth/me").json()["can"]["admin"] is True  # 戻してもログインしたまま


def test_cli_restore_refuses_while_running(tmp_path, monkeypatch):
    from app import __main__ as entry
    from app import config
    from app.config import Settings
    from app.locking import ProcessLock

    s = Settings(_env_file=None, app_data_dir=tmp_path / "k", log_to_file=False)
    monkeypatch.setattr(entry, "get_settings", lambda: s)
    monkeypatch.setattr(config, "get_settings", lambda: s)
    s.ensure_dirs()
    holder = ProcessLock(s.lock_file)
    holder.acquire()
    try:
        assert entry.main(["--restore", str(tmp_path / "x.db")]) == 2
    finally:
        holder.release()
