"""監査ログ。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

SYSTEM = "system"


def record(
    db: Session,
    actor: str,
    action: str,
    target_type: str | None = None,
    target_id: int | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor=actor or SYSTEM,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail_json=detail,
    )
    db.add(entry)
    return entry
