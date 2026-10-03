from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.config import Settings
from app.jenkins.mock import MockJenkinsClient
from app.models import ACTIVE, Base, Category, Schedule, Target

FIXTURES = Path(__file__).parent / "fixtures" / "jenkins"
CSRF_HEADERS = {"X-Requested-With": "jenkins-scheduler"}


class FakeClock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixtures)
    return Settings(
        _env_file=None,
        app_data_dir=tmp_path / "scheduler",
        jenkins_mock=True,
        jenkins_mock_fixtures=fixtures,
        log_to_file=False,
        app_basic_auth_user=None,
        app_basic_auth_password=None,
        seed_file=None,
        database_url=None,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def mock_client(settings: Settings, clock: FakeClock) -> MockJenkinsClient:
    settings.ensure_dirs()
    return MockJenkinsClient(
        settings.jenkins_mock_fixtures,
        settings.data_path / "mock_state.json",
        min_seconds=4,
        max_seconds=4,
        clock=clock,
    )


@pytest.fixture
def session_factory(settings: Settings):
    settings.ensure_dirs()
    engine = dbmod.init_engine(settings.db_url)
    Base.metadata.create_all(engine)
    yield dbmod.SessionLocal
    engine.dispose()


@pytest.fixture
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def app_client(settings: Settings, mock_client: MockJenkinsClient):
    from app.main import create_app

    app = create_app(settings, start_scheduler=False, migrate=True, client=mock_client)
    with TestClient(app, headers=CSRF_HEADERS) as c:
        yield c


def edit_fixture(settings: Settings, job: str, fn) -> None:
    path = Path(settings.jenkins_mock_fixtures) / "jobs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data["jobs"][job])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_target(db, job_path="buildset/core-pipeline", **kw) -> Target:
    cat = db.query(Category).first()
    if cat is None:
        cat = Category(name="ビルドセット", sort_order=0)
        db.add(cat)
        db.flush()
    t = Target(job_path=job_path, display_name=kw.pop("display_name", job_path), category_id=cat.id, **kw)
    db.add(t)
    db.flush()
    return t


def make_schedule(db, target: Target, **kw) -> Schedule:
    defaults = dict(
        start_date=date(2027, 1, 1),
        end_date=None,
        mode="cron",
        cron_expr="0 3 * * *",
        status=ACTIVE,
        missed_policy="run_late",
        grace_minutes=10,
    )
    defaults.update(kw)
    s = Schedule(target=target, **defaults)
    db.add(s)
    db.flush()
    return s


def utc(*args) -> datetime:
    return datetime(*args)
