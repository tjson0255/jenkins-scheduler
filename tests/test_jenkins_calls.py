"""Jenkins への呼び出しが、画面を開いている人数に比例しないこと。"""

from app.jenkins.health import JenkinsHealth
from app.timeutil import local_today
from tests.conftest import FakeClock


class CountingClient:
    def __init__(self, inner):
        self.inner = inner
        self.calls = {}

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def wrapper(*a, **k):
            self.calls[name] = self.calls.get(name, 0) + 1
            return attr(*a, **k)

        return wrapper


def test_health_pings_jenkins_once_per_interval(mock_client):
    clock = FakeClock(0)
    counting = CountingClient(mock_client)
    h = JenkinsHealth(counting, interval_seconds=30, clock=clock)
    for _ in range(100):  # 100人が同時に開いても
        assert h.status() == "ok"
    assert counting.calls["ping"] == 1
    clock.advance(31)
    h.status()
    assert counting.calls["ping"] == 2


def test_health_endpoint_does_not_ping_per_request(app_client):
    app = app_client.app
    counting = CountingClient(app.state.client)
    app.state.jenkins_health = JenkinsHealth(counting, interval_seconds=30)
    for _ in range(50):
        app_client.get("/api/health")
    assert counting.calls.get("ping", 0) == 1


def test_params_tab_calls_jenkins_only_for_admin(settings, mock_client):
    from fastapi.testclient import TestClient

    from app.auth.passwords import hash_password
    from app.main import create_app
    from tests.conftest import CSRF_HEADERS

    settings.admin_password_hash = hash_password("admin-password-123", iterations=1000)
    counting = CountingClient(mock_client)
    app = create_app(settings, start_scheduler=False, client=counting)
    with TestClient(app, headers=CSRF_HEADERS) as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "admin-password-123"})
        t = c.post("/api/targets", json={"job_path": "buildset/core-pipeline"}).json()
        s = c.post("/api/schedules", json={"target_id": t["id"], "start_date": local_today().isoformat(), "cron_expr": "0 3 * * *"}).json()
        before = counting.calls.get("get_job_info", 0)
        admin_view = c.get(f"/api/schedules/{s['id']}/params").json()
        assert admin_view["fresh"] is True and counting.calls["get_job_info"] == before + 1
        c.post("/api/auth/logout")

        # ログインしていない人が何度開いても Jenkins は呼ばない（最後に取得した定義を表示）
        before = counting.calls["get_job_info"]
        for _ in range(20):
            v = c.get(f"/api/schedules/{s['id']}/params").json()
        assert counting.calls["get_job_info"] == before
        assert v["fresh"] is False and {f["name"] for f in v["fields"]} == {f["name"] for f in admin_view["fields"]}
        assert v["fields"][0]["description"]  # 説明文も表示できる

        # ジョブ検索（重い呼び出し）は管理者だけ
        assert c.get("/api/jenkins/jobs?q=build").status_code == 403
        assert counting.calls.get("search_jobs", 0) == 0
