"""ログ設定。logs/scheduler.log に JSON Lines で日次ローテーション（14世代）。"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone

from app.config import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


class _RedactFilter(logging.Filter):
    """念のため Jenkins トークンがログに混ざったら伏せる。"""

    def __init__(self, secret: str):
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secret and self.secret in record.getMessage():
            record.msg = record.getMessage().replace(self.secret, "***")
            record.args = ()
        return True


_configured = False


def setup_logging(settings: Settings) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handlers: list[logging.Handler] = [console]
    if settings.log_to_file:
        settings.logs_path.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.TimedRotatingFileHandler(
            settings.logs_path / "scheduler.log", when="midnight", backupCount=14, encoding="utf-8"
        )
        fh.setFormatter(JsonFormatter())
        handlers.append(fh)
    redact = _RedactFilter(settings.jenkins_token)
    for h in handlers:
        h.addFilter(redact)
        root.addHandler(h)
    # httpx は URL をログに出すので WARNING 以上に絞る
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
