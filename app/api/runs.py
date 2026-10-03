from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import get_actor, get_db, get_dispatcher, jenkins_http_error, not_found
from app.api.serializers import run_out
from app.jenkins.base import JenkinsError
from app.models import (
    R_CANCELLED,
    R_HOLDING,
    R_SCHEDULED,
    R_SKIPPED,
    Run,
)
from app.scheduler import runstate
from app.scheduler.dispatcher import Dispatcher

RUN_STATE_LABEL = {
    "scheduled": "予定", "holding": "保留", "queued": "キュー", "running": "実行中", "success": "成功",
    "unstable": "不安定", "failure": "失敗", "aborted": "中断", "skipped": "スキップ", "missed": "見逃し", "cancelled": "キャンセル",
}
from app.timeutil import local_midnight_utc, utcnow

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _get(db: Session, rid: int) -> Run:
    r = db.get(Run, rid)
    if not r:
        raise not_found("run")
    return r


@router.get("/holding-count")
def get_holding_count(db: Session = Depends(get_db)):
    """画面上部のバッジ用: 保留中の run の件数。"""
    from app.api.holding import holding_count

    return {"holding": holding_count(db)}


@router.get("")
def list_runs(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    target: int | None = None,
    schedule_id: int | None = None,
    status: str | None = None,
    order: str | None = Query(default=None, pattern="^(asc|desc)$"),
    limit: int = Query(default=2000, le=10000),
    db: Session = Depends(get_db),
):
    q = select(Run)
    if from_:
        q = q.where(Run.scheduled_at >= local_midnight_utc(from_))
    if to:
        q = q.where(Run.scheduled_at < local_midnight_utc(to + timedelta(days=1)))
    if target:
        q = q.where(Run.target_id == target)
    if schedule_id:
        q = q.where(Run.schedule_id == schedule_id)
    if status:
        q = q.where(Run.status.in_(status.split(",")))
    desc = order == "desc" if order else bool(schedule_id)
    sort = Run.scheduled_at.desc() if desc else Run.scheduled_at
    return [run_out(r) for r in db.scalars(q.order_by(sort).limit(limit))]


@router.post("/{rid}/release-hold")
def release_hold(
    rid: int,
    db: Session = Depends(get_db),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
):
    """保留解除: 再検証し、問題が無ければ即時キックする。"""
    r = _get(db, rid)
    previous_reason = r.reason
    # 保留 → キック用に確保、を1回の更新で行う（dispatcher に先に拾われて見逃し・二重キックにならないように）
    if not runstate.claim_held(db, r, utcnow()):
        raise HTTPException(409, f"保留中ではないため保留解除できません（現在: {r.status}）。画面を更新してください")
    audit.record(db, actor, "run.release_hold", "run", r.id, {"reason": previous_reason})
    db.commit()
    try:
        dispatcher.kick_now(db, r)
    except JenkinsError as exc:
        raise jenkins_http_error(exc) from exc
    db.refresh(r)
    if r.status == R_HOLDING:
        raise HTTPException(409, {"message": "再検証でもエラーのため保留のままです", "reason": r.reason, "run": run_out(r)})
    return run_out(r)


@router.post("/{rid}/skip")
def skip_run(rid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    r = _get(db, rid)
    if not runstate.transition(db, r, R_SKIPPED, "手動でスキップ"):
        raise HTTPException(409, f"未実行の run のみスキップできます（現在: {RUN_STATE_LABEL.get(r.status, r.status)}{'・キック処理中' if r.status == 'scheduled' else ''}）")
    audit.record(db, actor, "run.skip", "run", r.id, None)
    db.commit()
    return run_out(r)


@router.post("/{rid}/cancel")
def cancel_run(rid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    r = _get(db, rid)
    if not runstate.transition(db, r, R_CANCELLED, "手動でキャンセル"):
        raise HTTPException(409, f"未実行の run のみキャンセルできます（現在: {RUN_STATE_LABEL.get(r.status, r.status)}{'・キック処理中' if r.status == 'scheduled' else ''}）")
    audit.record(db, actor, "run.cancel", "run", r.id, None)
    db.commit()
    return run_out(r)


@router.post("/{rid}/retry", status_code=201)
def retry_run(
    rid: int,
    db: Session = Depends(get_db),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
):
    """同じパラメータで新しい run を作って即時キックする（元の run は変更しない）。"""
    src = _get(db, rid)
    if src.params_json is None:
        raise HTTPException(409, "送信したパラメータの記録が無いため再実行できません")
    run = Run(
        schedule_id=None,
        target_id=src.target_id,
        scheduled_at=utcnow(),
        status=R_SCHEDULED,
        params_json=dict(src.params_json),
        retry_of_id=src.id,
        reason=f"run #{src.id} の再実行",
        triggered_at=utcnow(),  # 作った時点で確保済みにして、dispatcher に拾われないようにする
    )
    db.add(run)
    db.flush()
    audit.record(db, actor, "run.retry", "run", run.id, {"retry_of": src.id})
    db.commit()
    try:
        dispatcher.kick_now(db, run)
    except JenkinsError as exc:
        raise jenkins_http_error(exc) from exc
    db.refresh(run)
    return run_out(run)

