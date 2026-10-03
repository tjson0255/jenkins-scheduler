"""排他・同期: 二重キックと同時編集の競合を再現して、防げていることを確かめる。"""

import threading
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.concurrency import bump_revision
from app.models import (
    PAUSED,
    R_HOLDING,
    R_MISSED,
    R_QUEUED,
    R_SCHEDULED,
    R_SKIPPED,
    Run,
    Schedule,
)
from app.scheduler import planner, runstate
from app.scheduler.dispatcher import Dispatcher
from app.timeutil import utcnow
from tests.conftest import make_schedule, make_target


@pytest.fixture
def dispatcher(session_factory, mock_client, settings):
    return Dispatcher(session_factory, mock_client, settings)


def due_run(db, at=None, **sched_kw):
    """予定時刻が今の、有効なスケジュールの run を1件作る。"""
    at = at or utcnow().replace(microsecond=0)
    t = make_target(db, job_path=sched_kw.pop("job", "buildset/core-pipeline"))
    s = make_schedule(db, t, generated_until=at + timedelta(days=30), **sched_kw)
    r = Run(schedule_id=s.id, target_id=t.id, scheduled_at=at, status=R_SCHEDULED)
    db.add(r)
    db.commit()
    return r.id


def load(session_factory, rid):
    with session_factory() as s:
        return s.get(Run, rid)


# ---------------------------------------------------------------- 二重キック
def test_many_threads_kick_same_run_only_once(session_factory, dispatcher, mock_client):
    with session_factory() as db:
        rid = due_run(db)
    barrier = threading.Barrier(8)

    def worker():
        with session_factory() as s:
            r = s.get(Run, rid)
            barrier.wait()
            dispatcher.process_due(s, r, utcnow())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(mock_client.trigger_calls) == 1
    assert load(session_factory, rid).status == R_QUEUED


def test_run_now_is_not_picked_up_by_tick(session_factory, dispatcher, mock_client):
    """「今すぐ実行」で作った run を、キック前に tick が拾っても二重にならない。"""
    with session_factory() as db:
        t = make_target(db)
        r = Run(schedule_id=None, target_id=t.id, scheduled_at=utcnow(), status=R_SCHEDULED, params_json={}, triggered_at=utcnow())
        db.add(r)
        db.commit()
        rid = r.id
    dispatcher.tick()  # API がキックする前に tick が割り込む
    with session_factory() as db:
        dispatcher.kick_now(db, db.get(Run, rid))
    assert len(mock_client.trigger_calls) == 1
    assert load(session_factory, rid).status == R_QUEUED


def test_release_hold_is_not_marked_missed_by_tick(session_factory, dispatcher, mock_client):
    """保留解除の直後に tick が割り込んでも、見逃しにされず1回だけキックされる。"""
    with session_factory() as db:
        rid = due_run(db, at=utcnow().replace(microsecond=0) - timedelta(hours=3))  # 猶予を過ぎた保留
        db.get(Run, rid).status = R_HOLDING
        db.commit()
    with session_factory() as db:
        r = db.get(Run, rid)
        assert runstate.claim_held(db, r)
        dispatcher.tick()  # 割り込み
        dispatcher.kick_now(db, r)
    r = load(session_factory, rid)
    assert r.status == R_QUEUED and r.status != R_MISSED
    assert len(mock_client.trigger_calls) == 1


def test_release_hold_twice_kicks_once(app_client, mock_client):
    t = app_client.post("/api/targets", json={"job_path": "buildset/core-pipeline"}).json()
    r = app_client.post(f"/api/targets/{t['id']}/run-now", json={"params": {"BUILD_TYPE": "nightly"}}).json()
    from tests.conftest import edit_fixture  # noqa: F401  （選択肢を直す代わりに値を差し替える）

    from app.db import SessionLocal

    with SessionLocal() as db:
        run = db.get(Run, r["id"])
        run.params_json = {"BUILD_TYPE": "release"}
        db.commit()
    first = app_client.post(f"/api/runs/{r['id']}/release-hold")
    second = app_client.post(f"/api/runs/{r['id']}/release-hold")
    assert first.status_code == 200 and first.json()["status"] == "queued"
    assert second.status_code == 409
    assert len(mock_client.trigger_calls) == 1


def test_skip_wins_against_stale_dispatcher(session_factory, dispatcher, mock_client):
    """dispatcher が古い状態の run を持っていても、先にスキップされていればキックしない。"""
    with session_factory() as db:
        rid = due_run(db)
    with session_factory() as tick_db:
        stale = tick_db.get(Run, rid)  # tick が due を読み込んだ
        with session_factory() as api_db:
            assert runstate.transition(api_db, api_db.get(Run, rid), R_SKIPPED, "手動でスキップ")
        dispatcher.process_due(tick_db, stale, utcnow())
    assert mock_client.trigger_calls == []
    assert load(session_factory, rid).status == R_SKIPPED


def test_skip_fails_while_kick_in_progress(session_factory):
    with session_factory() as db:
        rid = due_run(db)
        r = db.get(Run, rid)
        assert runstate.claim(db, r)  # dispatcher が確保してキック処理中
    with session_factory() as db:
        assert not runstate.transition(db, db.get(Run, rid), R_SKIPPED, "手動でスキップ")
        assert db.get(Run, rid).status == R_SCHEDULED


def test_skip_api_returns_conflict_while_kicking(app_client):
    from app.db import SessionLocal

    t = app_client.post("/api/targets", json={"job_path": "buildset/core-pipeline"}).json()
    with SessionLocal() as db:
        r = Run(schedule_id=None, target_id=t["id"], scheduled_at=utcnow(), status=R_SCHEDULED, triggered_at=utcnow())
        db.add(r)
        db.commit()
        rid = r.id
    res = app_client.post(f"/api/runs/{rid}/skip")
    assert res.status_code == 409 and "キック処理中" in res.json()["detail"]


def test_pause_after_due_is_respected(session_factory, dispatcher, mock_client):
    """tick が due を読んだ後に一時停止されたら、キックしない。"""
    with session_factory() as db:
        rid = due_run(db)
    with session_factory() as tick_db:
        stale = tick_db.get(Run, rid)
        with session_factory() as api_db:
            api_db.get(Schedule, stale.schedule_id).status = PAUSED
            api_db.commit()
        dispatcher.process_due(tick_db, stale, utcnow())
    assert mock_client.trigger_calls == []
    assert load(session_factory, rid).status == R_SCHEDULED


def test_regenerate_keeps_run_being_kicked(session_factory):
    with session_factory() as db:
        rid = due_run(db, cron_expr="* * * * *")
        r = db.get(Run, rid)
        assert runstate.claim(db, r)
        s = db.get(Schedule, r.schedule_id)
        s.cron_expr = "0 3 * * *"
        planner.regenerate_runs(db, s, utcnow())
        db.commit()
    assert load(session_factory, rid) is not None


def test_startup_puts_interrupted_kick_on_hold(session_factory, dispatcher, mock_client, settings):
    with session_factory() as db:
        rid = due_run(db)
        assert runstate.claim(db, db.get(Run, rid))  # 確保した直後にプロセスが落ちた
    Dispatcher(session_factory, mock_client, settings).startup_check()
    r = load(session_factory, rid)
    assert r.status == R_HOLDING and "途中で停止" in r.reason
    assert mock_client.trigger_calls == []


# ---------------------------------------------------------------- 同時編集
def test_stale_revision_is_rejected(app_client):
    t = app_client.post("/api/targets", json={"job_path": "buildset/core-pipeline"}).json()
    today = utcnow().date().isoformat()
    s = app_client.post("/api/schedules", json={"target_id": t["id"], "start_date": today, "cron_expr": "0 3 * * *"}).json()
    rev = s["revision"]
    ok = app_client.patch(f"/api/schedules/{s['id']}", json={"label": "Aさん", "revision": rev})
    assert ok.status_code == 200 and ok.json()["revision"] == rev + 1
    stale = app_client.patch(f"/api/schedules/{s['id']}", json={"label": "Bさん", "revision": rev})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "conflict"
    assert app_client.get(f"/api/schedules/{s['id']}").json()["label"] == "Aさん"

    p = app_client.put(f"/api/schedules/{s['id']}/params", json={"overrides": {"BRANCH": "x"}, "revision": rev})
    assert p.status_code == 409

    item_rev = t["revision"]
    assert app_client.patch(f"/api/targets/{t['id']}", json={"display_name": "A", "revision": item_rev}).status_code == 200
    assert app_client.patch(f"/api/targets/{t['id']}", json={"display_name": "B", "revision": item_rev}).status_code == 409

    # 状態の操作（有効化など）も番号を進めるので、その前に開いていた画面からの保存は止まる
    cur = app_client.get(f"/api/schedules/{s['id']}").json()["revision"]
    app_client.post(f"/api/schedules/{s['id']}/activate")
    assert app_client.patch(f"/api/schedules/{s['id']}", json={"note": "x", "revision": cur}).status_code == 409


def test_concurrent_saves_with_same_revision_only_one_wins(session_factory):
    with session_factory() as db:
        t = make_target(db)
        s = make_schedule(db, t)
        db.commit()
        sid = s.id
    barrier = threading.Barrier(2)
    results = []

    def save(name):
        with session_factory() as db:
            barrier.wait()
            try:
                bump_revision(db, Schedule, sid, 0)
                db.get(Schedule, sid).label = name
                threading.Event().wait(0.2)  # 保存処理に時間がかかっている間に、もう一方が来る
                db.commit()
                results.append(("ok", name))
            except HTTPException as exc:
                results.append(("conflict", exc.status_code))

    threads = [threading.Thread(target=save, args=(n,)) for n in ("A", "B")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(r[0] for r in results) == ["conflict", "ok"]
    winner = next(r[1] for r in results if r[0] == "ok")
    with session_factory() as db:
        s = db.get(Schedule, sid)
        assert s.label == winner and s.revision == 1


def test_no_pending_claims_left_after_tick(session_factory, dispatcher):
    with session_factory() as db:
        due_run(db)
    dispatcher.tick()
    with session_factory() as db:
        left = db.scalars(select(Run).where(Run.status == R_SCHEDULED, Run.triggered_at.is_not(None))).all()
        assert left == []
