from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_actor
from app.backup import backup_now, list_backups
from app.db import SessionLocal

router = APIRouter(prefix="/api/backups", tags=["backup"])


@router.get("")
def get_backups(request: Request):
    s = request.app.state.settings
    return {
        "enabled": s.backup_enabled,
        "time": s.backup_time,
        "keep": s.backup_keep,
        # 保存先のパスはサーバー内部の情報なので返さない（設定されているかどうかだけ返す）
        "dir_configured": s.backup_dir is not None,
        "files": list_backups(s),
    }


@router.post("", status_code=201)
def create_backup(request: Request, actor: str = Depends(get_actor)):
    return backup_now(request.app.state.settings, SessionLocal, actor=actor)
