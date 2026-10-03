"""dispatcher（9.1）: holding / missed / 重複チェック / 状態遷移 / 再起動耐性。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    AppState,
    AuditLog,
    ParamOverride,
    R_CANCELLED,
    R_FAILURE,
    R_HOLDING,
    R_MISSED,
    R_QUEUED,
    R_RUNNING,
    R_SCHEDULED,
    R_SKIPPED,
    R_SUCCESS,
    Run,
)
from app.scheduler.dispatcher import LAST_TICK_KEY, Dispatcher
from tests.conftest import edit_fixture, make_schedule, make_target

T0 = datetime(2027, 1, 5, 18, 0)  # 2027-01-06 03:00 JST


@pytest.fixture
def dispatcher(session_factory, mock_client, settings):
    return Dispatcher(session_factory, mock_client, settings)


def setup_run(db, job="buildset/core-pipeline", overrides=None, at=T0, **sched_kw):
    t = make_target(db, job_path=job, overlap_policy=sched_kw.pop("overlap_policy", "skip"))
    s = make_schedule(db, t, generated_until=at + timedelta(days=30), **sched_kw)
    for k, v in (overrides or {}).items():
        s.overrides.append(ParamOverride(param_name=k, value_template=v))
    r = Run(schedule_id=s.id, target_id=t.id, scheduled_at=at, status=R_SCHEDULED)
    db.add(r)
    db.commit()
    return r.id


def get_run(session_factory, rid) -> Run:
    with session_factory() as db:
        return db.get(Run, rid)


def test_kick_and_track_to_success(db, session_factory, dispatcher, mock_client, clock):
    rid = setup_run(db, overrides={"MOCK_RESULT": "SUCCESS", "VERSION": "{{run.date}}"})
    dispatcher.tick(now=T0 + timedelta(seconds=5))
    r = get_run(session_factory, rid)
    assert r.status == R_QUEUED and r.queue_id is not None
    assert r.params_json["VERSION"] == "2027-01-06"
    assert r.schema_hash
    assert mock_client.trigger_calls[-1]["with_params"] is True

    clock.advance(2)
    dispatcher.tick(now=T0 + timedelta(seconds=35))
    r = get_run(session_factory, rid)
    assert r.status == R_RUNNING and r.build_number == 1 and r.build_url

    clock.advance(3)
    dispatcher.tick(now=T0 + timedelta(seconds=65))
    r = get_run(session_factory, rid)
    assert r.status == R_SUCCESS and r.finished_at
    assert len(mock_client.trigger_calls) == 1


def test_failure_result(db, session_factory, dispatcher, clock):
    rid = setup_run(db, overrides={"MOCK_RESULT": "FAILURE"})
    dispatcher.tick(now=T0)
    clock.advance(10)
    dispatcher.tick(now=T0 + timedelta(seconds=30))  # queued → running → 結果まで1 tick で進む
    assert get_run(session_factory, rid).status == R_FAILURE


def test_not_due_and_inactive_are_not_kicked(db, session_factory, dispatcher, mock_client):
    rid = setup_run(db)
    dispatcher.tick(now=T0 - timedelta(seconds=1))
    assert get_run(session_factory, rid).status == R_SCHEDULED
    rid2 = setup_run(db, job="buildset/web-pipeline", status="draft")
    dispatcher.tick(now=T0 + timedelta(seconds=1))
    assert get_run(session_factory, rid2).status == R_SCHEDULED
    assert [c["job"] for c in mock_client.trigger_calls] == ["buildset/core-pipeline"]


def test_missed_policy_skip(db, session_factory, dispatcher, mock_client):
    rid = setup_run(db, missed_policy="skip")
    dispatcher.tick(now=T0 + timedelta(minutes=3))
    r = get_run(session_factory, rid)
    assert r.status == R_MISSED and "skip" in r.reason
    assert mock_client.trigger_calls == []


def test_missed_policy_run_late_within_and_beyond_grace(db, session_factory, dispatcher):
    rid = setup_run(db, missed_policy="run_late", grace_minutes=10)
    dispatcher.tick(now=T0 + timedelta(minutes=9))
    assert get_run(session_factory, rid).status == R_QUEUED

    rid2 = setup_run(db, job="buildset/web-pipeline", missed_policy="run_late", grace_minutes=10)
    dispatcher.tick(now=T0 + timedelta(minutes=11))
    r2 = get_run(session_factory, rid2)
    assert r2.status == R_MISSED and "猶予" in r2.reason


def test_holding_on_invalid_choice(db, session_factory, dispatcher, mock_client):
    rid = setup_run(db, overrides={"BUILD_TYPE": "nightly"})
    dispatcher.tick(now=T0)
    r = get_run(session_factory, rid)
    assert r.status == R_HOLDING and "BUILD_TYPE" in r.reason
    assert mock_client.trigger_calls == []


def test_holding_when_schema_changes_choice(db, session_factory, dispatcher, settings, mock_client):
    rid = setup_run(db, overrides={"BUILD_TYPE": "release"})
    edit_fixture(settings, "buildset/core-pipeline", lambda j: j["parameters"][1].update(choices=["debug", "profile"]))
    dispatcher.tick(now=T0)
    assert get_run(session_factory, rid).status == R_HOLDING


def test_holding_when_job_missing_or_not_buildable(db, session_factory, dispatcher):
    rid = setup_run(db, job="no/such-job")
    rid2 = setup_run(db, job="misc/legacy-job")
    dispatcher.tick(now=T0)
    assert "見つかりません" in get_run(session_factory, rid).reason
    r2 = get_run(session_factory, rid2)
    assert r2.status == R_HOLDING and "buildable" in r2.reason


def test_overlap_skip_and_queue(db, session_factory, dispatcher, mock_client):
    mock_client.trigger("buildset/core-pipeline", {}, True)  # 前回ビルドがキュー中
    mock_client.trigger("buildset/web-pipeline", {}, True)
    rid_skip = setup_run(db, job="buildset/core-pipeline", overlap_policy="skip")
    rid_queue = setup_run(db, job="buildset/web-pipeline", overlap_policy="queue")
    dispatcher.tick(now=T0)
    r = get_run(session_factory, rid_skip)
    assert r.status == R_SKIPPED and "実行中" in r.reason
    assert get_run(session_factory, rid_queue).status == R_QUEUED


def test_job_without_params_uses_build(db, session_factory, dispatcher, mock_client):
    rid = setup_run(db, job="misc/cleanup-workspace")
    dispatcher.tick(now=T0)
    assert get_run(session_factory, rid).status == R_QUEUED
    assert mock_client.trigger_calls[-1]["with_params"] is False


def test_restart_resumes_tracking_without_rekick(db, session_factory, dispatcher, mock_client, settings, clock):
    rid = setup_run(db, overrides={"MOCK_RESULT": "SUCCESS"})
    dispatcher.tick(now=T0)
    assert get_run(session_factory, rid).status == R_QUEUED

    # プロセス再起動: 新しい Dispatcher（モックの状態はファイルに残る）
    from app.jenkins.mock import MockJenkinsClient

    client2 = MockJenkinsClient(settings.jenkins_mock_fixtures, mock_client.state_file, clock=clock)
    d2 = Dispatcher(session_factory, client2, settings)
    d2.startup_check(now=T0 + timedelta(minutes=10))
    clock.advance(10)
    d2.tick(now=T0 + timedelta(minutes=10))
    assert get_run(session_factory, rid).status == R_SUCCESS
    assert client2.trigger_calls == []


def test_runs_missed_while_stopped_follow_policy(db, session_factory, dispatcher, settings, mock_client):
    # 前回 tick から 2 時間停止していた
    with session_factory() as s:
        s.add(AppState(key=LAST_TICK_KEY, value=(T0 - timedelta(hours=1)).isoformat()))
        s.commit()
    late_skip = setup_run(db, missed_policy="run_late", grace_minutes=10, at=T0)
    in_grace = setup_run(db, job="buildset/web-pipeline", missed_policy="run_late", grace_minutes=180, at=T0)
    now = T0 + timedelta(hours=1)
    dispatcher.startup_check(now=now)
    dispatcher.tick(now=now)
    assert get_run(session_factory, late_skip).status == R_MISSED
    assert get_run(session_factory, in_grace).status == R_QUEUED
    with session_factory() as s:
        log = s.scalars(select(AuditLog).where(AuditLog.action == "startup_gap")).one()
        assert log.detail_json["runs"] == 2


def test_cancelled_queue_item(db, session_factory, dispatcher, mock_client):
    rid = setup_run(db)
    dispatcher.tick(now=T0)
    mock_client.cancel_queue_item(get_run(session_factory, rid).queue_id)
    dispatcher.tick(now=T0 + timedelta(seconds=30))
    assert get_run(session_factory, rid).status == R_CANCELLED


def test_disabled_target_is_skipped(db, session_factory, dispatcher, mock_client):
    rid = setup_run(db)
    with session_factory() as s:
        r = s.get(Run, rid)
        r.target.enabled = False
        s.commit()
    dispatcher.tick(now=T0)
    assert get_run(session_factory, rid).status == R_SKIPPED
    assert mock_client.trigger_calls == []


def test_trigger_failure_is_not_retried(db, session_factory, dispatcher, mock_client, monkeypatch):
    from app.jenkins.base import JenkinsError

    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise JenkinsError("HTTP 500", "http_500", 500)

    monkeypatch.setattr(mock_client, "trigger", boom)
    rid = setup_run(db)
    dispatcher.tick(now=T0)
    dispatcher.tick(now=T0 + timedelta(seconds=30))
    r = get_run(session_factory, rid)
    assert r.status == R_HOLDING and "自動再試行しません" in r.reason
    assert len(calls) == 1
