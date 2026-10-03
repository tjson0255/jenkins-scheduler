"""Jenkins クライアントの共通インターフェースと例外。"""

from __future__ import annotations

from typing import Any, Protocol

TIMER_TRIGGER_CAUSE = "hudson.triggers.TimerTrigger$TimerTriggerCause"

JOB_INFO_TREE = (
    "buildable,inQueue,lastBuild[number,building,result],"
    "property[parameterDefinitions[name,type,description,defaultParameterValue[value],choices]]"
)


class JenkinsError(Exception):
    def __init__(self, message: str, kind: str = "other", status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class JobNotFound(JenkinsError):
    def __init__(self, job_path: str):
        super().__init__(f"ジョブが見つかりません: {job_path}", kind="not_found", status_code=404)
        self.job_path = job_path


class NotFound(JenkinsError):
    def __init__(self, what: str):
        super().__init__(f"見つかりません: {what}", kind="not_found", status_code=404)


def job_url_path(job_path: str) -> str:
    """`a/b` → `/job/a/job/b`"""
    parts = [p for p in job_path.strip("/").split("/") if p]
    if not parts:
        raise ValueError("ジョブパスが空です")
    from urllib.parse import quote

    return "".join(f"/job/{quote(p, safe='')}" for p in parts)


def parameter_definitions(job_info: dict[str, Any]) -> list[dict[str, Any]]:
    defs: list[dict[str, Any]] = []
    for prop in job_info.get("property") or []:
        for d in (prop or {}).get("parameterDefinitions") or []:
            defs.append(d)
    return defs


def parse_queue_id(location: str | None) -> int:
    """`Location: https://jenkins/queue/item/123/` からキューIDを取り出す。"""
    if not location:
        raise JenkinsError("Location ヘッダがありません", kind="protocol")
    parts = [p for p in location.rstrip("/").split("/") if p]
    try:
        idx = parts.index("item")
        return int(parts[idx + 1])
    except (ValueError, IndexError):
        raise JenkinsError(f"Location ヘッダを解釈できません: {location}", kind="protocol") from None


class JenkinsClientProtocol(Protocol):
    is_mock: bool

    def ping(self) -> dict[str, Any]: ...
    def search_jobs(self, q: str | None = None) -> list[dict[str, Any]]: ...
    def get_job_info(self, job_path: str) -> dict[str, Any]: ...
    def trigger(self, job_path: str, params: dict[str, str], with_params: bool) -> int: ...
    def get_queue_item(self, queue_id: int) -> dict[str, Any]: ...
    def get_build(self, job_path: str, number: int) -> dict[str, Any]: ...
    def find_build_by_queue_id(self, job_path: str, queue_id: int) -> dict[str, Any] | None: ...
    def get_recent_timer_builds(self, job_path: str, days: int) -> list[dict[str, Any]]: ...


def filter_timer_builds(builds: list[dict[str, Any]], since_ms: int) -> list[dict[str, Any]]:
    found = []
    for b in builds:
        if (b.get("timestamp") or 0) < since_ms:
            continue
        for action in b.get("actions") or []:
            for cause in (action or {}).get("causes") or []:
                if cause.get("_class") == TIMER_TRIGGER_CAUSE:
                    found.append(b)
                    break
            else:
                continue
            break
    return found


def filter_jobs(jobs: list[dict[str, Any]], q: str | None) -> list[dict[str, Any]]:
    if not q:
        return jobs
    ql = q.lower()
    return [j for j in jobs if ql in j["path"].lower()]
