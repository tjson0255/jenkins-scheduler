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
    parser.add_argument("--seed", help="seed.yaml を読み込んで終了する")
    parser.add_argument("--backup", action="store_true", help="バックアップを今すぐ作成して終了する（サービス稼働中でも可）")
    parser.add_argument("--hash-password", action="store_true", help="管理者パスワードのハッシュ（ADMIN_PASSWORD_HASH に書く値）を作る")
    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
