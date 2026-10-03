"""FastAPI アプリの組み立て。dispatcher はこのプロセス内で動くため、必ず単一プロセスで起動する。"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import secrets
from urllib.parse import quote, urlparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from app import db as dbmod
from app import demo
from app.api import agenda
from app.api import auth as auth_api
from app.api import backup as backup_api
from app.api import categories, runs, schedules, system, targets
from app.auth.directory import make_directory
from app.auth.roles import ADMIN, MEMO_EDITOR, ROLE_LABEL, User
from app.auth.sessions import COOKIE_NAME, LoginThrottle, get_session_user
from app.backup import scheduled_backup
from app.config import PROJECT_ROOT, Settings, get_settings
from app.jenkins import make_client
from app.jenkins.health import JenkinsHealth
from app.locking import ProcessLock
from app.logging_setup import setup_logging
from app.scheduler.dispatcher import Dispatcher
from app.scheduler.jobs import JobRunner
from app.scheduler.poller import poll_all
from app.seed import ensure_default_categories, load_seed

log = logging.getLogger(__name__)
STATIC_DIR = PROJECT_ROOT / "static"
MONITORING_PATHS = ("/metrics", "/api/health")
PUBLIC_PATHS = ("/login", "/api/auth/login", "/favicon.ico")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "jenkins-scheduler"
# 2. 予定・メモの編集: 予定・メモのアイテムとその予定、カテゴリの追加・変更・削除
#    （対象が予定・メモのアイテムかどうかは各 API で確かめる。Jenkins アイテムは並び順の変更だけ）
MEMO_EDITOR_WRITES = [
    ("POST", re.compile(r"^/api/schedules$")),
    ("PATCH", re.compile(r"^/api/schedules/\d+$")),
    ("DELETE", re.compile(r"^/api/schedules/\d+$")),
    ("POST", re.compile(r"^/api/targets$")),
    ("PATCH", re.compile(r"^/api/targets/\d+$")),
    ("DELETE", re.compile(r"^/api/targets/\d+$")),
    ("POST", re.compile(r"^/api/categories$")),
    ("PATCH", re.compile(r"^/api/categories/\d+$")),
    ("DELETE", re.compile(r"^/api/categories/\d+$")),
]
ANY_USER_WRITES = [("POST", re.compile(r"^/api/auth/logout$"))]


def create_app(
    settings: Settings | None = None,
    *,
    start_scheduler: bool = True,
    migrate: bool = True,
    client=None,
) -> FastAPI:
    settings = settings or get_settings()
    if settings.demo_mode:
        # 公開デモでは、設定を間違えても本物の Jenkins には絶対につながないようにする
        settings.jenkins_mock = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        setup_logging(settings)
        lock = ProcessLock(settings.lock_file)
        if settings.app_lock_enabled:
            lock.acquire()
        scheduler: JobRunner | None = None
        try:
            dbmod.init_engine(settings.db_url)
            if migrate:
                dbmod.run_migrations(settings.db_url)
            else:
                from app.models import Base

                Base.metadata.create_all(dbmod.engine)
            with dbmod.SessionLocal() as db:
                ensure_default_categories(db)
                if settings.seed_file:
                    load_seed(db, settings.seed_file, settings.default_overlap_policy)

            app.state.client = client or make_client(settings)
            if settings.demo_mode:
                if not app.state.client.is_mock:
                    raise RuntimeError("DEMO_MODE はモックの Jenkins でのみ使えます")
                with dbmod.SessionLocal() as db:
                    demo.reset(db, settings, app.state.client)
            app.state.jenkins_health = JenkinsHealth(app.state.client, interval_seconds=30)
            dispatcher = Dispatcher(dbmod.SessionLocal, app.state.client, settings)
            app.state.dispatcher = dispatcher
            dispatcher.startup_check()
            log.info(
                "起動しました host=%s port=%s mock=%s data=%s",
                settings.app_host, settings.app_port, settings.jenkins_mock, settings.app_data_dir,
            )

            if start_scheduler:
                scheduler = JobRunner()
                scheduler.add_interval("dispatcher", dispatcher.tick, settings.dispatch_interval_seconds, first_delay=0)
                scheduler.add_interval(
                    "schema_poll", poll_all, settings.schema_poll_minutes * 60,
                    args=(dbmod.SessionLocal, app.state.client, settings), first_delay=5,
                )
                # 1日1回、run の先行生成を補充する（tick でも不足分は補充される）
                scheduler.add_daily("daily_fill", _daily_fill, 0, 5, args=(dispatcher,))
                if settings.demo_mode:
                    scheduler.add_interval(
                        "demo_reset", _demo_reset, settings.demo_reset_hours * 3600, args=(dispatcher, settings, app.state.client),
                    )
                if settings.backup_enabled and not settings.demo_mode:
                    hour, minute = settings.backup_hour_minute
                    scheduler.add_daily("backup", scheduled_backup, hour, minute, args=(settings, dbmod.SessionLocal))
                scheduler.start()
            yield
        finally:
            if scheduler is not None:
                # 実行中の tick の完了を待ってから終了する
                scheduler.shutdown()
            if dbmod.engine is not None:
                dbmod.engine.dispose()
            lock.release()
            log.info("停止しました")

    app = FastAPI(title="Jenkins Scheduler", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.settings = settings

    app.state.directory = make_directory(settings)
    app.state.login_throttle = LoginThrottle(settings.login_max_failures, settings.login_lock_minutes)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        """認証・CSRF 対策・権限のチェック。画面のボタンを隠すだけでなく、ここで必ず拒否する。"""
        path = request.url.path
        method = request.method
        mode = settings.effective_auth_mode
        if settings.app_auth_exempt_monitoring and path in MONITORING_PATHS:
            return await call_next(request)

        # CSRF 対策: 変更系の API は独自ヘッダー必須（他サイトのフォームからは付けられない）。Origin も照合する
        if method in MUTATING and path.startswith("/api/"):
            if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return JSONResponse(status_code=403, content={"detail": f"リクエストに {CSRF_HEADER}: {CSRF_VALUE} ヘッダーが必要です"})
            origin = request.headers.get("origin")
            if origin and origin != "null" and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse(status_code=403, content={"detail": "別のサイトからのリクエストは受け付けません"})

        if path in PUBLIC_PATHS or path.startswith("/static/"):
            return await call_next(request)

        if mode == "none":
            user = User("local", "ローカル", ADMIN)
        elif mode == "basic":
            name = _check_basic(request.headers.get("authorization"), settings)
            if name is None:
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="jenkins-scheduler"'})
            user = User(name, name, ADMIN)
        else:
            user = await run_in_threadpool(_session_user, request.cookies.get(COOKIE_NAME))
            if user is not None and mode == "shared_admin" and not user.is_admin:
                user = None  # 他の方式で作られたログイン状態は使わない（この方式で有効なのは管理者だけ）
            if user is None and mode == "shared_admin":
                # ログインしていない人: 閲覧と予定・メモの編集ができる。ログには接続元を残す
                client = request.client.host if request.client else "-"
                user = User(f"guest@{client}", "利用者（ログインなし）", MEMO_EDITOR)
            elif user is None:
                if path.startswith("/api/"):
                    return JSONResponse(status_code=401, content={"detail": "ログインしてください"})
                return RedirectResponse(f"/login?next={quote(path)}", status_code=303)

        # 権限: フルコントロール以外は、許可した変更操作だけ通す（新しい API も既定で拒否になる）
        if method in MUTATING and not user.is_admin:
            allowed = ANY_USER_WRITES + (MEMO_EDITOR_WRITES if user.can_edit_memo else [])
            if not any(m == method and rx.match(path) for m, rx in allowed):
                msg = (
                    "この操作には管理者ログインが必要です（画面右上の「管理者ログイン」から）"
                    if mode == "shared_admin"
                    else f"この操作の権限がありません（現在の権限: {ROLE_LABEL.get(user.role, user.role)}）"
                )
                return JSONResponse(status_code=403, content={"detail": msg})
        request.state.user = user
        return await call_next(request)

    @app.middleware("http")
    async def json_charset(request: Request, call_next):
        # Windows PowerShell 5.1 の Invoke-RestMethod は charset が無いと UTF-8 で読まない（日本語が文字化けする）
        response = await call_next(request)
        if response.headers.get("content-type") == "application/json":
            response.headers["content-type"] = "application/json; charset=utf-8"
        return response

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    for r in (agenda.router, auth_api.router, categories.router, targets.router, schedules.router, runs.router, system.router, backup_api.router):
        app.include_router(r)

    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

    def page(name: str):
        def handler():
            return FileResponse(STATIC_DIR / name, headers={"Cache-Control": "no-cache"})

        return handler

    app.get("/", include_in_schema=False)(page("index.html"))
    app.get("/targets", include_in_schema=False)(page("targets.html"))
    app.get("/audit", include_in_schema=False)(page("audit.html"))
    app.get("/day", include_in_schema=False)(page("day.html"))
    app.get("/help", include_in_schema=False)(page("help.html"))

    @app.get("/login", include_in_schema=False)
    def login_page():
        if app.state.directory is None:
            return RedirectResponse("/", status_code=303)
        return FileResponse(STATIC_DIR / "login.html", headers={"Cache-Control": "no-cache"})
    return app


class NoCacheStaticFiles(StaticFiles):
    """ビルド工程が無いので、更新したファイルがすぐ反映されるよう毎回 ETag で再検証させる。"""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def _demo_reset(dispatcher: Dispatcher, settings: Settings, client) -> None:
    with dispatcher.lock, dbmod.SessionLocal() as db:
        demo.reset(db, settings, client)


def _daily_fill(dispatcher: Dispatcher) -> None:
    with dispatcher.lock, dbmod.SessionLocal() as db:
        n = dispatcher.fill_all(db)
        db.commit()
        log.info("日次の run 補充: %d 件", n)


def _session_user(token: str | None):
    with dbmod.SessionLocal() as db:
        return get_session_user(db, token)


def _check_basic(header: str | None, settings: Settings) -> str | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    user, _, password = decoded.partition(":")
    ok_user = secrets.compare_digest(user.encode(), (settings.app_basic_auth_user or "").encode())
    ok_pass = secrets.compare_digest(password.encode(), (settings.app_basic_auth_password or "").encode())
    return user if ok_user and ok_pass else None


# `uvicorn app.main:app`（開発時の --reload 用）
app = create_app()
