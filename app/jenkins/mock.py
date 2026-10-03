"""モック Jenkins（仕様書 7.2）。

- ジョブ一覧・パラメータ定義は `<fixtures>/jobs.json` から毎回読む（書き換えればスキーマ差分を試せる）。
- キックはキュー待ち → 実行 → 数秒後にランダムで成功/失敗/不安定。
  パラメータ `MOCK_RESULT`（SUCCESS/FAILURE/UNSTABLE/ABORTED）があればその結果に固定する。
- 状態はデータディレクトリの `mock_state.json` に保存するので、ツールを再起動しても追跡を再開できる。
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path
from typing import Any

from app.jenkins.base import (
    TIMER_TRIGGER_CAUSE,
    JobNotFound,
    NotFound,
    filter_jobs,
    filter_timer_builds,
)

RESULT_WEIGHTS = (("SUCCESS", 0.7), ("UNSTABLE", 0.15), ("FAILURE", 0.15))


class MockJenkinsClient:
    is_mock = True

    def __init__(
        self,
        fixtures_dir: Path,
        state_file: Path,
        min_seconds: float = 3.0,
        max_seconds: float = 10.0,
        base_url: str = "http://mock-jenkins",
        clock=time.time,
    ):
        self.fixtures_dir = Path(fixtures_dir)
        self.state_file = Path(state_file)
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.base_url = base_url.rstrip("/")
        self.clock = clock
        self._lock = threading.RLock()
        self.trigger_calls: list[dict[str, Any]] = []  # テスト用

    # ------------------------------------------------------------------ fixtures / state
    def _jobs(self) -> dict[str, Any]:
        path = self.fixtures_dir / "jobs.json"
        with path.open(encoding="utf-8") as f:
            return json.load(f).get("jobs", {})

    def _load_state(self) -> dict[str, Any]:
        if self.state_file.exists():
            with self.state_file.open(encoding="utf-8") as f:
                return json.load(f)
        return {"next_queue_id": 1, "queue": {}, "next_build": {}}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        tmp.replace(self.state_file)

    def _job(self, job_path: str) -> dict[str, Any]:
        jobs = self._jobs()
        if job_path not in jobs:
            raise JobNotFound(job_path)
        return jobs[job_path]

    def _job_url(self, job_path: str) -> str:
        return self.base_url + "".join(f"/job/{p}" for p in job_path.split("/")) + "/"

    def _phase(self, item: dict[str, Any], now: float) -> str:
        if item.get("cancelled"):
            return "cancelled"
        if now < item["start_at"]:
            return "queued"
        if now < item["end_at"]:
            return "running"
        return "finished"

    # ------------------------------------------------------------------ API
    def ping(self) -> dict[str, Any]:
        return {"ok": True, "mode": "MOCK"}

    def search_jobs(self, q: str | None = None) -> list[dict[str, Any]]:
        jobs = [
            {"path": p, "name": p.split("/")[-1], "url": self._job_url(p), "class": "mock.Job"}
            for p in sorted(self._jobs())
        ]
        return filter_jobs(jobs, q)

    def get_job_info(self, job_path: str) -> dict[str, Any]:
        job = self._job(job_path)
        now = self.clock()
        with self._lock:
            state = self._load_state()
        items = [i for i in state["queue"].values() if i["job"] == job_path]
        in_queue = any(self._phase(i, now) == "queued" for i in items)
        last_build = None
        started = [i for i in items if self._phase(i, now) in ("running", "finished")]
        if started:
            last = max(started, key=lambda i: i["number"])
            phase = self._phase(last, now)
            last_build = {
                "number": last["number"],
                "building": phase == "running",
                "result": last["result"] if phase == "finished" else None,
            }
        props = []
        if job.get("parameters"):
            props.append({"parameterDefinitions": job["parameters"]})
        return {
            "buildable": job.get("buildable", True),
            "inQueue": in_queue or bool(job.get("mock_in_queue")),
            "lastBuild": last_build,
            "property": props,
        }

    def trigger(self, job_path: str, params: dict[str, str], with_params: bool) -> int:
        self._job(job_path)
        now = self.clock()
        with self._lock:
            state = self._load_state()
            qid = state["next_queue_id"]
            state["next_queue_id"] = qid + 1
            number = state["next_build"].get(job_path, 1)
            state["next_build"][job_path] = number + 1
            forced = (params or {}).get("MOCK_RESULT")
            if forced:
                result = forced.upper()
            else:
                r = random.random()
                acc = 0.0
                result = RESULT_WEIGHTS[-1][0]
                for name, w in RESULT_WEIGHTS:
                    acc += w
                    if r < acc:
                        result = name
                        break
            span = max(self.max_seconds - self.min_seconds, 0)
            queue_wait = min(1.0, self.min_seconds / 2)
            state["queue"][str(qid)] = {
                "job": job_path,
                "params": params,
                "with_params": with_params,
                "created": now,
                "start_at": now + queue_wait,
                "end_at": now + self.min_seconds + random.random() * span,
                "result": result,
                "number": number,
            }
            self._save_state(state)
        self.trigger_calls.append({"job": job_path, "params": params, "with_params": with_params})
        return qid

    def get_queue_item(self, queue_id: int) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
        item = state["queue"].get(str(queue_id))
        if not item:
            raise NotFound(f"queue item {queue_id}")
        phase = self._phase(item, self.clock())
        data: dict[str, Any] = {"id": queue_id, "cancelled": phase == "cancelled", "executable": None}
        if phase in ("running", "finished"):
            data["executable"] = {
                "number": item["number"],
                "url": f"{self._job_url(item['job'])}{item['number']}/",
            }
        return data

    def get_build(self, job_path: str, number: int) -> dict[str, Any]:
        with self._lock:
            state = self._load_state()
        for item in state["queue"].values():
            if item["job"] == job_path and item["number"] == number:
                phase = self._phase(item, self.clock())
                return {
                    "number": number,
                    "building": phase == "running",
                    "result": item["result"] if phase == "finished" else None,
                    "url": f"{self._job_url(job_path)}{number}/",
                    "timestamp": int(item["start_at"] * 1000),
                    "duration": int((item["end_at"] - item["start_at"]) * 1000),
                }
        raise NotFound(f"build {job_path}#{number}")

    def find_build_by_queue_id(self, job_path: str, queue_id: int) -> dict[str, Any] | None:
        try:
            q = self.get_queue_item(queue_id)
        except NotFound:
            return None
        if q.get("executable"):
            return self.get_build(job_path, q["executable"]["number"])
        return None

    def get_recent_timer_builds(self, job_path: str, days: int) -> list[dict[str, Any]]:
        job = self._job(job_path)
        if not job.get("timer_trigger"):
            return []
        now_ms = int(self.clock() * 1000)
        builds = [
            {
                "number": 100,
                "timestamp": now_ms - 3600_000,
                "actions": [{"causes": [{"_class": TIMER_TRIGGER_CAUSE}]}],
            }
        ]
        return filter_timer_builds(builds, now_ms - days * 86400_000)

    # テスト・デバッグ用
    def cancel_queue_item(self, queue_id: int) -> None:
        with self._lock:
            state = self._load_state()
            state["queue"][str(queue_id)]["cancelled"] = True
            self._save_state(state)
