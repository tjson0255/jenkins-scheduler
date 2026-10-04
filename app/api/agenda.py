"""1日の予定（日別の一覧）。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.serializers import run_out
from app.models import ACTIVE, CANCELLED, DRAFT, MODE_CRON, MODE_MEMO, MODE_ONCE, PAUSED, Run, Schedule
from app.scheduler.cronutil import CronError, iter_occurrences, summarize
from app.scheduler.exclusive import overriding_schedule
from app.scheduler.planner import schedule_window
from app.timeutil import iso_z, local_tz, to_utc_naive, utcnow

router = APIRouter(tags=["agenda"])

# run を作る前の予定（先の日付やドラフト）も、スケジューラから時刻を計算して見せる
PLANNING_STATUSES = (DRAFT, ACTIVE, PAUSED)


def _title(s: Schedule) -> str:
    return s.label or (summarize(s.cron_expr) if s.mode == MODE_CRON else "1回")


@router.get("/api/agenda")
def get_agenda(
    day: date = Query(alias="date"),
    start: str = Query(default="00:00", pattern=r"^\d{1,2}:\d{2}$"),
    db: Session = Depends(get_db),
):
    """1日分（date の start 時刻から24時間。Asia/Tokyo）の run と、まだ run になっていない実行予定、テキストのレーンに書いた予定。

    start を 17:00 にすると、date の 17:00 から翌日の 17:00 まで（夜間の実行を前の日の夕方にまとめて見る）。
    """
    h, m = (int(x) for x in start.split(":"))
    if not (0 <= h < 24 and 0 <= m < 60):
        raise HTTPException(422, "start は 00:00〜23:59 で指定してください")
    lo = to_utc_naive(datetime.combine(day, time(h, m), tzinfo=local_tz()))
    hi = lo + timedelta(days=1)
    last_day = day if (h, m) == (0, 0) else day + timedelta(days=1)  # 範囲にかかる最後の日付
    now = utcnow()

    runs = db.scalars(select(Run).where(Run.scheduled_at >= lo, Run.scheduled_at < hi).order_by(Run.scheduled_at, Run.id)).all()
    have = {(r.schedule_id, r.scheduled_at) for r in runs}

    schedules = db.scalars(
        select(Schedule).where(
            Schedule.status != CANCELLED,
            Schedule.start_date <= last_day,
            (Schedule.end_date.is_(None)) | (Schedule.end_date >= day),
        ).order_by(Schedule.start_date, Schedule.id)
    ).all()

    planned = []
    for s in schedules:
        if s.mode == MODE_MEMO or s.status not in PLANNING_STATUSES:
            continue
        # スケジューラの期間（開始日〜終了日）と、表示する範囲の重なりだけを計算する
        win_start, win_end = schedule_window(s)
        a_, b_ = max(lo, now, win_start), min(hi, win_end) if win_end else hi
        if s.mode == MODE_CRON and s.cron_expr and a_ < b_:
            try:
                times = list(iter_occurrences(s.cron_expr, a_, b_))
            except CronError:
                times = []
        elif s.mode == MODE_ONCE and s.once_at and a_ <= s.once_at < b_:
            times = [s.once_at]
        else:
            times = []
        for t in times:
            if (s.id, t) in have:
                continue
            by = overriding_schedule(s.target.schedules, s, t)
            planned.append({
                "suppressed_by": _title(by) if by else None,
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
    return {"date": day.isoformat(), "start": f"{h:02d}:{m:02d}", "from": iso_z(lo), "to": iso_z(hi),
            "runs": out_runs, "planned": planned, "memos": memos}
