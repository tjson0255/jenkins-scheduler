from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import get_actor, get_client, get_db, get_dispatcher, get_user, jenkins_http_error, not_found
from app.auth.roles import User
from app.api.concurrency import bump_revision
from app.api.holding import holding_count
from app.api.serializers import run_out, schedule_out
from app.jenkins.base import JenkinsClientProtocol, JenkinsError
from app.models import (
    ACTIVE,
    CANCELLED,
    DRAFT,
    ENDED,
    PAUSED,
    R_CANCELLED,
    R_SCHEDULED,
    ParamOverride,
    Run,
    Schedule,
    Target,
)
from app.scheduler import planner
from app.scheduler.cronutil import CronError, preview, summarize, validate_cron
from app.scheduler.dispatcher import Dispatcher
from app.scheduler.poller import schedule_issues_cached
from app.schema import diff
from app.schema.normalize import default_as_str, kind_of
from app.schema.service import cached_state, evaluate_schedule, fetch_schema, next_pending_run, schedule_context
from app.timeutil import iso_z, local_midnight_utc, local_today, parse_datetime_input, to_local, utcnow

router = APIRouter(tags=["schedules"])

Mode = Literal["cron", "once"]
MissedPolicy = Literal["skip", "run_late"]
SCHEDULE_FIELDS = ("start_date", "end_date", "mode", "cron_expr", "once_at")


class ScheduleIn(BaseModel):
    target_id: int
    label: str | None = Field(default=None, max_length=200)
    start_date: date
    end_date: date | None = None
    mode: Mode = "cron"
    cron_expr: str | None = None
    once_at: str | None = None
    missed_policy: MissedPolicy | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=1440)
    params_pinned: bool = False
    note: str | None = None
    activate: bool = False


class SchedulePatch(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    start_date: date | None = None
    end_date: date | None = None
    mode: Mode | None = None
    cron_expr: str | None = None
    once_at: str | None = None
    missed_policy: MissedPolicy | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=1440)
    params_pinned: bool | None = None
    note: str | None = None
    revision: int | None = None  # 読み込んだ時点の更新番号（同時編集の検出）


class ParamsIn(BaseModel):
    overrides: dict[str, str]
    revision: int | None = None


class CronPreviewIn(BaseModel):
    cron_expr: str
    count: int = Field(default=5, ge=1, le=50)
    start_date: date | None = None  # 指定すると max(現在, 開始日) 以降の予定を返す


# ---------------------------------------------------------------------- helpers
def _get(db: Session, sid: int) -> Schedule:
    s = db.get(Schedule, sid)
    if not s:
        raise not_found("スケジュール")
    return s


def _require_editable(user: User, *, memo: bool) -> None:
    """2. 自由記入のみ編集の人は、自由記入アイテムの予定・メモだけ変更できる（3. 読み取り専用はミドルウェアで拒否済み）。"""
    if not user.is_admin and not (memo and user.can_edit_memo):
        raise HTTPException(403, "自由記入の予定・メモ以外の変更には管理者ログインが必要です")


def _require_jenkins(s: Schedule) -> Schedule:
    """実行に関わる操作（有効化・パラメータ・ドライランなど）は Jenkins アイテムの予定だけ。"""
    if s.is_memo:
        raise HTTPException(400, "自由記入の予定には実行に関する操作はありません")
    return s


def _validate(s: Schedule) -> None:
    if s.end_date and s.end_date < s.start_date:
        raise HTTPException(400, "終了日は開始日以降にしてください")
    if s.is_memo:
        return
    if s.mode == "cron":
        try:
            s.cron_expr = validate_cron(s.cron_expr)
        except CronError as exc:
            raise HTTPException(400, str(exc)) from exc
    elif s.mode == "once":
        if not s.once_at:
            raise HTTPException(400, "1回モードでは実行日時が必要です")
        local_date = to_local(s.once_at).date()
        if local_date < s.start_date or (s.end_date and local_date > s.end_date):
            raise HTTPException(400, "実行日時が期間外です")


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return parse_datetime_input(value)
    except ValueError as exc:
        raise HTTPException(400, f"日時の形式が不正です: {value}") from exc


def _run_counts(db: Session, ids: list[int], frm, to) -> dict[int, dict[str, int]]:
    if not ids:
        return {}
    q = select(Run.schedule_id, Run.status, func.count()).where(Run.schedule_id.in_(ids))
    if frm is not None:
        q = q.where(Run.scheduled_at >= frm)
    if to is not None:
        q = q.where(Run.scheduled_at < to)
    out: dict[int, dict[str, int]] = {}
    for sid, status, n in db.execute(q.group_by(Run.schedule_id, Run.status)):
        out.setdefault(sid, {})[status] = n
    return out


def _out(db: Session, s: Schedule, run_counts=None) -> dict:
    issues = schedule_issues_cached(db, s) if s.status in (DRAFT, ACTIVE, PAUSED) else []
    nxt = next_pending_run(db, s)
    return schedule_out(s, issues, run_counts, nxt.scheduled_at if nxt else None, holding_count(db, schedule_id=s.id))


def _snapshot(s: Schedule) -> dict:
    return {
        "label": s.label,
        "start_date": s.start_date.isoformat(),
        "end_date": s.end_date.isoformat() if s.end_date else None,
        "mode": s.mode,
        "cron_expr": s.cron_expr,
        "once_at": iso_z(s.once_at),
        "status": s.status,
    }


def _activate(db: Session, s: Schedule, client: JenkinsClientProtocol, dispatcher: Dispatcher) -> list[dict]:
    """有効化（承認）。スキーマを取り直し、error があれば有効化しない。"""
    try:
        state = fetch_schema(db, client, s.target)
    except JenkinsError as exc:
        raise jenkins_http_error(exc) from exc
    nxt = next_pending_run(db, s)
    at = nxt.scheduled_at if nxt else utcnow()
    _params, detail, issues = evaluate_schedule(db, s, state, at)
    errors = [i for i in issues if i["level"] == diff.ERROR]
    if errors:
        db.commit()  # スキーマ取得結果は保存する
        raise HTTPException(409, {"message": "検証エラーがあるため有効化できません", "issues": errors})
    if s.baseline_schema_hash is None:
        s.baseline_schema_hash = state.hash
    if s.params_pinned:
        s.pinned_params_json = {name: d["template"] for name, d in detail.items()}
    s.status = ACTIVE
    planner.fill_runs(db, s, utcnow(), dispatcher.settings.run_horizon_days)
    return issues


# ---------------------------------------------------------------------- CRUD
@router.get("/api/schedules")
def list_schedules(
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    target_id: int | None = None,
    include_cancelled: bool = False,
    db: Session = Depends(get_db),
):
    q = select(Schedule)
    if to is not None:
        q = q.where(Schedule.start_date <= to)
    if from_ is not None:
        q = q.where((Schedule.end_date.is_(None)) | (Schedule.end_date >= from_))
    if target_id is not None:
        q = q.where(Schedule.target_id == target_id)
    if not include_cancelled:
        q = q.where(Schedule.status != CANCELLED)
    schedules = db.scalars(q.order_by(Schedule.start_date, Schedule.id)).all()
    frm_dt = local_midnight_utc(from_) if from_ else None
    to_dt = local_midnight_utc(to + timedelta(days=1)) if to else None
    counts = _run_counts(db, [s.id for s in schedules], frm_dt, to_dt)
    return [_out(db, s, counts.get(s.id, {})) for s in schedules]


@router.get("/api/schedules/{sid}")
def get_schedule(sid: int, db: Session = Depends(get_db)):
    s = _get(db, sid)
    return _out(db, s, _run_counts(db, [s.id], None, None).get(s.id, {}))


@router.post("/api/schedules", status_code=201)
def create_schedule(
    body: ScheduleIn,
    db: Session = Depends(get_db),
    client: JenkinsClientProtocol = Depends(get_client),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
    user: User = Depends(get_user),
):
    t = db.get(Target, body.target_id)
    if not t:
        raise HTTPException(400, "アイテムが見つかりません")
    _require_editable(user, memo=t.is_memo)
    st = dispatcher.settings
    if t.is_memo:
        # 自由記入: タイトル・期間・メモだけ。run は作らず、作った時点でタイムラインに出す
        s = Schedule(target=t, label=body.label or None, start_date=body.start_date, end_date=body.end_date,
                     mode="memo", status=ACTIVE, note=body.note)
        _validate(s)
        db.add(s)
        db.flush()
        audit.record(db, actor, "schedule.create", "schedule", s.id, {"item": t.display_name, **_snapshot(s)})
        db.commit()
        return _out(db, s)
    s = Schedule(
        target=t,
        label=body.label or None,
        start_date=body.start_date,
        end_date=body.end_date,
        mode=body.mode,
        cron_expr=body.cron_expr if body.mode == "cron" else None,
        once_at=_parse_dt(body.once_at) if body.mode == "once" else None,
        status=DRAFT,
        params_pinned=body.params_pinned,
        missed_policy=body.missed_policy or st.default_missed_policy,
        grace_minutes=body.grace_minutes if body.grace_minutes is not None else st.default_grace_minutes,
        note=body.note,
        baseline_schema_hash=t.schema_hash,
    )
    _validate(s)
    db.add(s)
    db.flush()
    planner.fill_runs(db, s, utcnow(), st.run_horizon_days)
    audit.record(db, actor, "schedule.create", "schedule", s.id, {"target": t.job_path, **_snapshot(s)})
    if body.activate:
        _activate(db, s, client, dispatcher)
        audit.record(db, actor, "schedule.activate", "schedule", s.id, None)
    db.commit()
    return _out(db, s)


@router.patch("/api/schedules/{sid}")
def update_schedule(
    sid: int,
    body: SchedulePatch,
    db: Session = Depends(get_db),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
    user: User = Depends(get_user),
):
    s = _get(db, sid)
    _require_editable(user, memo=s.is_memo)
    if s.status == CANCELLED:
        raise HTTPException(409, "キャンセル済みのスケジュールは変更できません")
    bump_revision(db, Schedule, s.id, body.revision)
    before = _snapshot(s)
    changes = body.model_dump(exclude_unset=True, exclude={"revision"})
    if s.is_memo:
        # 自由記入はタイトル・期間・メモだけ変更できる
        changes = {k: v for k, v in changes.items() if k in ("label", "start_date", "end_date", "note")}
    timing_changed = False
    for k, v in changes.items():
        if k == "once_at":
            v = _parse_dt(v)
        if k in ("start_date", "mode") and v is None:
            continue
        if getattr(s, k) != v:
            setattr(s, k, v)
            if k in SCHEDULE_FIELDS:
                timing_changed = True
    if s.mode != "cron":
        s.cron_expr = None
    if s.mode != "once":
        s.once_at = None
    _validate(s)
    if not s.is_memo and s.status == ENDED and (s.end_date is None or s.end_date >= local_today()):
        # 終了済みを延長した場合は改めて承認（有効化）してもらう
        s.status = DRAFT
    regenerated = 0
    if timing_changed and not s.is_memo:
        regenerated = planner.regenerate_runs(db, s, utcnow(), dispatcher.settings.run_horizon_days)
    audit.record(
        db, actor, "schedule.update", "schedule", s.id,
        {"before": before, "after": _snapshot(s), "changes": {k: str(v) for k, v in changes.items()}, "regenerated_runs": regenerated},
    )
    db.commit()
    return _out(db, s)


@router.delete("/api/schedules/{sid}", status_code=204)
def delete_schedule(sid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor), user: User = Depends(get_user)):
    s = _get(db, sid)
    _require_editable(user, memo=s.is_memo)
    if s.status != DRAFT and not s.is_memo:
        raise HTTPException(409, "削除できるのはドラフトのみです（有効化済みはキャンセルしてください）")
    audit.record(db, actor, "schedule.delete", "schedule", s.id, _snapshot(s))
    db.delete(s)
    db.commit()


# ---------------------------------------------------------------------- 状態遷移
@router.post("/api/schedules/{sid}/activate")
def activate_schedule(
    sid: int,
    db: Session = Depends(get_db),
    client: JenkinsClientProtocol = Depends(get_client),
    dispatcher: Dispatcher = Depends(get_dispatcher),
    actor: str = Depends(get_actor),
):
    s = _require_jenkins(_get(db, sid))
    bump_revision(db, Schedule, s.id, None)
    if s.status != DRAFT:
        raise HTTPException(409, f"有効化できるのはドラフトのみです（現在: {s.status}）")
    issues = _activate(db, s, client, dispatcher)
    audit.record(db, actor, "schedule.activate", "schedule", s.id, {"warnings": [i["message"] for i in issues if i["level"] == "warning"]})
    db.commit()
    return _out(db, s)


def _transition(db: Session, s: Schedule, allowed: tuple[str, ...], new: str, actor: str, action: str) -> dict:
    _require_jenkins(s)
    bump_revision(db, Schedule, s.id, None)
    if s.status not in allowed:
        raise HTTPException(409, f"現在の状態（{s.status}）からは操作できません")
    s.status = new
    detail = None
    if new == CANCELLED:
        n = planner.cancel_pending_runs(db, s, R_CANCELLED, "スケジュールのキャンセル")
        detail = {"cancelled_runs": n}
    audit.record(db, actor, action, "schedule", s.id, detail)
    db.commit()
    return _out(db, s)


@router.post("/api/schedules/{sid}/pause")
def pause_schedule(sid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _transition(db, _get(db, sid), (ACTIVE,), PAUSED, actor, "schedule.pause")


@router.post("/api/schedules/{sid}/resume")
def resume_schedule(sid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _transition(db, _get(db, sid), (PAUSED,), ACTIVE, actor, "schedule.resume")


@router.post("/api/schedules/{sid}/cancel")
def cancel_schedule(sid: int, db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _transition(db, _get(db, sid), (DRAFT, ACTIVE, PAUSED), CANCELLED, actor, "schedule.cancel")


# ---------------------------------------------------------------------- パラメータ
def _params_view(db: Session, s: Schedule, client: JenkinsClientProtocol, *, fresh: bool = True) -> dict:
    """fresh=False のときは Jenkins に問い合わせず、最後に取得した定義（5分ごとに更新）で表示する。"""
    _require_jenkins(s)
    if fresh:
        try:
            state = fetch_schema(db, client, s.target)
        except JenkinsError as exc:
            raise jenkins_http_error(exc) from exc
    else:
        state = cached_state(db, s.target)
        state.error = s.target.schema_error
    nxt = next_pending_run(db, s)
    at = nxt.scheduled_at if nxt else utcnow()
    params, detail, issues = evaluate_schedule(db, s, state, at)
    fields = []
    for d in state.defs:
        fields.append(
            {
                "name": d["name"],
                "type": d["type"],
                "kind": kind_of(d["type"]),
                "default": default_as_str(d.get("default")),
                "choices": d.get("choices"),
                "description": state.descriptions.get(d["name"], ""),
                "override": s.override_map().get(d["name"]),
                "pinned": (s.pinned_params_json or {}).get(d["name"]) if s.params_pinned else None,
                "preview": detail.get(d["name"], {}).get("value"),
                "source": detail.get(d["name"], {}).get("source"),
            }
        )
    db.commit()
    return {
        "schema_hash": state.hash,
        "baseline_schema_hash": s.baseline_schema_hash,
        "job_error": state.error,
        "fields": fields,
        "orphan_overrides": {k: v for k, v in s.override_map().items() if k not in detail},
        "params": params,
        "issues": issues,
        "context": schedule_context(s, at),
        "context_at": iso_z(at),
        "fresh": fresh,
        "fetched_at": iso_z(s.target.last_synced_at),
    }


@router.get("/api/schedules/{sid}/params")
def get_params(sid: int, db: Session = Depends(get_db), client: JenkinsClientProtocol = Depends(get_client), user: User = Depends(get_user)):
    # Jenkins から取り直すのは管理者が開いたときだけ（閲覧者が何人開いても Jenkins を呼ばない）
    return _params_view(db, _get(db, sid), client, fresh=user.is_admin)


@router.put("/api/schedules/{sid}/params")
def put_params(
    sid: int,
    body: ParamsIn,
    db: Session = Depends(get_db),
    client: JenkinsClientProtocol = Depends(get_client),
    actor: str = Depends(get_actor),
):
    s = _require_jenkins(_get(db, sid))
    bump_revision(db, Schedule, s.id, body.revision)
    before = s.override_map()
    s.overrides.clear()
    db.flush()
    for name, tmpl in body.overrides.items():
        s.overrides.append(ParamOverride(param_name=name, value_template=tmpl))
    try:
        state = fetch_schema(db, client, s.target)
    except JenkinsError as exc:
        raise jenkins_http_error(exc) from exc
    # 保存した時点のスキーマを基準にする（追加・削除などの「要確認」を解消する）
    s.baseline_schema_hash = state.hash
    if s.params_pinned and s.status in (ACTIVE, PAUSED):
        _p, detail, _i = evaluate_schedule(db, s, state, utcnow())
        s.pinned_params_json = {name: d["template"] for name, d in detail.items()}
    audit.record(db, actor, "schedule.params", "schedule", s.id, {"before": before, "after": body.overrides})
    db.commit()
    return _params_view(db, s, client)


@router.post("/api/schedules/{sid}/dry-run")
def dry_run(
    sid: int,
    db: Session = Depends(get_db),
    client: JenkinsClientProtocol = Depends(get_client),
):
    """キックせずに、次回 run の展開後パラメータと検証結果を返す。"""
    s = _require_jenkins(_get(db, sid))
    try:
        state = fetch_schema(db, client, s.target)
    except JenkinsError as exc:
        raise jenkins_http_error(exc) from exc
    nxt = next_pending_run(db, s)
    at = nxt.scheduled_at if nxt else utcnow()
    params, detail, issues = evaluate_schedule(db, s, state, at)
    info = state.info or {}
    last_build = info.get("lastBuild") or {}
    busy = bool(info.get("inQueue") or last_build.get("building"))
    errors = [i for i in issues if i["level"] == diff.ERROR]
    if s.status != ACTIVE:
        verdict = f"スケジュールが {s.status} のため実行されません"
    elif errors:
        verdict = "検証エラーのため holding になります"
    elif busy and s.target.overlap_policy == "skip":
        verdict = "現在ビルド中/キュー中のため、今の状態だと skipped になります"
    else:
        verdict = "キックされます"
    db.commit()
    return {
        "run_id": nxt.id if nxt else None,
        "scheduled_at": iso_z(at),
        "endpoint": "buildWithParameters" if state.has_params else "build",
        "params": params,
        "detail": detail,
        "issues": issues,
        "busy": busy,
        "would_kick": s.status == ACTIVE and not errors and not (busy and s.target.overlap_policy == "skip"),
        "verdict": verdict,
    }


WEEKDAYS_JA = "月火水木金土日"


def _fmt_local(t) -> str:
    lt = to_local(t)
    return f"{lt:%Y-%m-%d}（{WEEKDAYS_JA[lt.weekday()]}）{lt:%H:%M}"


@router.post("/api/cron/preview")
def cron_preview(body: CronPreviewIn):
    try:
        start = utcnow()
        if body.start_date:
            start = max(start, local_midnight_utc(body.start_date))
        times = preview(body.cron_expr, body.count, start - timedelta(seconds=1))
    except CronError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "cron_expr": body.cron_expr.strip(),
        "summary": summarize(body.cron_expr.strip()),
        "times": [iso_z(t) for t in times],
        "times_local": [_fmt_local(t) for t in times],
    }
