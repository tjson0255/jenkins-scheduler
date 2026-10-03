"""run 生成（6.2）: cron の Asia/Tokyo 評価、期間の境界、無期限の先行生成、変更時の再生成。"""

from datetime import date, datetime

from sqlalchemy import select

from app.models import R_HOLDING, R_SCHEDULED, R_SUCCESS, Run
from app.scheduler import planner
from app.scheduler.cronutil import CronError, preview, summarize, validate_cron
from tests.conftest import make_schedule, make_target

import pytest


def run_times(db, s):
    return sorted(db.scalars(select(Run.scheduled_at).where(Run.schedule_id == s.id)))


def test_cron_is_evaluated_in_tokyo(db):
    t = make_target(db)
    s = make_schedule(db, t, start_date=date(2027, 1, 1), end_date=date(2027, 1, 3), cron_expr="0 3 * * *")
    planner.fill_runs(db, s, now=datetime(2026, 12, 31, 0, 0), horizon_days=14)
    # JST 03:00 = 前日 18:00 UTC
    assert run_times(db, s) == [
        datetime(2026, 12, 31, 18, 0),
        datetime(2027, 1, 1, 18, 0),
        datetime(2027, 1, 2, 18, 0),
    ]


def test_period_boundaries_are_inclusive_local_days(db):
    t = make_target(db)
    s = make_schedule(db, t, start_date=date(2027, 1, 1), end_date=date(2027, 1, 1), cron_expr="0 0,23 * * *")
    planner.fill_runs(db, s, now=datetime(2026, 12, 30), horizon_days=14)
    # 2027-01-01 00:00 JST と 23:00 JST の2件だけ（前後の日は含まない）
    assert run_times(db, s) == [datetime(2026, 12, 31, 15, 0), datetime(2027, 1, 1, 14, 0)]


def test_infinite_schedule_generates_only_horizon_and_refills(db):
    t = make_target(db)
    s = make_schedule(db, t, start_date=date(2027, 1, 1), end_date=None, cron_expr="0 12 * * *")
    now = datetime(2027, 1, 1, 0, 0)
    planner.fill_runs(db, s, now=now, horizon_days=14)
    assert len(run_times(db, s)) == 14
    # 翌日に補充すると1件増える（重複は作らない）
    planner.fill_runs(db, s, now=datetime(2027, 1, 2, 0, 0), horizon_days=14)
    planner.fill_runs(db, s, now=datetime(2027, 1, 2, 0, 0), horizon_days=14)
    assert len(run_times(db, s)) == 15


def test_regenerate_only_replaces_pending_runs(db):
    t = make_target(db)
    s = make_schedule(db, t, start_date=date(2027, 1, 1), end_date=date(2027, 1, 10), cron_expr="0 12 * * *")
    now = datetime(2027, 1, 1, 0, 0)
    planner.fill_runs(db, s, now=now, horizon_days=14)
    runs = db.scalars(select(Run).where(Run.schedule_id == s.id).order_by(Run.scheduled_at)).all()
    runs[0].status = R_SUCCESS
    runs[1].status = R_HOLDING
    db.flush()
    done_id = runs[0].id

    s.cron_expr = "30 9 * * *"
    planner.regenerate_runs(db, s, now=datetime(2027, 1, 2, 4, 0), horizon_days=14)
    after = db.scalars(select(Run).where(Run.schedule_id == s.id).order_by(Run.scheduled_at)).all()
    assert after[0].id == done_id and after[0].status == R_SUCCESS  # 実行済みはそのまま
    pending = [r for r in after if r.status == R_SCHEDULED]
    assert all(r.scheduled_at.minute == 30 for r in pending)
    assert not any(r.status == R_HOLDING for r in after)
    # 2027-01-03〜01-10 の 09:30 JST（= 00:30 UTC）
    assert [r.scheduled_at for r in pending][0] == datetime(2027, 1, 3, 0, 30)
    assert len(pending) == 8


def test_once(db):
    t = make_target(db)
    once = make_schedule(db, t, mode="once", cron_expr=None, once_at=datetime(2027, 1, 5, 1, 0),
                         start_date=date(2027, 1, 1), end_date=date(2027, 1, 31))
    planner.fill_runs(db, once, now=datetime(2027, 1, 1), horizon_days=14)
    planner.fill_runs(db, once, now=datetime(2027, 1, 2), horizon_days=14)
    assert run_times(db, once) == [datetime(2027, 1, 5, 1, 0)]


def test_catch_up_after_downtime(db):
    t = make_target(db)
    s = make_schedule(db, t, start_date=date(2027, 1, 1), cron_expr="0 * * * *")
    planner.fill_runs(db, s, now=datetime(2027, 1, 1, 0, 0), horizon_days=1)
    # 2日停止 → generated_until 以降（過去分を含む）が補完され、dispatcher の遅延判定に回る
    planner.fill_runs(db, s, now=datetime(2027, 1, 3, 0, 0), horizon_days=1)
    times = run_times(db, s)
    assert len(times) == 72 and times[-1] == datetime(2027, 1, 3, 23, 0)


def test_cron_validation_and_preview():
    with pytest.raises(CronError):
        validate_cron("H 3 * * *")
    with pytest.raises(CronError):
        validate_cron("0 3 * *")
    with pytest.raises(CronError):
        validate_cron("99 3 * * *")
    assert validate_cron(" 0 3 * * THU ") == "0 3 * * THU"
    times = preview("0 3 * * 1-5", 5, datetime(2027, 1, 1, 0, 0))  # 2027-01-01 は金曜
    assert times[0] == datetime(2027, 1, 3, 18, 0)  # 月曜 03:00 JST
    assert summarize("0 3 * * 1-5") == "平日 03:00"
    assert summarize("15 */6 * * *") == "6時間ごと（15分）"
    assert summarize("0 9 * * 1") == "毎週月 09:00"
