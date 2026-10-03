"""予定・メモのアイテム: Jenkins に接続せず、タイトル・期間・メモだけの予定を持つ。"""

from datetime import timedelta

from app.timeutil import local_today


def make_memo_item(c, name="リリース計画"):
    r = c.post("/api/targets", json={"kind": "memo", "display_name": name, "color": "#888888"})
    assert r.status_code == 201, r.text
    return r.json()


def test_memo_item_needs_no_jenkins(app_client, mock_client):
    t = make_memo_item(app_client)
    assert t["kind"] == "memo" and t["job_path"] is None and t["display_name"] == "リリース計画"
    # 名前が無いと作れない
    assert app_client.post("/api/targets", json={"kind": "memo"}).status_code == 400
    # 同じ名前の予定・メモのアイテムは複数あってよい（job_path が NULL）
    make_memo_item(app_client)
    # Jenkins アイテムは従来どおりジョブのパスが必須
    assert app_client.post("/api/targets", json={"kind": "jenkins"}).status_code == 400


def test_memo_schedule_is_plain_note(app_client, mock_client):
    t = make_memo_item(app_client)
    today = local_today()
    r = app_client.post("/api/schedules", json={
        "target_id": t["id"], "label": "v2.4 コードフリーズ", "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=3)).isoformat(), "note": "QA 期間\n担当: 山田",
        "mode": "cron", "cron_expr": "これは無視される", "activate": True,
    })
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["mode"] == "memo" and s["status"] == "active" and s["cron_expr"] is None
    assert s["note"] == "QA 期間\n担当: 山田" and s["issues"] == []
    assert app_client.get(f"/api/runs?schedule_id={s['id']}").json() == []

    # タイトル・期間・メモは変更できる（mode などは無視）
    r = app_client.patch(f"/api/schedules/{s['id']}", json={"label": "v2.4 凍結", "end_date": None, "mode": "cron"})
    assert r.status_code == 200 and r.json()["label"] == "v2.4 凍結" and r.json()["end_date"] is None and r.json()["mode"] == "memo"

    # 実行に関する操作はできない
    for path in ("activate", "pause", "dry-run"):
        assert app_client.post(f"/api/schedules/{s['id']}/{path}").status_code == 400
    assert app_client.get(f"/api/schedules/{s['id']}/params").status_code == 400
    assert app_client.post(f"/api/targets/{t['id']}/run-now", json={}).status_code == 400

    # 有効状態でも削除できる
    assert app_client.delete(f"/api/schedules/{s['id']}").status_code == 204


def test_memo_items_are_skipped_by_jenkins_sync(app_client, mock_client):
    make_memo_item(app_client)
    app_client.post("/api/targets", json={"job_path": "buildset/core-pipeline"})
    results = app_client.post("/api/targets/sync").json()
    assert [r["job_path"] for r in results] == ["buildset/core-pipeline"]

    from app.db import SessionLocal
    from app.scheduler.poller import poll_all

    assert len(poll_all(SessionLocal, mock_client, app_client.app.state.settings)) == 1
    app_client.app.state.dispatcher.tick()
    assert "jenkins_scheduler_schema_drift" in app_client.get("/metrics").text
