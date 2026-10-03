"""`python -m app` で起動する（Windows サービスからもこれを実行する）。

- 必ず単一プロセス（workers=1）。dispatcher がプロセス内で動くため、複数プロセスにすると二重キックになる。
- ロックファイルで多重起動を防ぐ（取得できなければ終了コード 2 で終了）。
- 停止時は Uvicorn にシャットダウンを伝え、dispatcher の tick 完了を待ってから終了する（最大20秒）。
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from app.config import get_settings
from app.locking import AlreadyRunning, ProcessLock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="Jenkins 定時キックツール")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--migrate-only", action="store_true", help="DB マイグレーションだけ実行して終了する")
    parser.add_argument("--seed", help="seed.toml を読み込んで終了する")
    parser.add_argument("--backup", action="store_true", help="バックアップを今すぐ作成して終了する（サービス稼働中でも可）")
    parser.add_argument("--hash-password", action="store_true", help="管理者パスワードのハッシュ（ADMIN_PASSWORD_HASH に書く値）を作る")
    # インストーラ用
    parser.add_argument("--init-env", action="store_true", help=".env が無ければ .env.example から作る")
    parser.add_argument("--set-admin-password-file", metavar="PATH", help="ファイルの1行目を管理者パスワードとして .env に（ハッシュで）書き込む")
    parser.add_argument("--set-env", metavar="KEY=VALUE", action="append", default=[], help=".env に設定を書き込む（複数指定可）")
    parser.add_argument("--version", action="store_true", help="バージョンを表示する")
    parser.add_argument("--restore", metavar="PATH", help="バックアップの .db の時点に戻して終了する（サービスを止めてから実行する）")
    args = parser.parse_args(argv)

    if args.version:
        from app import __version__

        print(__version__)
        return 0

    if args.init_env or args.set_admin_password_file or args.set_env:
        return _edit_env(args)

    if args.hash_password:
        import getpass

        from app.auth.passwords import hash_password

        pw = getpass.getpass("管理者パスワード: ")
        if pw != getpass.getpass("もう一度: "):
            print("エラー: パスワードが一致しません", file=sys.stderr)
            return 1
        if len(pw) < 12:
            print("エラー: 12文字以上にしてください", file=sys.stderr)
            return 1
        print("\n.env に次の1行を書いてください（ADMIN_PASSWORD は消してください）:\n")
        print(f"ADMIN_PASSWORD_HASH={hash_password(pw)}")
        return 0

    settings = get_settings()
    settings.ensure_dirs()

    if args.backup:
        from app import db as dbmod
        from app.backup import backup_now

        dbmod.init_engine(settings.db_url)
        print(backup_now(settings, dbmod.SessionLocal, actor="cli"))
        return 0

    if args.restore:
        return _restore(settings, args.restore)

    if args.migrate_only or args.seed:
        from app import db as dbmod
        from app.seed import ensure_default_categories, load_seed

        dbmod.init_engine(settings.db_url)
        dbmod.run_migrations(settings.db_url)
        with dbmod.SessionLocal() as db:
            ensure_default_categories(db)
            if args.seed:
                print(load_seed(db, args.seed, settings.default_overlap_policy))
        print("migration: OK")
        return 0

    # ロックはアプリの lifespan で取得するが、ここで先に確認して分かりやすいメッセージで終了する
    probe = ProcessLock(settings.lock_file)
    try:
        probe.acquire()
    except AlreadyRunning as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    probe.release()

    from app.main import create_app

    ssl_kwargs = {}
    if settings.app_tls_cert and settings.app_tls_key:
        ssl_kwargs = {"ssl_certfile": str(settings.app_tls_cert), "ssl_keyfile": str(settings.app_tls_key)}

    config = uvicorn.Config(
        create_app(settings),
        host=args.host or settings.app_host,
        port=args.port or settings.app_port,
        workers=1,
        loop="asyncio",
        log_config=None,
        timeout_graceful_shutdown=20,
        **ssl_kwargs,
    )
    server = uvicorn.Server(config)
    server.run()
    return 0 if server.started else 1


def _restore(settings, path: str) -> int:
    """サービスが起動しないときなどのためのリストア。動いている間は行わない。"""
    from pathlib import Path

    from app import audit
    from app import db as dbmod
    from app.backup import RestoreError, backup_now, restore_file

    probe = ProcessLock(settings.lock_file)
    try:
        probe.acquire()
    except AlreadyRunning:
        print("エラー: サービスが動いています。止めてから実行するか、画面（レーン → バックアップ）から戻してください", file=sys.stderr)
        return 2
    try:
        dbmod.init_engine(settings.db_url)
        before = backup_now(settings, dbmod.SessionLocal, actor="cli", prune_old=False)
        restore_file(settings, Path(path))
        with dbmod.SessionLocal() as db:
            audit.record(db, "cli", "backup.restore", "system", None, {"restored_from": str(Path(path).name), "backup_before_restore": before["created"]})
            db.commit()
        print(f"戻しました: {path}（戻す前の状態: {', '.join(before['created'])}）")
        return 0
    except RestoreError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    finally:
        probe.release()


def _edit_env(args) -> int:
    from app import envfile
    from app.config import PROJECT_ROOT

    env_path = PROJECT_ROOT / ".env"
    if args.init_env and envfile.init_from_example(env_path, PROJECT_ROOT / ".env.example"):
        print(f".env を作成しました: {env_path}")
    values: dict[str, str | None] = {}
    for item in args.set_env:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            print(f"エラー: KEY=VALUE の形で指定してください: {item}", file=sys.stderr)
            return 1
        values[key.strip()] = value
    if args.set_admin_password_file:
        from pathlib import Path

        from app.auth.passwords import hash_password

        pw_path = Path(args.set_admin_password_file)
        # インストーラ（Inno Setup）は BOM 付き UTF-8 で書くので utf-8-sig で読む
        lines = pw_path.read_text(encoding="utf-8-sig").splitlines() if pw_path.exists() else []
        pw = lines[0] if lines else ""
        if len(pw) < 12:
            print("エラー: 管理者パスワードは12文字以上にしてください", file=sys.stderr)
            return 1
        values["ADMIN_PASSWORD_HASH"] = hash_password(pw)
        values["ADMIN_PASSWORD"] = None  # 平文は残さない
    if values:
        envfile.set_values(env_path, values)
        print(f".env を更新しました: {', '.join(k for k in values)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
