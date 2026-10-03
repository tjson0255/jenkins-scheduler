"""定期処理の実行（app/scheduler/jobs.py）。"""

import threading
import time

from app.scheduler.jobs import JobRunner


def test_interval_runs_repeatedly_without_overlap_and_shutdown_waits():
    calls = []
    running = threading.Lock()
    overlaps = []

    def job(tag):
        if not running.acquire(blocking=False):
            overlaps.append(1)
            return
        try:
            calls.append(tag)
            time.sleep(0.03)  # 間隔より長くかかっても重ならない
        finally:
            running.release()

    r = JobRunner()
    r.add_interval("t", job, 0.02, args=("x",), first_delay=0)
    r.start()
    time.sleep(0.25)
    r.shutdown()
    n = len(calls)
    time.sleep(0.05)
    assert n >= 3 and overlaps == [] and len(calls) == n  # 停止後は動かない


def test_job_error_does_not_stop_the_loop():
    calls = []

    def job():
        calls.append(1)
        raise RuntimeError("boom")

    r = JobRunner()
    r.add_interval("t", job, 0.01, first_delay=0)
    r.start()
    time.sleep(0.1)
    r.shutdown()
    assert len(calls) >= 3


def test_daily_waits_until_next_occurrence():
    r = JobRunner()
    r.add_daily("d", lambda: None, 0, 0)
    wait = r._jobs[0][1]()
    assert 0 < wait <= 24 * 3600
