from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import get_actor, get_client, get_db, get_dispatcher, get_user, jenkins_http_error, not_found
from app.auth.roles import User
from app.api.concurrency import bump_revision
from app.api.holding import holding_count
from app.api.serializers import run_out, target_out
from app.jenkins.base import JenkinsClientProtocol, JenkinsError
from app.models import ACTIVE, ITEM_JENKINS, ITEM_MEMO, R_SCHEDULED, Category, Run, Schedule, Target
from app.scheduler.dispatcher import Dispatcher
from app.scheduler.poller import has_pending_runs, schedule_issues_cached, sync_target
from app.schema.service import evaluate_schedule, fetch_schema, snapshot_defs
from app.timeutil import utcnow

router = APIRouter(tags=["targets"])


class TargetIn(BaseModel):
    kind: Literal["jenkins", "memo"] = ITEM_JENKINS
    job_path: str | None = Field(default=None, max_length=500)  # Jenkins アイテムのみ
    display_name: str | None = Field(default=None, max_length=200)
    category_id: int | None = None
    pinned: bool = False
    color: str | None = None
    overlap_policy: Literal["skip", "queue"] | None = None
    enabled: bool = True
    note: str | None = Field(default=None, max_length=4000)


class TargetPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    category_id: int | None = None
    pinned: bool | None = None
    sort_order: int | None = None
    color: str | None = None
    overlap_policy: Literal["skip", "queue"] | None = None
    enabled: bool | None = None
    note: str | None = Field(default=None, max_length=4000)
    revision: int | None = None  # 読み込んだ時点の更新番号（同時編集の検出）


class RunNowIn(BaseModel):
    schedule_id: int | None = None
    params: dict[str, Any] | None = None


def _issue_counts(db: Session, t: Target) -> dict[str, int]:
    counts = {"error": 0, "warning": 0}
    for s in t.schedules:
        if not has_pending_runs(db, s):
            continue
        for i in schedule_issues_cached(db, s):
            if i["level"] in counts:
                counts[i["level"]] += 1
    return counts


def _out(db: Session, t: Target) -> dict:
    if t.is_memo:
        return target_out(t, None, {})
    defs = snapshot_defs(db, t.job_path, t.schema_hash)
    return target_out(t, len(defs) if defs is not None else None, _issue_counts(db, t), holding_count(db, target_id=t.id))


@router.get("/api/jenkins/jobs")
def search_jobs(q: str | None = None, client: JenkinsClientProtocol = Depends(get_client), user: User = Depends(get_user)):
    # アイテム登録用。Jenkins のフォルダをすべてたどる重い呼び出しなので管理者だけ
    if not user.is_admin:
        raise HTTPException(403, "ジョブの検索には管理者ログインが必要です")
    try:
        return client.search_jobs(q)[:200]
    except JenkinsError as exc:
        raise jenkins_http_error(exc) from exc


@router.get("/api/targets")
def list_targets(db: Session = Depends(get_db)):
    targets = db.scalars(
        select(Target).join(Category).order_by(Category.sort_order, Target.sort_order, Target.id)
    ).all()
    return [_out(db, t) for t in targets]


@router.post("/api/targets", status_code=201)
def create_target(
    body: TargetIn,
    request_dispatcher: Dispatcher = Depends(get_dispatcher),
    db: Session = Depends(get_db),
    client: JenkinsClientProtocol = Depends(get_client),
    actor: str = Depends(get_actor),
):
    if body.kind == ITEM_MEMO:
        job_path = None
        if not (body.display_name or "").strip():
            raise HTTPException(400, "自由記入のアイテムには名前を入れてください")
    else:
        job_path = (body.job_path or "").strip().strip("/")
        if not job_path:
            raise HTTPException(400, "ジョブのパスを入力してください")
        if db.scalars(select(Target).where(Target.job_path == job_path)).first():
            raise HTTPException(409, "このジョブは登録済みです")
    if body.category_id is None:
        cat = db.scalars(select(Category).order_by(Category.sort_order.desc())).first()
    else:
        cat = db.get(Category, body.category_id)
    if not cat:
        raise HTTPException(400, "カテゴリが見つかりません")
    order = (db.scalar(select(func.max(Target.sort_order)).where(Target.category_id == cat.id)) or 0) + 1
    t = Target(
        kind=body.kind,
        job_path=job_path,
        display_name=(body.display_name or "").strip() or job_path.split("/")[-1],
        category_id=cat.id,
        pinned=body.pinned,
        color=body.color,
        overlap_policy=body.overlap_policy or request_dispatcher.settings.default_overlap_policy,
        enabled=body.enabled,
        note=body.note,
        sort_order=order,
    )
    db.add(t)
    db.flush()
    if not t.is_memo:
        # Jenkins アイテムは登録時にジョブの存在とパラメータ定義を確認する
        try:
            state = fetch_schema(db, client, t)
        except JenkinsError as exc:
            db.rollback()
            raise jenkins_http_error(exc) from exc
        if state.error:
            db.rollback()
            raise HTTPException(400, state.error)
    audit.record(db, actor, "target.create", "target", t.id, {"kind": t.kind, "job_path": t.job_path, "name": t.display_name})
    db.commit()
    return _out(db, t)


@router.patch("/api/targets/{tid}")
def update_target(tid: int, body: TargetPatch, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    t = db.get(Target, tid)
    if not t:
        raise not_found("アイテム")
    bump_revision(db, Target, t.id, body.revision)
    changes = body.model_dump(exclude_unset=True, exclude={"revision"})
    if "category_id" in changes and not db.get(Category, changes["category_id"]):
        raise HTTPException(400, "カテゴリが見つかりません")
    for k, v in changes.items():
        if v is None and k not in ("color", "note"):
            continue
        setattr(t, k, v)
    audit.record(db, actor, "target.update", "target", t.id, changes)
    db.commit()
    return _out(db, t)


@router.delete("/api/targets/{tid}", status_code=204)
def delete_target(tid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    t = db.get(Target, tid)
    if not t:
        raise not_found("アイテム")
    if any(s.status == ACTIVE for s in t.schedules):
        raise HTTPException(409, "有効なスケジュールがあるため削除できません")
    for r in db.scalars(select(Run).where(Run.target_id == t.id)):
        db.delete(r)
    audit.record(db, actor, "target.delete", "target", t.id, {"job_path": t.job_path})
    db.delete(t)
    db.commit()


@router.post("/api/targets/sync")
def sync_targets(
    db: Session = Depends(get_db),
    client: JenkinsClientProtocol = Depends(get_client),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
):
    results = []
    for t in db.scalars(select(Target).where(Target.kind == ITEM_JENKINS)).all():
        try:
            results.append(sync_target(db, client, t, dispatcher.settings.timer_trigger_lookback_days))
            db.commit()
        except JenkinsError as exc:
            db.rollback()
            results.append({"target_id": t.id, "job_path": t.job_path, "error": str(exc)})
    audit.record(db, actor, "target.sync", "target", None, {"count": len(results)})
    db.commit()
    return results


@router.post("/api/targets/{tid}/run-now", status_code=201)
def run_now(
    tid: int,
    body: RunNowIn,
    db: Session = Depends(get_db),
    client: JenkinsClientProtocol = Depends(get_client),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
):
    """即時キック。schedule_id を渡すとそのスケジュールのパラメータで実行する。"""
    t = db.get(Target, tid)
    if not t:
        raise not_found("アイテム")
    if t.is_memo:
        raise HTTPException(400, "自由記入のアイテムは実行できません")
    now = utcnow()
    params: dict[str, str] = {k: str(v) for k, v in (body.params or {}).items()}
    if body.schedule_id is not None:
        s = db.get(Schedule, body.schedule_id)
        if not s or s.target_id != t.id:
            raise HTTPException(400, "スケジュールが見つかりません")
        try:
            state = fetch_schema(db, client, t)
        except JenkinsError as exc:
            raise jenkins_http_error(exc) from exc
        rendered, _detail, _issues = evaluate_schedule(db, s, state, now)
        rendered.update(params)
        params = rendered
    # 作った時点で確保済み（triggered_at）にして、dispatcher に拾われて二重キックにならないようにする
    run = Run(schedule_id=None, target_id=t.id, scheduled_at=now, status=R_SCHEDULED, params_json=params, triggered_at=now,
              reason=f"スケジュール #{body.schedule_id} のパラメータで即時実行" if body.schedule_id else "即時実行")
    db.add(run)
    db.flush()
    audit.record(db, actor, "run.run_now", "run", run.id, {"target": t.job_path, "schedule_id": body.schedule_id, "params": params})
    db.commit()
    try:
        dispatcher.kick_now(db, run)
    except JenkinsError as exc:
        raise jenkins_http_error(exc) from exc
    db.refresh(run)
    return run_out(run)
