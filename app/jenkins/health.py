"""Jenkins の疎通状態。何人が画面を開いていても、Jenkins への問い合わせは一定間隔に1回だけにする。"""

from __future__ import annotations

import threading
import time

from app.jenkins.base import JenkinsClientProtocol


class JenkinsHealth:
    def __init__(self, client: JenkinsClientProtocol, interval_seconds: float = 30.0, clock=time.monotonic):
        self.client = client
        self.interval = interval_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._checking = False
        self._result: str | None = None  # "ok" または "error: ..."
        self._checked_at: float | None = None

    def status(self) -> str:
        """最後の結果を返す。古ければ（間隔を過ぎていれば）1回だけ問い合わせ直す。"""
        with self._lock:
            fresh = self._checked_at is not None and self.clock() - self._checked_at < self.interval
            if fresh or self._checking:
                # 他の人の確認中は、前の結果を返して待たせない
                return self._result or "checking"
            self._checking = True
        try:
            self.client.ping()
            result = "ok"
        except Exception as exc:  # 接続できない理由はそのまま表示する
            result = f"error: {exc}"
        with self._lock:
            self._result = result
            self._checked_at = self.clock()
            self._checking = False
        return result
