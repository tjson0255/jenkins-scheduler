"""公開デモ（DEMO_MODE=true）。"""

from fastapi.testclient import TestClient

from tests.conftest import CSRF_HEADERS


def test_demo_populates_and_forces_mock(settings, mock_client):
    from app.main import create_app

    settings.demo_mode = True
    settings.jenkins_mock = False  # 間違えて本物を指定しても
    settings.admin_password = "demo-admin-password"
    app = create_app(settings, start_scheduler=False, client=mock_client)
    assert settings.jenkins_mock is True  # モックに強制される
    with TestClient(app, headers=CSRF_HEADERS) as c:
        me = c.get("/api/auth/me").json()
        assert me["demo"]["admin_password"] == "demo-admin-password" and me["can"]["admin"] is False
        targets = c.get("/api/targets").json()
        assert len(targets) >= 10 and any(t["kind"] == "memo" for t in targets)
        schedules = c.get("/api/schedules").json()
        assert any(s["mode"] == "memo" for s in schedules) and any(s["status"] == "active" for s in schedules)
        runs = c.get("/api/runs?status=success,failure").json()
        assert runs, "過去の実行履歴がある"
        assert c.get("/api/runs/holding-count").json()["holding"] == 1
        # いろいろな機能の例が入っている
        statuses = {s["status"] for s in schedules}
        assert {"active", "paused", "draft", "ended"} <= statuses
        assert any(s["exclusive"] and s["status"] == "active" for s in schedules)  # 他スケジューラ停止
        all_runs = c.get("/api/runs?limit=10000").json()
        assert any(r["suppressed_by"] for r in all_runs)  # 止められる回
        assert any(r["replaces_run_id"] for r in all_runs)  # この回だけ変更
        assert any(r["schedule_deleted"] for r in all_runs)  # 削除済みのスケジューラの履歴
        rc_titles = {s["label"] for s in schedules if s["mode"] == "once" and s["status"] == "active"}
        assert {"v2.4.0 RC1", "v2.4.0 RC2"} <= rc_titles  # 同じレーンで日によって違うスケジューラ

        # 初期化すると、書き込んだ内容は消えて元に戻る
        memo_item = next(t for t in targets if t["kind"] == "memo")
        c.post("/api/schedules", json={"target_id": memo_item["id"], "start_date": "2030-01-01", "label": "落書き"})
        from app import demo
        from app.db import SessionLocal

        with SessionLocal() as db:
            demo.reset(db, settings, mock_client)
        labels = [s["label"] for s in c.get("/api/schedules?include_cancelled=true").json()]
        assert "落書き" not in labels and len(labels) == len(schedules)
