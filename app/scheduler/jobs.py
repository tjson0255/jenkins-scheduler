"""定期処理の実行（dispatcher の tick、パラメータ定義の取得、毎日のバックアップなど）。

外部ライブラリを使わず、ジョブごとに1本のスレッドで「待つ → 実行する」を繰り返す。
- 同じジョブが重なって動くことはない（前回が終わってから次を待つ）
- 実行が長引いて予定を過ぎた分は、まとめて1回だけ実行する
- 停止するときは、実行中のジョブが終わるのを待つ
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from app.timeutil import local_tz

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._jobs: list[tuple[str, Callable[[], float], Callable[..., Any], tuple]] = []

    def add_interval(self, job_id: str, func: Callable[..., Any], seconds: float, *, args: tuple = (), first_delay: float | None = None) -> None:
        """seconds ごとに実行する。first_delay 秒後に1回目（省略時は seconds 後）。

        実行にかかった時間で予定がずれないよう、開始時刻を基準に seconds 刻みで実行する。
        """
        state: dict[str, float | None] = {"next": None}

        def wait_seconds() -> float:
            now = time.monotonic()
            if state["next"] is None:
                state["next"] = now + (seconds if first_delay is None else first_delay)
            else:
                state["next"] += seconds
                while state["next"] <= now - seconds:  # 実行が長引いて飛ばした回は、まとめて1回にする
                    state["next"] += seconds
            return state["next"] - now

        self._jobs.append((job_id, wait_seconds, func, args))

    def add_daily(self, job_id: str, func: Callable[..., Any], hour: int, minute: int, *, args: tuple = ()) -> None:
        """毎日 hour:minute（APP_TZ の時刻）に実行する。"""

        def wait_seconds() -> float:
            now = datetime.now(local_tz())
            at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if at <= now:
                at = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return (at - now).total_seconds()

        self._jobs.append((job_id, wait_seconds, func, args))

    def _loop(self, job_id: str, wait_seconds: Callable[[], float], func: Callable[..., Any], args: tuple) -> None:
        while not self._stop.wait(max(0.0, wait_seconds())):
            try:
                func(*args)
            except Exception:  # 1回の失敗で定期処理を止めない
                log.exception("定期処理 %s でエラーが発生しました", job_id)

    def start(self) -> None:
        for job_id, wait_seconds, func, args in self._jobs:
            t = threading.Thread(target=self._loop, args=(job_id, wait_seconds, func, args), name=f"job-{job_id}", daemon=True)
            t.start()
            self._threads.append(t)

    def shutdown(self, timeout: float | None = 60) -> None:
        """新しい実行を止め、実行中のものが終わるのを待つ。"""
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads.clear()
