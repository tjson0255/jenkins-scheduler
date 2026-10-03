"""run の状態を「確認してから変える」を1回の UPDATE で行う（compare-and-set）。

API のリクエストと dispatcher は別スレッドで同時に動くので、
「未実行か確認 → 変更」を別々に行うと、間に相手の変更が割り込んで二重キックや上書きが起きる。
ここでは条件付き UPDATE の影響行数で、先に変更できた側だけが成功したと判断する。

キックの確保（claim）:
  status='scheduled' かつ triggered_at IS NULL の run の triggered_at を埋めた側が、その run をキックする権利を持つ。
  確保済みの run は、スキップ・キャンセル・再生成・他のキック処理から触られない。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session

from app import metrics
from app.models import (
    ACTIVE,
    FINISHED_RUN_STATUSES,
    R_HOLDING,
    R_SCHEDULED,
    Run,
    Schedule,
)
from app.timeutil import utcnow

# 未実行で、まだ誰もキック処理に入っていない run
UNCLAIMED_PENDING = or_(
    and_(Run.status == R_SCHEDULED, Run.triggered_at.is_(None)),
    and_(Run.status == R_HOLDING, Run.triggered_at.is_(None)),
)


def transition(
    db: Session,
    run: Run,
    to_status: str,
    reason: str | None = None,
    *,
    condition=UNCLAIMED_PENDING,
    values: dict | None = None,
) -> bool:
    """条件を満たすときだけ状態を変えてコミットする。変えられたら True。"""
    vals: dict = {"status": to_status}
    if reason is not None:
        vals["reason"] = reason
    if to_status in FINISHED_RUN_STATUSES:
        vals["finished_at"] = utcnow()
    vals.update(values or {})
    res = db.execute(
        update(Run).where(Run.id == run.id, condition).values(**vals).execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    ok = res.rowcount == 1
    if ok:
        metrics.observe_run_status(run.target.job_path or "", to_status)
    return ok


def claim(db: Session, run: Run, now: datetime | None = None, *, require_active_schedule: bool = True) -> bool:
    """予定どおりのキック用に run を確保する。スケジュールが有効でなくなっていたら確保しない。"""
    cond = and_(Run.status == R_SCHEDULED, Run.triggered_at.is_(None))
    if require_active_schedule:
        cond = and_(
            cond,
            or_(
                Run.schedule_id.is_(None),
                exists(select(Schedule.id).where(Schedule.id == Run.schedule_id, Schedule.status == ACTIVE)),
            ),
        )
    res = db.execute(
        update(Run).where(Run.id == run.id, cond).values(triggered_at=now or utcnow()).execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    return res.rowcount == 1


def claim_held(db: Session, run: Run, now: datetime | None = None) -> bool:
    """保留解除: holding の run を、そのままキック用に確保する（dispatcher に先に拾われないように）。"""
    res = db.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == R_HOLDING)
        .values(status=R_SCHEDULED, triggered_at=now or utcnow())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    return res.rowcount == 1
