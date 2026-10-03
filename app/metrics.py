"""Prometheus メトリクス（仕様書 10.1）。"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

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
