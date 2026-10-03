from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_actor, get_user
from app.auth.roles import User
from app.backup import RestoreError, backup_now, list_backups, restore_backup
from app.db import SessionLocal



def require_admin(user: User = Depends(get_user)) -> User:
    # バックアップは一覧も含めて管理者だけ
    if not user.is_admin:
        raise HTTPException(403, "バックアップには管理者ログインが必要です")
    return user


router = APIRouter(prefix="/api/backups", tags=["backup"], dependencies=[Depends(require_admin)])


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


@router.post("/{name}/restore")
async def restore(name: str, request: Request, user: User = Depends(get_user)):
    """バックアップの時点に戻す。"""
    try:
        return await run_in_threadpool(
            restore_backup, request.app.state.settings, SessionLocal, name,
            lock=request.app.state.dispatcher.lock, actor=user.username,
        )
    except RestoreError as exc:
        raise HTTPException(400, str(exc)) from exc
