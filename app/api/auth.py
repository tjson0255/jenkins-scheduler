"""ログイン・ログアウト・ログイン中の利用者。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import audit
from app.api.deps import get_db, get_user
from app.auth.directory import AuthError, DirectoryUnavailable
from app.auth.roles import ROLE_LABEL, User
from app.auth.sessions import COOKIE_NAME, create_session, delete_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)


def user_out(user: User, mode: str, settings=None) -> dict:
    demo = None
    if settings is not None and settings.demo_mode:
        # 公開デモでは、試したい人が管理者でログインできるよう、ユーザー名とパスワードを画面に出す
        demo = {"reset_hours": settings.demo_reset_hours, "admin_username": settings.admin_username, "admin_password": settings.admin_password or None}
    return {
        "demo": demo,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "role_label": ROLE_LABEL.get(user.role, user.role),
        "auth_mode": mode,
        "can": {"admin": user.is_admin, "edit_memo": user.can_edit_memo},
    }


@router.get("/me")
def me(request: Request, user: User = Depends(get_user)):
    return user_out(user, request.app.state.settings.effective_auth_mode, request.app.state.settings)


@router.post("/login")
async def login(request: Request, body: LoginIn):
    settings = request.app.state.settings
    directory = request.app.state.directory
    if directory is None:
        raise HTTPException(400, "この設定（AUTH_MODE）ではログイン画面を使いません")
    client = request.client.host if request.client else "-"
    throttle = request.app.state.login_throttle
    if throttle.blocked(body.username, client):
        raise HTTPException(429, f"ログインの失敗が続いたため、{settings.login_lock_minutes} 分ほど待ってからやり直してください")
    try:
        # LDAP の通信はブロッキングなので、別スレッドで行う
        du = await run_in_threadpool(directory.authenticate, body.username, body.password)
    except AuthError as exc:
        throttle.failed(body.username, client)
        await run_in_threadpool(_record, body.username.strip()[:100] or "-", "auth.login_failed", {"reason": str(exc), "client": client})
        raise HTTPException(401, str(exc)) from exc
    except DirectoryUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    throttle.succeeded(body.username, client)
    user = User(username=du.username, display_name=du.display_name, role=du.role)
    token = await run_in_threadpool(_login, user, settings.session_hours, client)
    resp = JSONResponse(user_out(user, settings.effective_auth_mode, settings))
    resp.set_cookie(
        COOKIE_NAME, token, max_age=settings.session_hours * 3600, httponly=True,
        secure=settings.secure_cookie, samesite="strict", path="/",
    )
    return resp


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), user: User = Depends(get_user)):
    delete_session(db, request.cookies.get(COOKIE_NAME))
    audit.record(db, user.username, "auth.logout", "system", None, None)
    db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


def _login(user: User, hours: int, client: str) -> str:
    from app.db import SessionLocal

    with SessionLocal() as db:
        token = create_session(db, user, hours)
        audit.record(db, user.username, "auth.login", "system", None, {"role": user.role, "client": client})
        db.commit()
        return token


def _record(actor: str, action: str, detail: dict) -> None:
    from app.db import SessionLocal

    with SessionLocal() as db:
        audit.record(db, actor, action, "system", None, detail)
        db.commit()
