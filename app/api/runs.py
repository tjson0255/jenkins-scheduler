from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import audit, metrics
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
from app.schema.normalize import kind_of
from app.schema.service import cached_state, evaluate_schedule
from app.schema.validate import apply_run_overrides, check_value
from app.timeutil import iso_z, local_midnight_utc, parse_datetime_input, to_local, utcnow

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



# ---------------------------------------------------------------------- その回だけの変更（置き換え）
REPLACED_PREFIX = "この回だけ変更"


class ReplaceIn(BaseModel):
    scheduled_at: str  # 変更後の日時（タイムゾーン無しは Asia/Tokyo）
    params: dict[str, Any] = Field(default_factory=dict)  # その回だけのパラメータ（スケジューラの値と違うものだけ保存する）


def _md_hm(dt) -> str:
    local = to_local(dt)
    return f"{local.month}/{local.day} {local.hour:02d}:{local.minute:02d}"


def _schedule_params(db: Session, r: Run, at):
    """スケジューラの設定から、その時刻の回に送るパラメータを展開する（Jenkins には問い合わせない）。"""
    s = r.schedule
    if s is None or s.is_memo:
        raise HTTPException(400, "スケジューラの回だけが対象です")
    state = cached_state(db, s.target)
    params, _detail, _issues = evaluate_schedule(db, s, state, at)
    return state, params


@router.get("/{rid}/params")
def run_params(rid: int, db: Session = Depends(get_db)):
    """その回に送るパラメータ（その回だけの変更を含む）。「この回だけ変更」の入力欄に使う。"""
    r = _get(db, rid)
    state, params = _schedule_params(db, r, r.scheduled_at)
    if r.override_params:
        params, _ = apply_run_overrides(state.defs, params, r.override_params)
    overridden = set((r.override_params or {}).keys())
    out = {
        "run_id": r.id,
        "scheduled_at": iso_z(r.scheduled_at),
        "fields": [
            {"name": d["name"], "kind": kind_of(d.get("type")), "choices": d.get("choices"),
             "value": params.get(d["name"], ""), "overridden": d["name"] in overridden}
            for d in state.defs
        ],
    }
    db.rollback()  # 展開のためにセッションへ読み込んだものは書き込まない
    return out


@router.post("/{rid}/replace")
def replace_run(
    rid: int,
    body: ReplaceIn,
    db: Session = Depends(get_db),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
):
    """この回だけ、日時やパラメータを変える。スケジューラの設定は変えない。

    元の回をスキップし、変更した内容の回を同じスケジューラに作る（1回の操作で両方行い、片方だけにはならない）。
    すでに置き換えた回に対して行うと、その回の日時・パラメータを変える。
    dispatcher のキック処理と同時に進まないよう、dispatcher のロックの中で行う。
    """
    with dispatcher.lock:
        r = _get(db, rid)
        s = r.schedule
        if s is None or s.is_memo:
            raise HTTPException(400, "スケジューラの回だけが対象です")
        try:
            new_at = parse_datetime_input(body.scheduled_at).replace(second=0, microsecond=0)
        except ValueError as exc:
            raise HTTPException(400, f"日時が不正です: {body.scheduled_at}") from exc
        if new_at <= utcnow():
            raise HTTPException(400, "過去の日時には変更できません")
        clash = db.scalar(select(Run.id).where(Run.schedule_id == s.id, Run.scheduled_at == new_at, Run.id != r.id))
        if clash:
            raise HTTPException(409, f"{_md_hm(new_at)} にはこのスケジューラの回がすでにあります。別の時刻にしてください")

        state, base = _schedule_params(db, r, new_at)
        by_name = {d["name"]: d for d in state.defs}
        overrides: dict[str, str] = {}
        for name, value in body.params.items():
            if name not in by_name:
                raise HTTPException(400, f"パラメータ {name} は Jenkins にありません")
            value = str(value)
            errors = [i["message"] for i in check_value(by_name[name], value)]
            if errors:
                raise HTTPException(400, errors[0])
            if value != base.get(name):
                overrides[name] = value

        if r.replaces_run_id:
            # 置き換えた回をもう一度変える（キック処理に入っていなければ）
            res = db.execute(
                update(Run).where(Run.id == r.id, runstate.UNCLAIMED_PENDING)
                .values(scheduled_at=new_at, override_params=overrides or None)
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:
                db.rollback()
                raise HTTPException(409, "キック処理に入ったため変更できません。画面を更新してください")
            audit.record(db, actor, "run.replace_update", "run", r.id, {"scheduled_at": iso_z(new_at), "params": overrides})
            db.commit()
            db.refresh(r)
            return run_out(r)

        # 元の回のスキップと、置き換えの回の作成を、同じトランザクションで行う
        res = db.execute(
            update(Run).where(Run.id == r.id, runstate.UNCLAIMED_PENDING)
            .values(status=R_SKIPPED, reason=f"{REPLACED_PREFIX} → {_md_hm(new_at)}", finished_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            db.rollback()
            raise HTTPException(409, f"未実行の回だけ変更できます（現在: {RUN_STATE_LABEL.get(r.status, r.status)}）。画面を更新してください")
        new = Run(
            schedule_id=s.id, target_id=r.target_id, scheduled_at=new_at, status=R_SCHEDULED,
            replaces_run_id=r.id, override_params=overrides or None,
            reason=f"{_md_hm(r.scheduled_at)} の回から変更",
        )
        db.add(new)
        db.flush()
        audit.record(db, actor, "run.replace", "run", r.id,
                     {"replacement_run": new.id, "from": iso_z(r.scheduled_at), "to": iso_z(new_at), "params": overrides})
        db.commit()
        metrics.observe_run_status(s.target.job_path or "", R_SKIPPED)
        db.refresh(new)
        return run_out(new)


@router.post("/{rid}/unreplace")
def unreplace_run(
    rid: int,
    db: Session = Depends(get_db),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
):
    """その回だけの変更を取り消す。置き換えの回を消し、元の回を「予定」に戻す。

    元の回の時刻を過ぎている場合は、遅れてキックされないよう、元の回はスキップのままにする。
    """
    with dispatcher.lock:
        r = _get(db, rid)
        if not r.replaces_run_id:
            raise HTTPException(400, "この回だけ変更した回ではありません")
        original = db.get(Run, r.replaces_run_id)
        res = db.execute(delete(Run).where(Run.id == r.id, runstate.UNCLAIMED_PENDING).execution_options(synchronize_session=False))
        if res.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "キック済み・キック処理中のため取り消せません")
        restored = False
        if (original is not None and original.status == R_SKIPPED and (original.reason or "").startswith(REPLACED_PREFIX)
                and original.scheduled_at > utcnow()):
            db.execute(
                update(Run).where(Run.id == original.id, Run.status == R_SKIPPED)
                .values(status=R_SCHEDULED, reason=None, finished_at=None)
                .execution_options(synchronize_session=False)
            )
            restored = True
        audit.record(db, actor, "run.unreplace", "run", r.id, {"original_run": r.replaces_run_id, "restored": restored})
        db.commit()
        return {"deleted_run_id": rid, "original_run_id": r.replaces_run_id, "restored": restored}
