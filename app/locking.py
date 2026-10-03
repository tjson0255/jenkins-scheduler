"""多重起動防止ロック（仕様書 14.1）。

OS のファイルロック（Windows は msvcrt、それ以外は fcntl。どちらも Python の標準ライブラリ）を使う。
プロセスが異常終了しても、OS がロックを解放するので残らない。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


def _try_lock(fh) -> bool:
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fh) -> None:
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class ProcessLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="ascii")
        if not _try_lock(fh):
            fh.close()
            raise AlreadyRunning(
                f"別のプロセスが起動中です（ロックファイル: {self.path}）。サービスとコンソールの二重起動を確認してください"
            )
        # 先頭1バイトをロックしているので、PID はその後ろに書く（Windows のロックは範囲単位）
        fh.seek(0)
        fh.truncate()
        fh.write(f" {os.getpid()}\n")
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is not None:
            _unlock(self._fh)
            self._fh.close()
            self._fh = None
