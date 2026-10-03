"""schedule → run の生成（仕様書 6.2）。

`schedule.generated_until` までは生成済みとみなし、[generated_until, now + horizon) を補充する。
停止中に過ぎた分も生成されるので、dispatcher の遅延判定（missed_policy）で扱われる。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import ACTIVE, DRAFT, PAUSED, R_HOLDING, R_SCHEDULED, Run, Schedule
from app.scheduler.cronutil import iter_occurrences
from app.scheduler.runstate import UNCLAIMED_PENDING
from app.timeutil import local_midnight_utc, utcnow

GENERATING_STATUSES = (DRAFT, ACTIVE, PAUSED)


def schedule_window(s: Schedule) -> tuple[datetime, datetime | None]:
    start = local_midnight_utc(s.start_date)
    end = local_midnight_utc(s.end_date + timedelta(days=1)) if s.end_date else None
    return start, end


def fill_runs(
    db: Session,
    s: Schedule,
    now: datetime | None = None,
    horizon_days: int = 14,
    catchup_max_days: int = 7,
) -> int:
    """不足している run を補充し、追加した件数を返す。"""
    if s.status not in GENERATING_STATUSES:
        return 0
    now = now or utcnow()
    win_start, win_end = schedule_window(s)
    lo = s.generated_until or now
    lo = max(lo, win_start, now - timedelta(days=catchup_max_days))
    hi = now + timedelta(days=horizon_days)
    if win_end is not None:
        hi = min(hi, win_end)
    if hi <= lo:
        return 0

    if s.mode == "cron":
        if not s.cron_expr:
            return 0
        times = list(iter_occurrences(s.cron_expr, lo, hi))
    elif s.mode == "once":
        times = [s.once_at] if s.once_at and lo <= s.once_at < hi else []
    else:
        times = []

    existing = set()
    if times:
        existing = set(
            db.scalars(
                select(Run.scheduled_at).where(
                    Run.schedule_id == s.id, Run.scheduled_at >= lo, Run.scheduled_at < hi
                )
            )
        )
    added = 0
    for t in times:
        if t in existing:
            continue
        db.add(Run(schedule_id=s.id, target_id=s.target_id, scheduled_at=t, status=R_SCHEDULED))
        added += 1
    s.generated_until = hi
    db.flush()
    return added


def regenerate_runs(db: Session, s: Schedule, now: datetime | None = None, horizon_days: int = 14) -> int:
    """未実行（scheduled / holding）の run だけを削除して作り直す。実行済みは変更しない。"""
    now = now or utcnow()
    # キック処理中（確保済み）や、キックに失敗して送ったか分からない run は残す
    for r in db.scalars(select(Run).where(Run.schedule_id == s.id, UNCLAIMED_PENDING)):
        db.delete(r)
    # 同じ (schedule_id, scheduled_at) を作り直すので、追加より先に削除を確定させる
    db.flush()
    db.expire(s, ["runs"])
    s.generated_until = now
    return fill_runs(db, s, now, horizon_days)


def cancel_pending_runs(db: Session, s: Schedule, status: str, reason: str) -> int:
    """未実行の run をまとめて終わらせる。キック処理中（確保済み）のものには触らない。"""
    res = db.execute(
        update(Run)
        .where(Run.schedule_id == s.id, or_(UNCLAIMED_PENDING, Run.status == R_HOLDING))
        .values(status=status, reason=reason, finished_at=utcnow())
        .execution_options(synchronize_session="fetch")
    )
    return res.rowcount
