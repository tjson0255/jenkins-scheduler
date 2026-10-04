"""この期間は、同じレーンの他のスケジューラを止める（臨時のスケジューラ）。"""

from datetime import timedelta

from tests.test_api import create_schedule, create_target

from app.timeutil import local_today


def setup(c):
    t = create_target(c)
    today = local_today()
    regular = create_schedule(c, t["id"], label="普段", start_date=today.isoformat(),
                              end_date=(today + timedelta(days=6)).isoformat(), cron_expr="0 3 * * *", activate=True)
    day = today + timedelta(days=3)
    temp = create_schedule(c, t["id"], label="臨時", start_date=day.isoformat(), end_date=day.isoformat(),
                           cron_expr="0 5 * * *", exclusive=True, activate=True)
    return t, regular, temp, day


def runs_of(c, sid):
    return c.get(f"/api/runs?schedule_id={sid}&order=asc").json()


def test_exclusive_suppresses_other_schedulers_only_in_its_period(app_client):
    c = app_client
    _t, regular, temp, day = setup(c)
    assert temp["exclusive"] is True
    by_day = {r["scheduled_at"][:10]: r for r in runs_of(c, regular["id"])}
    # 03:00 JST は前日の 18:00 UTC
    target_key = (day - timedelta(days=1)).isoformat()
    assert by_day[target_key]["suppressed_by"] == "臨時"
    assert all(r["suppressed_by"] is None for k, r in by_day.items() if k != target_key)
    # 臨時のスケジューラ自身の回は止めない
    assert all(r["suppressed_by"] is None for r in runs_of(c, temp["id"]))

    # 1日の予定でも、止められる回が分かる
    a = c.get(f"/api/agenda?date={day.isoformat()}").json()
    assert {(x["schedule_title"], x.get("suppressed_by")) for x in a["runs"]} == {("普段", "臨時"), ("臨時", None)}


def test_pausing_temp_restores_regular_runs(app_client):
    c = app_client
    _t, regular, temp, day = setup(c)
    c.post(f"/api/schedules/{temp['id']}/pause")
    assert all(r["suppressed_by"] is None for r in runs_of(c, regular["id"]))
    c.post(f"/api/schedules/{temp['id']}/resume")
    assert any(r["suppressed_by"] == "臨時" for r in runs_of(c, regular["id"]))
    # 削除しても元に戻る
    assert c.delete(f"/api/schedules/{temp['id']}").status_code == 204
    assert all(r["suppressed_by"] is None for r in runs_of(c, regular["id"]))


def test_dispatcher_skips_suppressed_run_and_kicks_temp(app_client, mock_client):
    from datetime import datetime

    c = app_client
    _t, regular, temp, day = setup(c)
    suppressed = next(r for r in runs_of(c, regular["id"]) if r["suppressed_by"])
    temp_run = runs_of(c, temp["id"])[0]
    dispatcher = c.app.state.dispatcher
    dispatcher.tick(datetime.fromisoformat(suppressed["scheduled_at"].replace("Z", "")))
    after = next(r for r in runs_of(c, regular["id"]) if r["id"] == suppressed["id"])
    assert after["status"] == "skipped" and "「臨時」を優先" in after["reason"]
    calls = len(mock_client.trigger_calls)
    dispatcher.tick(datetime.fromisoformat(temp_run["scheduled_at"].replace("Z", "")))
    assert next(r for r in runs_of(c, temp["id"]) if r["id"] == temp_run["id"])["status"] in ("queued", "running")
    assert len(mock_client.trigger_calls) == calls + 1


def test_exclusive_requires_end_date(app_client):
    c = app_client
    t = create_target(c)
    r = c.post("/api/schedules", json={"target_id": t["id"], "label": "臨時", "start_date": local_today().isoformat(),
                                       "end_date": None, "mode": "cron", "cron_expr": "0 5 * * *", "exclusive": True})
    assert r.status_code == 400 and "終了日" in r.json()["detail"]
    s = create_schedule(c, t["id"], end_date=None)
    assert c.patch(f"/api/schedules/{s['id']}", json={"exclusive": True}).status_code == 400
