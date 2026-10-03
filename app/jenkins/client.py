"""実 Jenkins 用の薄いクライアント（仕様書 7.1）。

- 認証は Basic（ユーザー名 + API トークン）。トークンはログに出さない。
- GET は指数バックオフで最大3回リトライ。キック（POST）は自動リトライしない。
- 403 のときだけ crumb を取得して1回やり直す（リクエストが受理されていないので二重起動にはならない）。
"""

from __future__ import annotations

import logging
import ssl
import time
from typing import Any

import httpx

from app import metrics
from app.jenkins.base import (
    JOB_INFO_TREE,
    JenkinsError,
    JobNotFound,
    NotFound,
    filter_jobs,
    filter_timer_builds,
    job_url_path,
    parse_queue_id,
)

log = logging.getLogger(__name__)

FOLDER_CLASSES = ("Folder", "OrganizationFolder", "WorkflowMultiBranchProject")
SEARCH_CACHE_SECONDS = 60


def _ssl_context(ca_bundle) -> ssl.SSLContext | bool:
    if ca_bundle:
        return ssl.create_default_context(cafile=str(ca_bundle))
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # pragma: no cover - truststore が使えない環境
        return True


class JenkinsClient:
    is_mock = False

    def __init__(
        self,
        base_url: str,
        user: str,
        token: str,
        ca_bundle=None,
        transport: httpx.BaseTransport | None = None,
        backoff_base: float = 0.5,
    ):
        self.base_url = base_url.rstrip("/")
        self._crumb: dict[str, str] | None = None
        self._search_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._backoff_base = backoff_base
        kwargs: dict[str, Any] = dict(
            base_url=self.base_url,
            auth=httpx.BasicAuth(user, token) if user else None,
            timeout=httpx.Timeout(30.0, connect=10.0),
            trust_env=True,  # HTTPS_PROXY / NO_PROXY を尊重
            follow_redirects=False,
        )
        if transport is not None:
            kwargs["transport"] = transport
        else:
            kwargs["verify"] = _ssl_context(ca_bundle)
        self._http = httpx.Client(**kwargs)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ 低レベル
    def _error(self, exc: Exception | None, resp: httpx.Response | None = None) -> JenkinsError:
        if resp is not None:
            kind = f"http_{resp.status_code}"
            err = JenkinsError(f"Jenkins API エラー: HTTP {resp.status_code} {resp.request.url.path}", kind, resp.status_code)
        elif isinstance(exc, httpx.TimeoutException):
            err = JenkinsError(f"Jenkins API タイムアウト: {exc}", "timeout")
        elif isinstance(exc, httpx.ConnectError):
            err = JenkinsError(f"Jenkins に接続できません: {exc}", "connect")
        else:
            err = JenkinsError(f"Jenkins API エラー: {exc}", "other")
        metrics.jenkins_api_errors_total.labels(kind=err.kind).inc()
        return err

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        last: JenkinsError | None = None
        for attempt in range(3):
            try:
                resp = self._http.get(path, params=params)
            except httpx.HTTPError as exc:
                last = self._error(exc)
            else:
                if resp.status_code == 404:
                    return resp
                if resp.status_code < 500 and resp.status_code != 429:
                    if resp.status_code >= 400:
                        raise self._error(None, resp)
                    return resp
                last = self._error(None, resp)
            if attempt < 2:
                time.sleep(self._backoff_base * (2**attempt))
        assert last is not None
        raise last

    def _get_json(self, path: str, params: dict[str, Any] | None = None, not_found=None) -> dict[str, Any]:
        resp = self._get(path, params)
        if resp.status_code == 404:
            raise not_found or NotFound(path)
        return resp.json()

    def _fetch_crumb(self) -> dict[str, str]:
        data = self._get_json("/crumbIssuer/api/json")
        self._crumb = {data["crumbRequestField"]: data["crumb"]}
        return self._crumb

    def _post(self, path: str, data: dict[str, str] | None) -> httpx.Response:
        """自動リトライしない POST。403 の場合のみ crumb を付けて1回だけやり直す。"""
        headers = dict(self._crumb or {})
        try:
            resp = self._http.post(path, data=data, headers=headers)
            if resp.status_code == 403:
                headers = self._fetch_crumb()
                resp = self._http.post(path, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc
        if resp.status_code >= 400:
            raise self._error(None, resp)
        return resp

    # ------------------------------------------------------------------ API
    def ping(self) -> dict[str, Any]:
        data = self._get_json("/api/json", {"tree": "mode,nodeName"})
        return {"ok": True, "mode": data.get("mode")}

    def search_jobs(self, q: str | None = None) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._search_cache and now - self._search_cache[0] < SEARCH_CACHE_SECONDS:
            jobs = self._search_cache[1]
        else:
            jobs = []
            self._walk("", "", jobs, depth=0)
            jobs.sort(key=lambda j: j["path"])
            self._search_cache = (now, jobs)
        return filter_jobs(jobs, q)

    def _walk(self, url_path: str, prefix: str, out: list[dict[str, Any]], depth: int) -> None:
        if depth > 6:
            return
        data = self._get_json(f"{url_path}/api/json", {"tree": "jobs[name,url,_class]"})
        for job in data.get("jobs") or []:
            name = job.get("name")
            cls = job.get("_class", "")
            path = f"{prefix}{name}"
            if any(cls.endswith(c) for c in FOLDER_CLASSES):
                self._walk(f"{url_path}/job/{name}", f"{path}/", out, depth + 1)
            else:
                out.append({"path": path, "name": name, "url": job.get("url"), "class": cls})

    def get_job_info(self, job_path: str) -> dict[str, Any]:
        return self._get_json(
            f"{job_url_path(job_path)}/api/json", {"tree": JOB_INFO_TREE}, not_found=JobNotFound(job_path)
        )

    def trigger(self, job_path: str, params: dict[str, str], with_params: bool) -> int:
        endpoint = "/buildWithParameters" if with_params else "/build"
        resp = self._post(f"{job_url_path(job_path)}{endpoint}", params if with_params else None)
        return parse_queue_id(resp.headers.get("Location"))

    def get_queue_item(self, queue_id: int) -> dict[str, Any]:
        return self._get_json(f"/queue/item/{queue_id}/api/json")

    def get_build(self, job_path: str, number: int) -> dict[str, Any]:
        return self._get_json(
            f"{job_url_path(job_path)}/{number}/api/json",
            {"tree": "number,result,building,url,timestamp,duration"},
        )

    def find_build_by_queue_id(self, job_path: str, queue_id: int) -> dict[str, Any] | None:
        data = self._get_json(
            f"{job_url_path(job_path)}/api/json",
            {"tree": "builds[number,queueId,url,building,result]{0,50}"},
            not_found=JobNotFound(job_path),
        )
        for b in data.get("builds") or []:
            if b.get("queueId") == queue_id:
                return b
        return None

    def get_recent_timer_builds(self, job_path: str, days: int) -> list[dict[str, Any]]:
        data = self._get_json(
            f"{job_url_path(job_path)}/api/json",
            {"tree": "builds[number,timestamp,actions[causes[_class]]]{0,50}"},
            not_found=JobNotFound(job_path),
        )
        since_ms = int((time.time() - days * 86400) * 1000)
        return filter_timer_builds(data.get("builds") or [], since_ms)
