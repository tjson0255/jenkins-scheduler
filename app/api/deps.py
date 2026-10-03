from __future__ import annotations

from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.roles import User
from app.db import SessionLocal
from app.jenkins.base import JenkinsClientProtocol, JenkinsError
from app.scheduler.dispatcher import Dispatcher


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user(request: Request) -> User:
    """ミドルウェアが確かめたログイン中の利用者。"""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "ログインしてください")
    return user


def get_actor(request: Request) -> str:
    """監査ログに残す実行者（AD のアカウント名）。"""
    user = getattr(request.state, "user", None)
    return user.username if user else "local"


def get_client(request: Request) -> JenkinsClientProtocol:
    return request.app.state.client


def get_dispatcher(request: Request) -> Dispatcher:
    return request.app.state.dispatcher


def jenkins_http_error(exc: JenkinsError) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Jenkins との通信に失敗しました: {exc}")


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} が見つかりません")
