from __future__ import annotations

from typing import Any

from app.models import Category, Run, Schedule, Target
from app.scheduler.cronutil import summarize
from app.schema import diff
from app.timeutil import iso_z


def category_out(c: Category) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "sort_order": c.sort_order, "target_count": len(c.targets)}


def target_out(t: Target, param_count: int | None = None, issue_counts: dict | None = None, holding: int = 0) -> dict[str, Any]:
    return {
        "id": t.id,
        "revision": t.revision,
        "kind": t.kind,
        "job_path": t.job_path,
        "display_name": t.display_name,
        "category_id": t.category_id,
        "category_name": t.category.name if t.category else None,
        "pinned": t.pinned,
        "sort_order": t.sort_order,
        "color": t.color,
        "overlap_policy": t.overlap_policy,
        "enabled": t.enabled,
        "note": t.note,
        "schema_hash": t.schema_hash,
        "schema_error": t.schema_error,
        "timer_trigger_detected": t.timer_trigger_detected,
        "last_synced_at": iso_z(t.last_synced_at),
        "param_count": param_count,
        "issue_counts": issue_counts or {},
        "holding_count": holding,  # 保留中の run の数（キックされずに止まっている）
    }


def schedule_out(
    s: Schedule,
    issues: list[dict] | None = None,
    run_counts: dict[str, int] | None = None,
    next_run_at=None,
    holding: int = 0,
) -> dict[str, Any]:
    issues = issues if issues is not None else []
    return {
        "id": s.id,
        "revision": s.revision,
        "target_id": s.target_id,
        "label": s.label,
        "start_date": s.start_date.isoformat(),
        "end_date": s.end_date.isoformat() if s.end_date else None,
        "mode": s.mode,
        "cron_expr": s.cron_expr,
        "cron_summary": summarize(s.cron_expr) if s.mode == "cron" else None,
        "once_at": iso_z(s.once_at),
        "status": s.status,
        "params_pinned": s.params_pinned,
        "missed_policy": s.missed_policy,
        "grace_minutes": s.grace_minutes,
        "note": s.note,
        "override_count": len(s.overrides),
        "issues": issues,
        "issue_level": diff.max_level([i for i in issues if i["level"] != diff.INFO]),
        "run_counts": run_counts or {},
        "holding_count": holding,
        "next_run_at": iso_z(next_run_at),
        "created_at": iso_z(s.created_at),
        "updated_at": iso_z(s.updated_at),
    }


def run_out(r: Run) -> dict[str, Any]:
    s = r.schedule
    live_title = (s.label or (summarize(s.cron_expr) if s.mode == "cron" else "1回")) if s else None
    return {
        "id": r.id,
        "schedule_id": r.schedule_id,
        # 一覧で何の run か分かるように、レーン名とスケジューラの件名も返す。
        # 実行した時点の件名・メモが残っていればそれを使う（スケジューラをあとで変更・削除しても分かるように）
        "target_name": r.target.display_name if r.target else None,
        "schedule_title": r.title_snapshot or live_title,
        "schedule_note": r.note_snapshot if r.title_snapshot else (s.note if s else None),
        # スケジューラを削除した後も残している実行済みの回
        "schedule_deleted": r.schedule_id is None and r.title_snapshot is not None and r.retry_of_id is None
        and not (r.reason or "").startswith("スケジューラ #"),
        "target_id": r.target_id,
        "scheduled_at": iso_z(r.scheduled_at),
        "status": r.status,
        "reason": r.reason,
        "params": r.params_json,
        "schema_hash": r.schema_hash,
        "queue_id": r.queue_id,
        "build_number": r.build_number,
        "build_url": r.build_url,
        "retry_of_id": r.retry_of_id,
        "replaces_run_id": r.replaces_run_id,
        "override_params": r.override_params,
        "created_at": iso_z(r.created_at),
        "triggered_at": iso_z(r.triggered_at),
        "finished_at": iso_z(r.finished_at),
    }
