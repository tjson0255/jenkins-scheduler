"""実 Jenkins クライアント（7.1）を respx でモックしてテストする。"""

import time

import httpx
import pytest
import respx

from app.jenkins.base import TIMER_TRIGGER_CAUSE, JenkinsError, JobNotFound, NotFound, job_url_path, parse_queue_id
from app.jenkins.client import JenkinsClient

BASE = "https://jenkins.example.local"


@pytest.fixture
def client():
    c = JenkinsClient(BASE, "bot", "secret-token", backoff_base=0)
    yield c
    c.close()


def test_job_url_path():
    assert job_url_path("release/core-pipeline") == "/job/release/job/core-pipeline"
    assert job_url_path("/a/b c/") == "/job/a/job/b%20c"


@pytest.mark.parametrize(
    "loc,expected",
    [(f"{BASE}/queue/item/123/", 123), (f"{BASE}/queue/item/7", 7), ("/queue/item/42/", 42)],
)
def test_parse_queue_id(loc, expected):
    assert parse_queue_id(loc) == expected


def test_parse_queue_id_invalid():
    with pytest.raises(JenkinsError):
        parse_queue_id(None)
    with pytest.raises(JenkinsError):
        parse_queue_id(f"{BASE}/job/x/")


@respx.mock
def test_trigger_with_params_uses_build_with_parameters(client):
    route = respx.post(f"{BASE}/job/buildset/job/core/buildWithParameters").mock(
        return_value=httpx.Response(201, headers={"Location": f"{BASE}/queue/item/555/"})
    )
    assert client.trigger("buildset/core", {"BRANCH": "main"}, with_params=True) == 555
    req = route.calls.last.request
    assert b"BRANCH=main" in req.content
    assert req.headers["authorization"].startswith("Basic ")


@respx.mock
def test_trigger_without_params_uses_build(client):
    route = respx.post(f"{BASE}/job/misc/job/cleanup/build").mock(
        return_value=httpx.Response(201, headers={"Location": f"{BASE}/queue/item/9/"})
    )
    assert client.trigger("misc/cleanup", {}, with_params=False) == 9
    assert route.called


@respx.mock
def test_trigger_is_not_retried_on_server_error(client):
    route = respx.post(f"{BASE}/job/a/build").mock(return_value=httpx.Response(500))
    with pytest.raises(JenkinsError):
        client.trigger("a", {}, with_params=False)
    assert route.call_count == 1


@respx.mock
def test_trigger_fetches_crumb_on_403(client):
    respx.get(f"{BASE}/crumbIssuer/api/json").mock(
        return_value=httpx.Response(200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "abc"})
    )
    route = respx.post(f"{BASE}/job/a/build").mock(
        side_effect=[httpx.Response(403), httpx.Response(201, headers={"Location": f"{BASE}/queue/item/1/"})]
    )
    assert client.trigger("a", {}, with_params=False) == 1
    assert route.calls[1].request.headers["Jenkins-Crumb"] == "abc"


@respx.mock
def test_get_is_retried_with_backoff(client):
    route = respx.get(f"{BASE}/job/a/api/json").mock(
        side_effect=[httpx.Response(502), httpx.ConnectError("boom"), httpx.Response(200, json={"buildable": True})]
    )
    assert client.get_job_info("a")["buildable"] is True
    assert route.call_count == 3


@respx.mock
def test_get_gives_up_after_three_attempts(client):
    route = respx.get(f"{BASE}/job/a/api/json").mock(return_value=httpx.Response(503))
    with pytest.raises(JenkinsError):
        client.get_job_info("a")
    assert route.call_count == 3


@respx.mock
def test_job_not_found(client):
    respx.get(f"{BASE}/job/nope/api/json").mock(return_value=httpx.Response(404))
    with pytest.raises(JobNotFound):
        client.get_job_info("nope")


@respx.mock
def test_queue_item_states(client):
    respx.get(f"{BASE}/queue/item/1/api/json").mock(
        return_value=httpx.Response(200, json={"id": 1, "cancelled": True})
    )
    respx.get(f"{BASE}/queue/item/2/api/json").mock(
        return_value=httpx.Response(200, json={"id": 2, "executable": {"number": 17, "url": f"{BASE}/job/a/17/"}})
    )
    respx.get(f"{BASE}/queue/item/3/api/json").mock(return_value=httpx.Response(404))
    assert client.get_queue_item(1)["cancelled"] is True
    assert client.get_queue_item(2)["executable"]["number"] == 17
    with pytest.raises(NotFound):
        client.get_queue_item(3)


@respx.mock
def test_timer_trigger_detection(client):
    now_ms = int(time.time() * 1000)
    builds = [
        {"number": 3, "timestamp": now_ms - 3600_000, "actions": [{}, {"causes": [{"_class": TIMER_TRIGGER_CAUSE}]}]},
        {"number": 2, "timestamp": now_ms - 7200_000, "actions": [{"causes": [{"_class": "hudson.model.Cause$UserIdCause"}]}]},
        {"number": 1, "timestamp": now_ms - 30 * 86400_000, "actions": [{"causes": [{"_class": TIMER_TRIGGER_CAUSE}]}]},
    ]
    respx.get(f"{BASE}/job/a/api/json").mock(return_value=httpx.Response(200, json={"builds": builds}))
    found = client.get_recent_timer_builds("a", days=7)
    assert [b["number"] for b in found] == [3]


@respx.mock
def test_search_jobs_walks_folders(client):
    respx.get(f"{BASE}/api/json").mock(
        return_value=httpx.Response(200, json={"jobs": [
            {"name": "release", "_class": "com.cloudbees.hudson.plugins.folder.Folder"},
            {"name": "top", "_class": "hudson.model.FreeStyleProject"},
        ]})
    )
    respx.get(f"{BASE}/job/release/api/json").mock(
        return_value=httpx.Response(200, json={"jobs": [{"name": "core-pipeline", "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob"}]})
    )
    assert [j["path"] for j in client.search_jobs()] == ["release/core-pipeline", "top"]
    assert [j["path"] for j in client.search_jobs("core")] == ["release/core-pipeline"]
