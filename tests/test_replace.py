"""その回だけの変更（置き換え）: 元の回をスキップし、日時・パラメータを変えた回を同じスケジューラに作る。"""

from datetime import timedelta

from tests.test_api import create_schedule, create_target

from app.timeutil import iso_z, local_today, to_local, utcnow


def setup(c, days_ahead=3):
    t = create_target(c)
    today = local_today()
    s = create_schedule(c, t["id"], start_date=(today + timedelta(days=days_ahead)).isoformat(),
                        end_date=(today + timedelta(days=days_ahead + 2)).isoformat(), activate=True)
    runs = c.get(f"/api/runs?schedule_id={s['id']}&order=asc").json()
    return t, s, runs


def local_input(dt_utc_naive):
    return to_local(dt_utc_naive).strftime("%Y-%m-%dT%H:%M")


def test_replace_creates_replacement_and_skips_original(app_client):
    c = app_client
    _t, s, runs = setup(c)
    first = runs[0]
    new_at = (utcnow() + timedelta(days=3, hours=5)).replace(second=0, microsecond=0)

    # その回に送るパラメータ（入力欄の初期値）
    p = c.get(f"/api/runs/{first['id']}/params").json()
    assert {f["name"] for f in p["fields"]} >= {"BUILD_TYPE", "VERSION"}

    r = c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": local_input(new_at), "params": {"BUILD_TYPE": "profile"}})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["replaces_run_id"] == first["id"] and rep["schedule_id"] == s["id"]
    assert rep["scheduled_at"] == iso_z(new_at) and rep["override_params"] == {"BUILD_TYPE": "profile"}
    assert rep["reason"].endswith("の回から変更")

    after = {x["id"]: x for x in c.get(f"/api/runs?schedule_id={s['id']}").json()}
    assert after[first["id"]]["status"] == "skipped" and after[first["id"]]["reason"].startswith("この回だけ変更 →")
    # スケジューラの設定は変わらない
    assert c.get(f"/api/schedules/{s['id']}").json()["cron_expr"] == s["cron_expr"]

    # 入力欄には、その回だけ変えた値が出る
    p2 = {f["name"]: f for f in c.get(f"/api/runs/{rep['id']}/params").json()["fields"]}
    assert p2["BUILD_TYPE"]["value"] == "profile" and p2["BUILD_TYPE"]["overridden"]

    # 置き換えた回をもう一度変える（新しい回は増えない）
    again = c.post(f"/api/runs/{rep['id']}/replace", json={"scheduled_at": local_input(new_at + timedelta(minutes=30)), "params": {}}).json()
    assert again["id"] == rep["id"] and again["override_params"] is None
    assert len(c.get(f"/api/runs?schedule_id={s['id']}").json()) == len(runs) + 1


def test_unreplace_restores_original(app_client):
    c = app_client
    _t, s, runs = setup(c)
    first = runs[0]
    new_at = (utcnow() + timedelta(days=4)).replace(second=0, microsecond=0)
    rep = c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": local_input(new_at)}).json()

    u = c.post(f"/api/runs/{rep['id']}/unreplace")
    assert u.status_code == 200 and u.json()["restored"] is True
    after = {x["id"]: x for x in c.get(f"/api/runs?schedule_id={s['id']}").json()}
    assert rep["id"] not in after and after[first["id"]]["status"] == "scheduled" and after[first["id"]]["reason"] is None
    assert c.post(f"/api/runs/{first['id']}/unreplace").status_code == 400  # 置き換えた回ではない


def test_replace_guards(app_client):
    c = app_client
    _t, s, runs = setup(c)
    first, second = runs[0], runs[1]
    past = utcnow() - timedelta(hours=1)
    assert c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": local_input(past)}).status_code == 400
    # 同じスケジューラの別の回と同じ時刻にはできない
    second_at = second["scheduled_at"].replace("Z", "+00:00")
    assert c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": second_at}).status_code == 409
    # 選択肢に無い値・存在しないパラメータは断る
    future = local_input(utcnow() + timedelta(days=5))
    assert c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": future, "params": {"BUILD_TYPE": "nightly"}}).status_code == 400
    assert c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": future, "params": {"NOPE": "1"}}).status_code == 400
    # 何も変わっていないので元の回は「予定」のまま
    assert c.get(f"/api/runs?schedule_id={s['id']}&order=asc").json()[0]["status"] == "scheduled"
    # 実行済み（スキップ済み）の回は変えられない
    c.post(f"/api/runs/{first['id']}/skip")
    assert c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": future}).status_code == 409


def test_replacement_survives_schedule_edit_and_kicks_with_override(app_client, mock_client):
    c = app_client
    t, s, runs = setup(c, days_ahead=1)
    first = runs[0]
    new_at = (utcnow() + timedelta(hours=30)).replace(second=0, microsecond=0)
    rep = c.post(f"/api/runs/{first['id']}/replace", json={"scheduled_at": local_input(new_at), "params": {"BUILD_TYPE": "profile"}}).json()

    # スケジューラを編集して未実行の回が作り直されても、置き換えの回は残り、元の回も復活しない
    assert c.patch(f"/api/schedules/{s['id']}", json={"cron_expr": "0 4 * * *"}).status_code == 200
    after = {x["id"]: x for x in c.get(f"/api/runs?schedule_id={s['id']}").json()}
    assert rep["id"] in after and after[rep["id"]]["status"] == "scheduled"
    assert after[first["id"]]["status"] == "skipped"

    # 置き換えの時刻になると、その回だけのパラメータでキックされる
    c.app.state.dispatcher.tick(new_at)
    kicked = c.get(f"/api/runs?schedule_id={s['id']}").json()
    rep_now = next(x for x in kicked if x["id"] == rep["id"])
    assert rep_now["status"] in ("queued", "running"), rep_now
    assert rep_now["params"]["BUILD_TYPE"] == "profile"
    assert mock_client.trigger_calls[-1]["params"]["BUILD_TYPE"] == "profile"
