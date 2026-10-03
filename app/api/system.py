from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.metrics import CONTENT_TYPE_LATEST, generate_latest
from app.models import AuditLog
from app.timeutil import iso_z, local_midnight_utc, utcnow

router = APIRouter(tags=["system"])

TICK_STALE_SECONDS = 300


@router.get("/api/audit")
def list_audit(
    type: str | None = None,
    target: int | None = None,
    action: str | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    limit: int = Query(default=500, le=5000),
    db: Session = Depends(get_db),
):
    q = select(AuditLog)
    if type:
        q = q.where(AuditLog.target_type == type)
    if target is not None:
        q = q.where(AuditLog.target_id == target)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if from_:
        q = q.where(AuditLog.at >= local_midnight_utc(from_))
    if to:
        q = q.where(AuditLog.at < local_midnight_utc(to + timedelta(days=1)))
    rows = db.scalars(q.order_by(AuditLog.id.desc()).limit(limit))
    return [
        {
            "id": a.id,
            "at": iso_z(a.at),
            "actor": a.actor,
            "action": a.action,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "detail": a.detail_json,
        }
        for a in rows
    ]


@router.get("/api/health")
def health(request: Request, db: Session = Depends(get_db)):
    out: dict = {"status": "ok"}
    try:
        db.execute(text("SELECT 1"))
        out["db"] = "ok"
    except Exception as exc:  # pragma: no cover
        out["db"] = f"error: {exc}"
        out["status"] = "error"
    out["jenkins_mock"] = request.app.state.client.is_mock
    # 何人が開いていても、Prometheus が何回見に来ても、Jenkins への問い合わせは30秒に1回だけ
    out["jenkins"] = request.app.state.jenkins_health.status()
    if out["jenkins"] != "ok" and out["status"] == "ok":
        out["status"] = "degraded"
    last = request.app.state.dispatcher.last_tick
    out["dispatcher_last_tick"] = iso_z(last)
    lag = (utcnow() - last).total_seconds() if last else None
    out["dispatcher_lag_seconds"] = lag
    if lag is None or lag > TICK_STALE_SECONDS:
        out["status"] = "error"
        out["dispatcher"] = "stale"
    else:
        out["dispatcher"] = "ok"
    return JSONResponse(out, status_code=200 if out["status"] != "error" else 503)


@router.get("/metrics")
def metrics_endpoint():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
