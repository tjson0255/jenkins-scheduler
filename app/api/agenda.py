"""1日の予定（日別の一覧）。"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import run_out
from app.models import ACTIVE, CANCELLED, DRAFT, MODE_CRON, MODE_MEMO, MODE_ONCE, PAUSED, Run, Schedule
from app.scheduler.cronutil import CronError, iter_occurrences, summarize
from app.timeutil import iso_z, local_midnight_utc, utcnow

router = APIRouter(tags=["agenda"])

# run を作る前の予定（先の日付やドラフト）も、スケジュールから時刻を計算して見せる
PLANNING_STATUSES = (DRAFT, ACTIVE, PAUSED)


def _title(s: Schedule) -> str:
    return s.label or (summarize(s.cron_expr) if s.mode == MODE_CRON else "1回")


@router.get("/api/agenda")
def get_agenda(day: date = Query(alias="date"), db: Session = Depends(get_db)):
    """その日（Asia/Tokyo）の run と、まだ run になっていない予定、自由記入の予定・メモ。"""
    lo = local_midnight_utc(day)
    hi = local_midnight_utc(day + timedelta(days=1))
    now = utcnow()

    runs = db.scalars(select(Run).where(Run.scheduled_at >= lo, Run.scheduled_at < hi).order_by(Run.scheduled_at, Run.id)).all()
    have = {(r.schedule_id, r.scheduled_at) for r in runs}

    schedules = db.scalars(
        select(Schedule).where(
            Schedule.status != CANCELLED,
            Schedule.start_date <= day,
            (Schedule.end_date.is_(None)) | (Schedule.end_date >= day),
        ).order_by(Schedule.start_date, Schedule.id)
    ).all()

    planned = []
    for s in schedules:
        if s.mode == MODE_MEMO or s.status not in PLANNING_STATUSES:
            continue
        if s.mode == MODE_CRON and s.cron_expr:
            try:
                times = list(iter_occurrences(s.cron_expr, max(lo, now), hi))
            except CronError:
                times = []
        elif s.mode == MODE_ONCE and s.once_at and max(lo, now) <= s.once_at < hi:
            times = [s.once_at]
        else:
            times = []
        for t in times:
            if (s.id, t) in have:
                continue
            planned.append({
                "schedule_id": s.id,
                "schedule_status": s.status,
                "schedule_title": _title(s),
                "target_id": s.target_id,
                "target_name": s.target.display_name,
                "scheduled_at": iso_z(t),
            })
    planned.sort(key=lambda p: p["scheduled_at"])

    memos = [
        {
            "schedule_id": s.id,
            "target_id": s.target_id,
            "target_name": s.target.display_name,
            "label": s.label,
            "note": s.note,
            "start_date": s.start_date.isoformat(),
            "end_date": s.end_date.isoformat() if s.end_date else None,
        }
        for s in schedules
        if s.mode == MODE_MEMO
    ]
    out_runs = []
    for r in runs:
        o = run_out(r)
        o["schedule_status"] = r.schedule.status if r.schedule else None
        out_runs.append(o)
    return {"date": day.isoformat(), "runs": out_runs, "planned": planned, "memos": memos}
