"""実行履歴に、実行した時点のスケジューラの件名・メモを残す。

日によって件名やパラメータの違うスケジューラを同じレーンで動かすため、
スケジューラをあとから変更・削除しても、過去の回が「何を・どの内容で」実行したかが分かるようにする。
（送ったパラメータは、キックした時点で run.params_json に残している）
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import PENDING_RUN_STATUSES, Run, Schedule
from app.scheduler.cronutil import summarize


def schedule_title(s: Schedule) -> str:
    return s.label or (summarize(s.cron_expr) if s.mode == "cron" else "1回")


def stamp_run(run: Run, s: Schedule) -> None:
    """その回に、今のスケジューラの件名・メモを書き込む（キックするときに呼ぶ）。"""
    run.title_snapshot = schedule_title(s)[:200]
    run.note_snapshot = s.note


def snapshot_past_runs(db: Session, s: Schedule) -> None:
    """スケジューラを変更・削除する前に、まだ件名・メモを残していない過去の回に、今の内容を書き込む。"""
    db.execute(
        update(Run)
        .where(Run.schedule_id == s.id, Run.title_snapshot.is_(None), Run.status.notin_(PENDING_RUN_STATUSES))
        .values(title_snapshot=schedule_title(s)[:200], note_snapshot=s.note)
        .execution_options(synchronize_session=False)
    )


def detach_past_runs(db: Session, s: Schedule) -> int:
    """スケジューラを削除するとき、実行済みの回を履歴として残す（スケジューラとのつながりだけ外す）。残した件数を返す。"""
    snapshot_past_runs(db, s)
    res = db.execute(
        update(Run)
        .where(Run.schedule_id == s.id, Run.status.notin_(PENDING_RUN_STATUSES))
        .values(schedule_id=None)
        .execution_options(synchronize_session=False)
    )
    db.expire(s, ["runs"])
    return res.rowcount
