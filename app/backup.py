"""設定の自動バックアップ。

- `scheduler-<日時>.db`：SQLite のオンラインバックアップ（稼働中でも一貫したスナップショット）。run 履歴・ログも含む。復元用
- `settings-<日時>.json`：カテゴリ・レーン・スケジューラ・パラメータ上書き値の書き出し。人が読む・差分を見る用
- 古いものは世代数（BACKUP_KEEP）を超えた分を削除する
- `.env` は Jenkins のトークンを含むためバックアップしない
"""

from __future__ import annotations

import json
import logging
import re
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


def backup_now(
    settings: Settings, session_factory: Callable[[], Session], actor: str = audit.SYSTEM, *, prune_old: bool = True
) -> dict[str, Any]:
    """バックアップを作成し、古い世代を削除する（prune_old=False なら削除しない）。"""
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

        removed = prune(dest_dir, settings.backup_keep) if prune_old else []
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


# ---------------------------------------------------------------- リストア（復元）
BACKUP_DB_NAME = re.compile(r"^scheduler-\d{8}-\d{6}(-\d+)?\.db$")
REQUIRED_TABLES = {"category", "target", "schedule", "run", "alembic_version"}


class RestoreError(Exception):
    pass


def _check_backup_file(path: Path) -> None:
    """このツールの DB のバックアップで、壊れていないことを確かめる。"""
    if not path.is_file():
        raise RestoreError("バックアップのファイルが見つかりません")
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ok = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"DB ファイルとして読めません: {exc}") from exc
    if not REQUIRED_TABLES <= tables:
        raise RestoreError("このツールのバックアップではありません")
    if ok != "ok":
        raise RestoreError(f"バックアップのファイルが壊れています: {ok}")


def restore_file(settings: Settings, src: Path) -> None:
    """src の DB で、今の DB を置き換える（呼び出し側で他の処理を止めておくこと）。"""
    from app import db as dbmod

    live = _sqlite_path(settings.db_url)
    if live is None:
        raise RestoreError("SQLite 以外の DB には対応していません")
    _check_backup_file(src)
    if dbmod.engine is not None:
        dbmod.engine.dispose()  # 今の接続を閉じる
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(live), timeout=30)
    try:
        s.backup(d)  # ページ単位で丸ごと置き換える（WAL の DB にもそのまま書ける）
    finally:
        d.close()
        s.close()
    # 古い版で取ったバックアップでも、DB の作りを今の版に合わせる
    dbmod.run_migrations(settings.db_url)


def restore_backup(
    settings: Settings,
    session_factory: Callable[[], Session],
    name: str,
    *,
    lock,
    actor: str,
) -> dict[str, Any]:
    """バックアップの1つ（scheduler-<日時>.db）の時点に戻す。

    - 戻す前に今の状態をバックアップする（間違えて戻しても、もう一度戻せる）
    - ログイン状態（user_session）は今のものを引き継ぐ（戻した管理者がログアウトされないように）
    - lock（dispatcher のロック）を持っている間に行い、定時キックの処理と重ならないようにする
    """
    from sqlalchemy import delete, select

    from app.models import UserSession

    if not BACKUP_DB_NAME.match(name or ""):
        raise RestoreError("バックアップの名前が不正です")
    src = settings.backup_path / name
    _check_backup_file(src)
    with lock:
        before = backup_now(settings, session_factory, actor=actor, prune_old=False)
        with session_factory() as db:
            sessions = [
                {c.name: getattr(r, c.name) for c in UserSession.__table__.columns}
                for r in db.scalars(select(UserSession))
            ]
        restore_file(settings, src)
        with session_factory() as db:
            db.execute(delete(UserSession))
            for row in sessions:
                db.add(UserSession(**row))
            detail = {"restored_from": name, "backup_before_restore": before["created"]}
            audit.record(db, actor, "backup.restore", "system", None, detail)
            db.commit()
    log.warning("バックアップ %s の時点に戻しました（戻す前の状態: %s）", name, before["created"])
    return detail
