"""ログイン状態の管理と、ログイン失敗の回数制限。"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth.roles import User
from app.models import UserSession
from app.timeutil import utcnow

COOKIE_NAME = "jenkins_scheduler_session"
TOUCH_INTERVAL = timedelta(minutes=5)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(UserSession(
        id=_hash(token), username=user.username, display_name=user.display_name, role=user.role,
        created_at=now, expires_at=now + timedelta(hours=hours), last_seen_at=now,
    ))
    # 期限切れのものを片付ける
    db.execute(delete(UserSession).where(UserSession.expires_at < now))
    db.commit()
    return token


def get_session_user(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    rec = db.get(UserSession, _hash(token))
    now = utcnow()
    if rec is None or rec.expires_at < now:
        return None
    if now - rec.last_seen_at > TOUCH_INTERVAL:
        rec.last_seen_at = now
        db.commit()
    return User(username=rec.username, display_name=rec.display_name, role=rec.role)


def delete_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(UserSession).where(UserSession.id == _hash(token)))
        db.commit()


class LoginThrottle:
    """同じユーザー名・同じ接続元からのログイン失敗が続いたら、しばらく受け付けない（総当たり対策）。"""

    def __init__(self, max_failures: int, lock_minutes: int):
        self.max_failures = max_failures
        self.window = lock_minutes * 60
        self._fail: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _keys(self, username: str, client: str) -> list[str]:
        return [f"u:{(username or '').strip().lower()}", f"ip:{client}"]

    def blocked(self, username: str, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            for k in self._keys(username, client):
                q = self._fail[k]
                while q and now - q[0] > self.window:
                    q.popleft()
                # 接続元 IP ごとの上限は緩めにする（社内のプロキシ・NAT で多くの人が同じ IP になるため）
                limit = self.max_failures * 4 if k.startswith("ip:") else self.max_failures
                if len(q) >= limit:
                    return True
        return False

    def failed(self, username: str, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            for k in self._keys(username, client):
                self._fail[k].append(now)

    def succeeded(self, username: str, client: str) -> None:
        with self._lock:
            self._fail.pop(self._keys(username, client)[0], None)
