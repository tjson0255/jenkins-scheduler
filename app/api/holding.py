"""保留（holding）の run の件数。画面で目立たせるために、アイテム・スケジューラごとに数える。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import R_HOLDING, Run


def holding_count(db: Session, *, target_id: int | None = None, schedule_id: int | None = None) -> int:
    q = select(func.count()).select_from(Run).where(Run.status == R_HOLDING)
    if target_id is not None:
        q = q.where(Run.target_id == target_id)
    if schedule_id is not None:
        q = q.where(Run.schedule_id == schedule_id)
    return db.scalar(q) or 0
