"""多重起動防止ロック（仕様書 14.1）。"""

from __future__ import annotations

import os
from pathlib import Path

import portalocker


class AlreadyRunning(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock: portalocker.Lock | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = portalocker.Lock(
            str(self.path),
            mode="a",
            timeout=0,
            fail_when_locked=True,
            flags=portalocker.LockFlags.EXCLUSIVE | portalocker.LockFlags.NON_BLOCKING,
        )
        try:
            fh = lock.acquire()
        except (portalocker.exceptions.AlreadyLocked, portalocker.exceptions.LockException) as exc:
            raise AlreadyRunning(
                f"別のプロセスが起動中です（ロックファイル: {self.path}）。サービスとコンソールの二重起動を確認してください"
            ) from exc
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        self._lock = lock

    def release(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None
