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
