"""定期ポーリング: スキーマ取得（8.2）と Jenkins 側 cron 残存の検出（9.3）。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, metrics
from app.config import Settings
from app.jenkins.base import JenkinsClientProtocol, JenkinsError, JobNotFound
from app.models import ITEM_JENKINS, PENDING_RUN_STATUSES, Run, Schedule, Target
from app.schema import diff
from app.schema.service import cached_state, evaluate_schedule, fetch_schema, next_pending_run
from app.timeutil import utcnow

log = logging.getLogger(__name__)


def schedule_issues_cached(db: Session, s: Schedule) -> list[dict[str, Any]]:
    """ネットワークに出ずに、最後に取得したスキーマで差分・検証を行う（一覧のバッジ用）。"""
    if s.is_memo or s.target.is_memo:
        return []
    state = cached_state(db, s.target)
    nxt = next_pending_run(db, s)
    at = nxt.scheduled_at if nxt else utcnow()
    _p, _d, issues = evaluate_schedule(db, s, state, at, target_error=s.target.schema_error)
    return issues


def has_pending_runs(db: Session, s: Schedule) -> bool:
    return (
        db.scalar(
            select(Run.id).where(Run.schedule_id == s.id, Run.status.in_(PENDING_RUN_STATUSES)).limit(1)
        )
        is not None
    )


def sync_target(db: Session, client: JenkinsClientProtocol, target: Target, lookback_days: int) -> dict[str, Any]:
    result: dict[str, Any] = {"target_id": target.id, "job_path": target.job_path}
    before_hash, before_err = target.schema_hash, target.schema_error
    state = fetch_schema(db, client, target)
    result["schema_hash"] = state.hash
    result["error"] = state.error or target.schema_error
    if before_hash and state.hash and before_hash != state.hash:
        audit.record(db, audit.SYSTEM, "schema.changed", "target", target.id, {"from": before_hash, "to": state.hash})
    if before_err != target.schema_error and target.schema_error:
        log.warning("target %s: %s", target.job_path, target.schema_error)
    if state.error is None:
        try:
            timer_builds = client.get_recent_timer_builds(target.job_path, lookback_days)
        except JobNotFound:
            timer_builds = []
        detected = bool(timer_builds)
        if detected and not target.timer_trigger_detected:
            log.warning("target %s: Jenkins 側の cron が残っています", target.job_path)
            audit.record(
                db, audit.SYSTEM, "timer_trigger.detected", "target", target.id,
                {"builds": [b.get("number") for b in timer_builds]},
            )
        target.timer_trigger_detected = detected
    result["timer_trigger_detected"] = target.timer_trigger_detected
    return result


def update_drift_metrics(db: Session) -> None:
    metrics.schema_drift.clear()
    metrics.timer_trigger_detected.clear()
    for t in db.scalars(select(Target).where(Target.kind == ITEM_JENKINS)):
        counts = {diff.WARNING: 0, diff.ERROR: 0}
        for s in t.schedules:
            if not has_pending_runs(db, s):
                continue
            for i in schedule_issues_cached(db, s):
                if i["level"] in counts:
                    counts[i["level"]] += 1
        for level, n in counts.items():
            metrics.schema_drift.labels(target=t.job_path, level=level).set(n)
        metrics.timer_trigger_detected.labels(target=t.job_path).set(1 if t.timer_trigger_detected else 0)


def poll_all(session_factory: Callable[[], Session], client: JenkinsClientProtocol, settings: Settings) -> list[dict]:
    results = []
    with session_factory() as db:
        for t in db.scalars(select(Target).where(Target.enabled.is_(True), Target.kind == ITEM_JENKINS)).all():
            try:
                results.append(sync_target(db, client, t, settings.timer_trigger_lookback_days))
                db.commit()
            except JenkinsError as exc:
                db.rollback()
                log.warning("target %s のスキーマ取得に失敗: %s", t.job_path, exc)
                results.append({"target_id": t.id, "job_path": t.job_path, "error": str(exc)})
        try:
            update_drift_metrics(db)
        except Exception:
            log.exception("メトリクス更新に失敗")
    return results
