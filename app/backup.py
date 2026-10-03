"""設定の自動バックアップ。

- `scheduler-<日時>.db`：SQLite のオンラインバックアップ（稼働中でも一貫したスナップショット）。run 履歴・ログも含む。復元用
- `settings-<日時>.json`：カテゴリ・アイテム・スケジュール・パラメータ上書き値の書き出し。人が読む・差分を見る用
- 古いものは世代数（BACKUP_KEEP）を超えた分を削除する
- `.env` は Jenkins のトークンを含むためバックアップしない
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import audit, metrics
from app.config import Settings
from app.models import Category, Schedule, Target
from app.timeutil import iso_z, local_tz

log = logging.getLogger(__name__)

DB_PATTERN = "scheduler-*.db"
JSON_PATTERN = "settings-*.json"


def export_settings(db: Session) -> dict[str, Any]:
    categories = db.scalars(select(Category).order_by(Category.sort_order, Category.id)).all()
    targets = db.scalars(select(Target).order_by(Target.category_id, Target.sort_order, Target.id)).all()
    schedules = db.scalars(select(Schedule).order_by(Schedule.target_id, Schedule.start_date, Schedule.id)).all()
    return {
        "exported_at": datetime.now(local_tz()).isoformat(timespec="seconds"),
        "categories": [{"id": c.id, "name": c.name, "sort_order": c.sort_order} for c in categories],
        "items": [
            {
                "id": t.id,
                "kind": t.kind,
                "job_path": t.job_path,
                "display_name": t.display_name,
                "category": t.category.name,
                "pinned": t.pinned,
                "sort_order": t.sort_order,
                "color": t.color,
                "overlap_policy": t.overlap_policy,
                "enabled": t.enabled,
                "note": t.note,
            }
            for t in targets
        ],
        "schedules": [
            {
                "id": s.id,
                "job_path": s.target.job_path,
                "label": s.label,
                "status": s.status,
                "start_date": s.start_date.isoformat(),
                "end_date": s.end_date.isoformat() if s.end_date else None,
                "mode": s.mode,
                "cron_expr": s.cron_expr,
                "once_at": iso_z(s.once_at),
                "missed_policy": s.missed_policy,
                "grace_minutes": s.grace_minutes,
                "params_pinned": s.params_pinned,
                "param_overrides": s.override_map(),
                "pinned_params": s.pinned_params_json,
                "note": s.note,
            }
            for s in schedules
        ],
    }


def _sqlite_path(db_url: str) -> Path | None:
    url = make_url(db_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def _snapshot_sqlite(src: Path, dest: Path) -> None:
    tmp = dest.with_suffix(".tmp")
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(tmp))
    try:
        s.backup(d)  # 書き込み中でも一貫したコピーを取る（WAL の内容も含む）
    finally:
        d.close()
        s.close()
    tmp.replace(dest)


def prune(directory: Path, keep: int) -> list[Path]:
    removed: list[Path] = []
    for pattern in (DB_PATTERN, JSON_PATTERN):
        files = sorted(directory.glob(pattern), key=lambda p: p.name, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
            removed.append(old)
    return removed


def list_backups(settings: Settings) -> list[dict[str, Any]]:
    d = settings.backup_path
    if not d.exists():
        return []
    files = sorted([*d.glob(DB_PATTERN), *d.glob(JSON_PATTERN)], key=lambda p: p.name, reverse=True)
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "modified_at": datetime.fromtimestamp(p.stat().st_mtime, local_tz()).isoformat(timespec="seconds"),
        }
        for p in files
    ]


def backup_now(settings: Settings, session_factory: Callable[[], Session], actor: str = audit.SYSTEM) -> dict[str, Any]:
    """バックアップを作成し、古い世代を削除する。"""
    dest_dir = settings.backup_path
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = base = datetime.now(local_tz()).strftime("%Y%m%d-%H%M%S")
    n = 1
    while (dest_dir / f"scheduler-{stamp}.db").exists() or (dest_dir / f"settings-{stamp}.json").exists():
        n += 1
        stamp = f"{base}-{n}"  # 同じ秒に複数回取った場合
    created: list[Path] = []

    src = _sqlite_path(settings.db_url)
    if src is not None:
        db_file = dest_dir / f"scheduler-{stamp}.db"
        _snapshot_sqlite(src, db_file)
        created.append(db_file)

    with session_factory() as db:
        data = export_settings(db)
        json_file = dest_dir / f"settings-{stamp}.json"
        json_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(json_file)

        removed = prune(dest_dir, settings.backup_keep)
        result = {
            "created": [p.name for p in created],
            "removed": [p.name for p in removed],
            "items": len(data["items"]),
            "schedules": len(data["schedules"]),
        }
        audit.record(db, actor, "backup.create", "system", None, result)
        db.commit()

    metrics.backup_last_success.set_to_current_time()
    # 保存先のパスは画面・API・操作記録には出さず、サーバーのログファイルにだけ残す
    log.info("バックアップを作成しました: dir=%s %s", dest_dir, result)
    return result


def scheduled_backup(settings: Settings, session_factory: Callable[[], Session]) -> None:
    try:
        backup_now(settings, session_factory)
    except Exception:
        metrics.backup_failures_total.inc()
        log.exception("バックアップに失敗しました")
