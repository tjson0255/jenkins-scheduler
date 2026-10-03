"""Prometheus メトリクス（仕様書 10.1）。

外部ライブラリを使わず、必要な分（ラベル付きの Counter / Gauge と、テキスト形式の出力）だけを実装する。
"""

from __future__ import annotations

import threading
import time

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
_registry: list[_Metric] = []


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Child:
    def __init__(self, parent: _Metric, key: tuple[str, ...]):
        self._parent = parent
        self._key = key

    def inc(self, amount: float = 1) -> None:
        with self._parent._lock:
            self._parent._values[self._key] = self._parent._values.get(self._key, 0.0) + amount

    def set(self, value: float) -> None:
        with self._parent._lock:
            self._parent._values[self._key] = float(value)

    def set_to_current_time(self) -> None:
        self.set(time.time())


class _Metric:
    kind = ""

    def __init__(self, name: str, documentation: str, labelnames: list[str] | tuple[str, ...] = ()):
        self.name = name
        self.documentation = documentation
        self.labelnames = tuple(labelnames)
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()
        if not self.labelnames:
            self._values[()] = 0.0
        _registry.append(self)

    def labels(self, **labels: str) -> _Child:
        return _Child(self, tuple(str(labels[n]) for n in self.labelnames))

    def clear(self) -> None:
        with self._lock:
            self._values = {} if self.labelnames else {(): 0.0}

    # ラベルの無いメトリクスはそのまま操作する
    def inc(self, amount: float = 1) -> None:
        _Child(self, ()).inc(amount)

    def set(self, value: float) -> None:
        _Child(self, ()).set(value)

    def set_to_current_time(self) -> None:
        _Child(self, ()).set_to_current_time()

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {_escape(self.documentation)}", f"# TYPE {self.name} {self.kind}"]
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            labels = ",".join(f'{n}="{_escape(v)}"' for n, v in zip(self.labelnames, key))
            lines.append(f"{self.name}{{{labels}}} {value!r}" if labels else f"{self.name} {value!r}")
        return lines


class Counter(_Metric):
    kind = "counter"


class Gauge(_Metric):
    kind = "gauge"


def generate_latest() -> bytes:
    lines: list[str] = []
    for m in _registry:
        lines.extend(m.render())
    return ("\n".join(lines) + "\n").encode("utf-8")


last_tick = Gauge(
    "jenkins_scheduler_dispatcher_last_tick_timestamp_seconds", "dispatcher が最後に tick した時刻 (unix 秒)"
)
runs_total = Counter("jenkins_scheduler_runs_total", "run の状態遷移数", ["target", "status"])
runs_holding = Gauge("jenkins_scheduler_runs_holding", "holding 状態の run 数")
runs_missed_total = Counter("jenkins_scheduler_runs_missed_total", "missed になった run 数", ["target"])
jenkins_api_errors_total = Counter(
    "jenkins_scheduler_jenkins_api_errors_total", "Jenkins API エラー数", ["kind"]
)
schema_drift = Gauge(
    "jenkins_scheduler_schema_drift", "未実行 run を持つスケジュールのスキーマ差分件数", ["target", "level"]
)
backup_last_success = Gauge(
    "jenkins_scheduler_backup_last_success_timestamp_seconds", "最後にバックアップに成功した時刻 (unix 秒)"
)
backup_failures_total = Counter("jenkins_scheduler_backup_failures_total", "バックアップの失敗回数")
timer_trigger_detected = Gauge(
    "jenkins_scheduler_timer_trigger_detected", "Jenkins 側 cron (TimerTrigger) の残存検出 (1=検出)", ["target"]
)


def observe_run_status(target_job_path: str, status: str) -> None:
    runs_total.labels(target=target_job_path, status=status).inc()
    if status == "missed":
        runs_missed_total.labels(target=target_job_path).inc()
