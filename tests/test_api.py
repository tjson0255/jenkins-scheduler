"""API を通した一連の流れ（M1〜M4 の完了条件の確認）。"""

import base64
from datetime import date, timedelta

from tests.conftest import edit_fixture

from app.timeutil import local_today


def create_target(c, job="buildset/core-pipeline", **kw):
    r = c.post("/api/targets", json={"job_path": job, "pinned": True, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def create_schedule(c, target_id, **kw):
    today = local_today()
    body = {
        "target_id": target_id,
        "label": "v1.0.0",
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=6)).isoformat(),
        "mode": "cron",
        "cron_expr": "0 3 * * *",
        **kw,
    }
    r = c.post("/api/schedules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_default_categories_and_pages(app_client):
    names = [c["name"] for c in app_client.get("/api/categories").json()]
    assert names == ["ビルドセット", "リリース関連", "その他"]
    for path in ("/", "/targets", "/audit"):
        assert app_client.get(path).status_code == 200


def test_target_registration_checks_jenkins(app_client):
    t = create_target(app_client)
    assert t["display_name"] == "core-pipeline" and t["schema_hash"] and t["param_count"] == 5
    assert app_client.post("/api/targets", json={"job_path": "no/such"}).status_code == 400
    assert app_client.post("/api/targets", json={"job_path": "buildset/core-pipeline"}).status_code == 409
    jobs = app_client.get("/api/jenkins/jobs?q=release").json()
    assert {j["path"] for j in jobs} >= {"release/release-candidate"}


def test_schedule_lifecycle_and_runs(app_client):
    t = create_target(app_client)
    s = create_schedule(app_client, t["id"])
    assert s["status"] == "draft" and s["cron_summary"] == "毎日 03:00"

    runs = app_client.get(f"/api/runs?schedule_id={s['id']}").json()
    assert 6 <= len(runs) <= 7  # 今日の 03:00 が過ぎていれば6件

    # 期間をずらすと未実行の run が作り直される
    new_start = date.fromisoformat(s["start_date"]) + timedelta(days=2)
    r = app_client.patch(f"/api/schedules/{s['id']}", json={"start_date": new_start.isoformat()})
    assert r.status_code == 200
    runs2 = app_client.get(f"/api/runs?schedule_id={s['id']}").json()
    assert len(runs2) == 5

    assert app_client.post(f"/api/schedules/{s['id']}/activate").json()["status"] == "active"
    assert app_client.post(f"/api/schedules/{s['id']}/pause").json()["status"] == "paused"
    assert app_client.post(f"/api/schedules/{s['id']}/resume").json()["status"] == "active"
    assert app_client.delete(f"/api/schedules/{s['id']}").status_code == 409  # draft 以外は削除不可
    assert app_client.post(f"/api/schedules/{s['id']}/cancel").json()["status"] == "cancelled"
    statuses = {r["status"] for r in app_client.get(f"/api/runs?schedule_id={s['id']}").json()}
    assert statuses == {"cancelled"}

    actions = [a["action"] for a in app_client.get(f"/api/audit?type=schedule&target={s['id']}").json()]
    assert {"schedule.create", "schedule.update", "schedule.activate", "schedule.pause", "schedule.cancel"} <= set(actions)


def test_invalid_schedule_input(app_client):
    t = create_target(app_client)
    today = local_today().isoformat()
    bad = [
        {"cron_expr": "H 3 * * *"},
        {"cron_expr": "1 2 3"},
        {"end_date": "2000-01-01"},
        {"mode": "once", "once_at": None},
    ]
    for extra in bad:
        r = app_client.post("/api/schedules", json={"target_id": t["id"], "start_date": today, "mode": "cron", "cron_expr": "0 3 * * *", **extra})
        assert r.status_code == 400, extra


def test_params_preview_save_and_drift_badge(app_client, settings):
    t = create_target(app_client)
    s = create_schedule(app_client, t["id"])
    p = app_client.get(f"/api/schedules/{s['id']}/params").json()
    assert {f["name"] for f in p["fields"]} == {"BRANCH", "BUILD_TYPE", "RUN_TESTS", "VERSION", "MOCK_RESULT"}

    p = app_client.put(
        f"/api/schedules/{s['id']}/params",
        json={"overrides": {"VERSION": "{{schedule.label}}", "BUILD_TYPE": "release"}},
    ).json()
    assert p["params"]["VERSION"] == "v1.0.0"
    assert p["issues"] == []

    dry = app_client.post(f"/api/schedules/{s['id']}/dry-run").json()
    assert dry["endpoint"] == "buildWithParameters" and dry["params"]["BUILD_TYPE"] == "release"
    assert dry["would_kick"] is False  # draft なので実行されない

    # Jenkins 側で選択肢から release を消し、パラメータを追加する → 差分バッジ
    def change(job):
        job["parameters"][1]["choices"] = ["debug", "profile"]
        job["parameters"].append({"name": "NEW_FLAG", "type": "BooleanParameterDefinition", "defaultParameterValue": {"value": False}})

    edit_fixture(settings, "buildset/core-pipeline", change)
    app_client.post("/api/targets/sync")
    sched = app_client.get(f"/api/schedules/{s['id']}").json()
    assert sched["issue_level"] == "error"
    codes = {i["code"] for i in sched["issues"]}
    assert {"choice_invalid", "param_added"} <= codes
    target = app_client.get("/api/targets").json()[0]
    assert target["issue_counts"]["error"] >= 1

    # エラーがあると有効化できない
    r = app_client.post(f"/api/schedules/{s['id']}/activate")
    assert r.status_code == 409


def test_run_now_and_retry(app_client, mock_client, clock):
    t = create_target(app_client, job="release/release-candidate")
    r = app_client.post(f"/api/targets/{t['id']}/run-now", json={"params": {"CHANNEL": "rc", "MOCK_RESULT": "FAILURE"}})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "queued" and run["schedule_id"] is None
    assert run["params"]["CHANNEL"] == "rc" and run["params"]["DRY_RUN"] == "false"

    clock.advance(10)
    app_client.app.state.dispatcher.tick()
    finished = app_client.get(f"/api/runs?target={t['id']}").json()[0]
    assert finished["status"] == "failure"

    retry = app_client.post(f"/api/runs/{run['id']}/retry").json()
    assert retry["retry_of_id"] == run["id"] and retry["params"]["CHANNEL"] == "rc"
    assert len(mock_client.trigger_calls) == 2


def test_hold_release_and_skip(app_client, settings):
    t = create_target(app_client)
    r = app_client.post(f"/api/targets/{t['id']}/run-now", json={"params": {"BUILD_TYPE": "nightly"}}).json()
    assert r["status"] == "holding"
    again = app_client.post(f"/api/runs/{r['id']}/release-hold")
    assert again.status_code == 409  # 再検証でもエラー

    # Jenkins 側で選択肢に追加されれば保留解除でキックされる
    edit_fixture(settings, "buildset/core-pipeline", lambda j: j["parameters"][1]["choices"].append("nightly"))
    ok = app_client.post(f"/api/runs/{r['id']}/release-hold")
    assert ok.status_code == 200 and ok.json()["status"] == "queued"

    s = create_schedule(app_client, t["id"])
    pending = app_client.get(f"/api/runs?schedule_id={s['id']}").json()[0]
    assert app_client.post(f"/api/runs/{pending['id']}/skip").json()["status"] == "skipped"
    assert app_client.post(f"/api/runs/{pending['id']}/skip").status_code == 409


def test_manual_mode_is_rejected(app_client):
    t = create_target(app_client)
    r = app_client.post("/api/schedules", json={"target_id": t["id"], "start_date": local_today().isoformat(), "mode": "manual"})
    assert r.status_code == 422


def test_cron_preview_api(app_client):
    r = app_client.post("/api/cron/preview", json={"cron_expr": "0 9 * * 1", "count": 3}).json()
    assert r["summary"] == "毎週月 09:00" and len(r["times"]) == 3 and "（月）09:00" in r["times_local"][0]
    assert app_client.post("/api/cron/preview", json={"cron_expr": "H H * * *"}).status_code == 400


def test_health_and_metrics(app_client):
    app_client.app.state.dispatcher.tick()
    h = app_client.get("/api/health").json()
    assert h["db"] == "ok" and h["jenkins"] == "ok" and h["dispatcher"] == "ok"
    m = app_client.get("/metrics").text
    assert "jenkins_scheduler_dispatcher_last_tick_timestamp_seconds" in m and "jenkins_scheduler_runs_holding" in m


def test_timer_trigger_warning(app_client):
    t = create_target(app_client, job="buildset/tools-pipeline")
    app_client.post("/api/targets/sync")
    t = next(x for x in app_client.get("/api/targets").json() if x["id"] == t["id"])
    assert t["timer_trigger_detected"] is True
    actions = [a["action"] for a in app_client.get("/api/audit?type=target").json()]
    assert "timer_trigger.detected" in actions


def test_basic_auth(settings, mock_client):
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings.app_basic_auth_user = "admin"
    settings.app_basic_auth_password = "pw"
    app = create_app(settings, start_scheduler=False, client=mock_client)
    with TestClient(app, headers={"X-Requested-With": "jenkins-scheduler"}) as c:
        assert c.get("/api/categories").status_code == 401
        assert c.get("/metrics").status_code == 200  # 監視用は認証対象外
        token = base64.b64encode(b"admin:pw").decode()
        hdr = {"Authorization": f"Basic {token}"}
        assert c.get("/api/categories", headers=hdr).status_code == 200
        c.post("/api/categories", json={"name": "新カテゴリ"}, headers=hdr)
        audit = c.get("/api/audit?type=category", headers=hdr).json()
        assert audit[0]["actor"] == "admin"


def test_holding_is_counted_everywhere(app_client):
    t = create_target(app_client)
    assert app_client.get("/api/runs/holding-count").json() == {"holding": 0}
    r = app_client.post(f"/api/targets/{t['id']}/run-now", json={"params": {"BUILD_TYPE": "nightly"}}).json()
    assert r["status"] == "holding" and r["target_name"] == "core-pipeline"
    assert app_client.get("/api/runs/holding-count").json() == {"holding": 1}
    item = next(x for x in app_client.get("/api/targets").json() if x["id"] == t["id"])
    assert item["holding_count"] == 1
    app_client.post(f"/api/runs/{r['id']}/skip")
    assert app_client.get("/api/runs/holding-count").json() == {"holding": 0}
