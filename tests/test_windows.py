"""Windows 固有の注意点（18章）: UTF-8 の読み込み、tzdata、ロックファイルによる二重起動検出。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.config import Settings
from app.locking import AlreadyRunning, ProcessLock
from app.models import Category, Target
from app.seed import ensure_default_categories, load_seed

SEED = """\
categories:
  - name: ビルドセット
  - name: リリース関連
targets:
  - job_path: buildset/core-pipeline
    display_name: コア（日本語）
    category: ビルドセット
    pinned: true
    color: "#4e79a7"
  - job_path: release/store-submit
    display_name: ストア申請
    category: 新しいカテゴリ
"""


def test_seed_yaml_utf8_and_idempotent(db, tmp_path):
    path = tmp_path / "seed.yaml"
    path.write_bytes(SEED.encode("utf-8"))  # cp932 環境でも UTF-8 として読むこと
    ensure_default_categories(db)
    first = load_seed(db, path)
    second = load_seed(db, path)
    assert first["targets"] == 2 and second["targets"] == 0 and second["updated_targets"] == 2
    t = db.scalars(select(Target).where(Target.job_path == "buildset/core-pipeline")).one()
    assert t.display_name == "コア（日本語）" and t.pinned
    names = {c.name for c in db.scalars(select(Category))}
    assert {"ビルドセット", "リリース関連", "その他", "新しいカテゴリ"} <= names


def test_env_file_utf8(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes("APP_BASIC_AUTH_USER=管理者\nAPP_PORT=9999\n".encode("utf-8"))
    s = Settings(_env_file=env)
    assert s.app_basic_auth_user == "管理者" and s.app_port == 9999


def test_tzdata_available_for_tokyo():
    import tzdata  # noqa: F401  Windows では zoneinfo のために必須

    tokyo = ZoneInfo("Asia/Tokyo")
    assert datetime(2027, 1, 1, tzinfo=tokyo).utcoffset().total_seconds() == 9 * 3600


def test_lock_prevents_double_start(tmp_path):
    lock_file = tmp_path / "data" / "scheduler.lock"
    a = ProcessLock(lock_file)
    a.acquire()
    b = ProcessLock(lock_file)
    try:
        with pytest.raises(AlreadyRunning):
            b.acquire()
    finally:
        a.release()
    b.acquire()  # 解放後は取得できる
    b.release()


def test_main_exits_when_locked(tmp_path, monkeypatch):
    from app import __main__ as entry
    from app import config

    s = Settings(_env_file=None, app_data_dir=tmp_path / "k", log_to_file=False)
    monkeypatch.setattr(entry, "get_settings", lambda: s)
    monkeypatch.setattr(config, "get_settings", lambda: s)
    s.ensure_dirs()
    holder = ProcessLock(s.lock_file)
    holder.acquire()
    try:
        assert entry.main([]) == 2
    finally:
        holder.release()


def test_files_read_by_configparser_are_ascii():
    """alembic.ini は configparser が OS の既定の文字コード（Windows では cp1252 / cp932）で読むので、ASCII だけにする。"""
    from app.config import PROJECT_ROOT

    (PROJECT_ROOT / "alembic.ini").read_bytes().decode("ascii")
