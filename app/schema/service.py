"""スキーマの取得・保存と、スケジュールに対する検証結果の組み立て。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jenkins.base import JenkinsClientProtocol, JobNotFound, parameter_definitions
from app.models import R_SCHEDULED, Run, Schedule, SchemaSnapshot, Target
from app.schema import diff
from app.schema.normalize import descriptions, normalize, schema_hash
from app.schema.validate import build_context, resolve_params
from app.timeutil import utcnow


@dataclass
class SchemaState:
    defs: list[dict[str, Any]] = field(default_factory=list)
    hash: str | None = None
    info: dict[str, Any] | None = None
    descriptions: dict[str, str] = field(default_factory=dict)
    error: str | None = None  # ジョブ無し・ビルド不可

    @property
    def has_params(self) -> bool:
        return bool(self.defs)


def store_snapshot(db: Session, job_path: str, defs: list[dict[str, Any]], h: str) -> None:
    latest = db.scalars(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.job_path == job_path)
        .order_by(SchemaSnapshot.id.desc())
        .limit(1)
    ).first()
    if latest is None or latest.schema_hash != h:
        db.add(SchemaSnapshot(job_path=job_path, schema_hash=h, definitions_json=defs, fetched_at=utcnow()))


def fetch_schema(db: Session, client: JenkinsClientProtocol, target: Target) -> SchemaState:
    """Jenkins からスキーマを取り直して保存する。一時的な通信エラーは JenkinsError を送出する。"""
    try:
        info = client.get_job_info(target.job_path)
    except JobNotFound as exc:
        target.schema_error = str(exc)
        target.last_synced_at = utcnow()
        state = SchemaState(error=str(exc))
        last = snapshot_defs(db, target.job_path, target.schema_hash)
        state.defs = last or []
        state.hash = target.schema_hash
        return state
    raw = parameter_definitions(info)
    defs = normalize(raw)
    h = schema_hash(defs)
    store_snapshot(db, target.job_path, defs, h)
    target.schema_hash = h
    target.schema_error = "ジョブがビルド不可（buildable=false）です" if info.get("buildable") is False else None
    target.last_synced_at = utcnow()
    return SchemaState(defs=defs, hash=h, info=info, descriptions=descriptions(raw), error=None)


def snapshot_defs(db: Session, job_path: str, h: str | None) -> list[dict[str, Any]] | None:
    if not h:
        return None
    snap = db.scalars(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.job_path == job_path, SchemaSnapshot.schema_hash == h)
        .order_by(SchemaSnapshot.id.desc())
        .limit(1)
    ).first()
    return snap.definitions_json if snap else None


def cached_state(db: Session, target: Target) -> SchemaState:
    """ネットワークに出ず、最後に取得したスキーマを使う（一覧表示用）。"""
    defs = snapshot_defs(db, target.job_path, target.schema_hash) or []
    return SchemaState(defs=defs, hash=target.schema_hash, error=None, info=None)


def next_pending_run(db: Session, schedule: Schedule) -> Run | None:
    return db.scalars(
        select(Run)
        .where(Run.schedule_id == schedule.id, Run.status == R_SCHEDULED, Run.scheduled_at >= utcnow())
        .order_by(Run.scheduled_at)
        .limit(1)
    ).first()


def schedule_context(schedule: Schedule, at: datetime) -> dict[str, Any]:
    return build_context(
        label=schedule.label,
        start_date=schedule.start_date,
        end_date=schedule.end_date,
        job_path=schedule.target.job_path,
        scheduled_at_utc=at,
    )


def evaluate_schedule(
    db: Session,
    schedule: Schedule,
    state: SchemaState,
    at: datetime,
    target_error: str | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """スケジュールのパラメータを時刻 at の文脈で展開し、最新スキーマに照らして検証する。"""
    ctx = schedule_context(schedule, at)
    overrides = schedule.override_map()
    pinned = schedule.pinned_params_json if schedule.params_pinned else None
    params, detail, v_issues = resolve_params(state.defs, overrides, ctx, pinned)

    issues = diff.job_status_issues(state.info, state.error or target_error)
    baseline = None
    if schedule.baseline_schema_hash and schedule.baseline_schema_hash != state.hash:
        baseline = snapshot_defs(db, schedule.target.job_path, schedule.baseline_schema_hash)
    rendered_overrides = {n: detail[n]["value"] for n in overrides if n in detail}
    issues += diff.classify(baseline, state.defs, rendered_overrides)
    issues += v_issues
    return params, detail, diff.dedupe(issues)
