"""run の状態機械と tick（仕様書 9.1）。

tick ごとに
  1. 終了日を過ぎたスケジューラを ended にする
  2. run を補充する
  3. 期限が来た scheduled の run を、遅延判定 → キック直前検証 → 重複チェック → キック の順に処理する
  4. queued / running の run の状態を1段ずつ進める（ブロッキング待ちはしない）
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, metrics
from app.config import Settings
from app.jenkins.base import JenkinsClientProtocol, JenkinsError, NotFound
from app.models import (
    ACTIVE,
    ENDED,
    PAUSED,
    R_ABORTED,
    R_CANCELLED,
    R_FAILURE,
    R_HOLDING,
    R_MISSED,
    R_QUEUED,
    R_RUNNING,
    R_SCHEDULED,
    R_SKIPPED,
    R_SUCCESS,
    R_UNSTABLE,
    AppState,
    Run,
    Schedule,
)
from app.scheduler import planner, runstate
from app.schema import diff
from app.schema.service import SchemaState, evaluate_schedule, fetch_schema
from app.schema.validate import validate_explicit
from app.timeutil import iso_z, local_today, utcnow

log = logging.getLogger(__name__)

RESULT_MAP = {
    "SUCCESS": R_SUCCESS,
    "UNSTABLE": R_UNSTABLE,
    "FAILURE": R_FAILURE,
    "ABORTED": R_ABORTED,
    "NOT_BUILT": R_ABORTED,
}
LAST_TICK_KEY = "dispatcher_last_tick"


def set_status(run: Run, status: str, reason: str | None = None) -> None:
    run.status = status
    if reason is not None:
        run.reason = reason
    if status in (R_SUCCESS, R_UNSTABLE, R_FAILURE, R_ABORTED, R_SKIPPED, R_MISSED, R_CANCELLED):
        run.finished_at = utcnow()
    metrics.observe_run_status(run.target.job_path, status)


class KickBlocked(Exception):
    def __init__(self, issues: list[dict]):
        super().__init__("; ".join(i["message"] for i in issues))
        self.issues = issues


class Dispatcher:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        client: JenkinsClientProtocol,
        settings: Settings,
    ):
        self.session_factory = session_factory
        self.client = client
        self.settings = settings
        self.lock = threading.RLock()
        self.last_tick: datetime | None = None
        self._gap: tuple[datetime, datetime] | None = None
        self._first_tick_done = False

    # ------------------------------------------------------------------ 起動時
    def startup_check(self, now: datetime | None = None) -> None:
        """前回停止からの空白時間を記録する。扱い（実行・見逃し）は最初の tick 後に集計する。"""
        now = now or utcnow()
        with self.session_factory() as db:
            state = db.get(AppState, LAST_TICK_KEY)
            resumed = db.scalar(select(func.count()).select_from(Run).where(Run.status.in_((R_QUEUED, R_RUNNING))))
            if resumed:
                log.info("queued/running の run %d 件の状態追跡を再開します（再キックしません）", resumed)
            # 確保したままキックの結果を記録できずに止まった run は、Jenkins に送ったか分からないので保留にする
            stuck = db.scalars(select(Run).where(Run.status == R_SCHEDULED, Run.triggered_at.is_not(None))).all()
            for r in stuck:
                set_status(r, R_HOLDING, "キック処理の途中で停止しました。二重起動を避けるため保留にしています（Jenkins で実行されたか確認してから保留解除してください）")
                log.warning("run %s はキック処理中に停止していたため保留にしました", r.id)
            db.commit()
            if state:
                last = datetime.fromisoformat(state.value)
                gap = now - last
                if gap > timedelta(seconds=self.settings.dispatch_interval_seconds * 3):
                    self._gap = (last, now)
                    log.info("前回の tick から %s 経過して起動しました（%s 〜 %s）", gap, iso_z(last), iso_z(now))

    def _report_gap(self, db: Session) -> None:
        if not self._gap:
            return
        start, end = self._gap
        rows = db.execute(
            select(Run.status, func.count())
            .where(Run.scheduled_at > start, Run.scheduled_at <= end, Run.schedule_id.is_not(None))
            .group_by(Run.status)
        ).all()
        counts = {status: n for status, n in rows}
        detail = {"from": iso_z(start), "to": iso_z(end), "runs": sum(counts.values()), "by_status": counts}
        log.info("停止期間中の run: %s", detail)
        audit.record(db, audit.SYSTEM, "startup_gap", "system", None, detail)
        self._gap = None

    # ------------------------------------------------------------------ tick
    def tick(self, now: datetime | None = None) -> None:
        with self.lock:
            now = now or utcnow()
            try:
                with self.session_factory() as db:
                    self._end_schedules(db, now)
                    self.fill_all(db, now)
                    db.commit()
                    self._dispatch_due(db, now)
                    self._track_active(db)
                    if not self._first_tick_done:
                        self._report_gap(db)
                        self._first_tick_done = True
                    self._save_last_tick(db, now)
                    db.commit()
                    metrics.runs_holding.set(
                        db.scalar(select(func.count()).select_from(Run).where(Run.status == R_HOLDING)) or 0
                    )
            except Exception:
                log.exception("dispatcher tick で予期しないエラー")
            finally:
                self.last_tick = now
                metrics.last_tick.set(time.time())

    def _save_last_tick(self, db: Session, now: datetime) -> None:
        state = db.get(AppState, LAST_TICK_KEY)
        if state is None:
            db.add(AppState(key=LAST_TICK_KEY, value=now.isoformat()))
        else:
            state.value = now.isoformat()

    def _end_schedules(self, db: Session, now: datetime) -> None:
        today = local_today()
        for s in db.scalars(
            # 予定は実行しないので「終了」にしない（過去の予定として画面で薄く表示する）
            select(Schedule).where(
                Schedule.status.in_((ACTIVE, PAUSED)), Schedule.end_date.is_not(None), Schedule.mode != "memo"
            )
        ):
            if s.end_date < today:
                s.status = ENDED
                s.revision += 1  # 画面で開いている人の古い内容で上書きされないように
                n = planner.cancel_pending_runs(db, s, R_CANCELLED, "スケジューラ終了")
                audit.record(db, audit.SYSTEM, "schedule.ended", "schedule", s.id, {"cancelled_runs": n})

    def fill_all(self, db: Session, now: datetime | None = None) -> int:
        now = now or utcnow()
        threshold = now + timedelta(days=self.settings.run_horizon_days) - timedelta(hours=1)
        total = 0
        for s in db.scalars(select(Schedule).where(Schedule.status.in_(planner.GENERATING_STATUSES))):
            if s.generated_until and s.generated_until >= threshold:
                continue
            total += planner.fill_runs(
                db, s, now, self.settings.run_horizon_days, self.settings.catchup_max_days
            )
        return total

    def _dispatch_due(self, db: Session, now: datetime) -> None:
        due = db.scalars(
            select(Run)
            .outerjoin(Schedule, Run.schedule_id == Schedule.id)
            .where(
                Run.status == R_SCHEDULED,
                Run.triggered_at.is_(None),  # 確保済み（キック処理中）のものは除く
                Run.scheduled_at <= now,
                (Schedule.status == ACTIVE) | (Run.schedule_id.is_(None)),
            )
            .order_by(Run.scheduled_at)
        ).all()
        for run in due:
            try:
                self.process_due(db, run, now)
            except Exception:
                log.exception("run %s の処理に失敗", run.id)
                db.rollback()
            else:
                db.commit()

    # ------------------------------------------------------------------ 1件の処理
    def is_late(self, run: Run, now: datetime) -> str | None:
        """遅延していて実行しない場合は理由を返す。"""
        s = run.schedule
        if s is None:
            return None
        late = now - run.scheduled_at
        tolerance = timedelta(seconds=self.settings.dispatch_interval_seconds * 2)
        if late <= tolerance:
            return None
        minutes = int(late.total_seconds() // 60)
        if s.missed_policy == "skip":
            return f"予定時刻から {minutes} 分遅延（missed_policy=skip）"
        if late > timedelta(minutes=s.grace_minutes):
            return f"予定時刻から {minutes} 分遅延（猶予 {s.grace_minutes} 分を超過）"
        return None

    def prepare(self, db: Session, run: Run, now: datetime) -> tuple[dict[str, str], SchemaState, list[dict]]:
        """キック直前の検証（8.4）。スキーマを取り直し、展開・検証する。"""
        state = fetch_schema(db, self.client, run.target)
        if run.schedule is not None:
            params, _detail, issues = evaluate_schedule(db, run.schedule, state, run.scheduled_at)
        else:
            issues = diff.job_status_issues(state.info, state.error)
            params, v = validate_explicit(state.defs, run.params_json or {})
            issues += v
        return params, state, issues

    def process_due(self, db: Session, run: Run, now: datetime) -> Run:
        """予定時刻が来た run を処理する。キックする前に必ず run を確保（claim）する。"""
        db.refresh(run)  # API 側で状態が変わっていないか、最新を読む
        if run.status != R_SCHEDULED or run.triggered_at is not None:
            return run
        if not run.target.enabled:
            runstate.transition(db, run, R_SKIPPED, "アイテムが無効です")
            return run
        late_reason = self.is_late(run, now)
        if late_reason:
            if runstate.transition(db, run, R_MISSED, late_reason):
                log.warning("run %s missed: %s", run.id, late_reason)
            return run
        if not runstate.claim(db, run, utcnow()):
            # スキップ・キャンセル・一時停止・他のキック処理が先だった
            log.info("run %s は他の操作で状態が変わったためキックしません（%s）", run.id, run.status)
            return run
        return self.kick_claimed(db, run, now, retry_on_jenkins_error=True)

    def kick_claimed(self, db: Session, run: Run, now: datetime, retry_on_jenkins_error: bool = False) -> Run:
        """確保済みの run を検証してキックする。ここに来た run はこの処理だけが触る。"""
        target = run.target

        def release(status: str, reason: str) -> Run:
            # 確保を外して終える（holding は後で保留解除できるよう triggered_at を空に戻す）
            run.triggered_at = None
            set_status(run, status, reason)
            db.commit()
            return run

        try:
            params, state, issues = self.prepare(db, run, now)
        except JenkinsError as exc:
            log.warning("run %s のキック前検証で Jenkins エラー: %s", run.id, exc)
            if retry_on_jenkins_error:
                # 一時的な通信エラー: 確保を外して scheduled に戻す（猶予内なら次の tick で再試行）
                run.triggered_at = None
                db.commit()
                return run
            return release(R_HOLDING, f"キック前の検証で Jenkins と通信できませんでした: {exc}")
        errors = [i for i in issues if i["level"] == diff.ERROR]
        if errors:
            run.schema_hash = state.hash
            log.warning("run %s holding: %s", run.id, errors)
            return release(R_HOLDING, "; ".join(i["message"] for i in errors))

        info = state.info or {}
        last_build = info.get("lastBuild") or {}
        if (info.get("inQueue") or last_build.get("building")) and target.overlap_policy == "skip":
            return release(R_SKIPPED, "前回ビルドが実行中またはキュー中")

        run.params_json = params
        run.schema_hash = state.hash
        run.triggered_at = utcnow()
        db.commit()
        try:
            queue_id = self.client.trigger(target.job_path, params, with_params=state.has_params)
        except JenkinsError as exc:
            # キックは自動リトライしない（二重起動防止）。triggered_at は残し、送ったかもしれない印にする
            set_status(run, R_HOLDING, f"キックに失敗しました（自動再試行しません）: {exc}")
            db.commit()
            log.error("run %s のキックに失敗: %s", run.id, exc)
            return run
        run.queue_id = queue_id
        run.reason = None
        set_status(run, R_QUEUED)
        db.commit()
        log.info("run %s をキックしました job=%s queue_id=%s", run.id, target.job_path, queue_id)
        return run

    # ------------------------------------------------------------------ 追跡
    def _track_active(self, db: Session) -> None:
        runs = db.scalars(select(Run).where(Run.status.in_((R_QUEUED, R_RUNNING)))).all()
        for run in runs:
            try:
                self.track(run)
            except JenkinsError as exc:
                log.warning("run %s の追跡で Jenkins エラー: %s", run.id, exc)
            except Exception:
                log.exception("run %s の追跡に失敗", run.id)
            db.commit()

    def track(self, run: Run) -> None:
        job = run.target.job_path
        if run.status == R_QUEUED:
            if run.queue_id is None:
                set_status(run, R_FAILURE, "キューIDがありません")
                return
            try:
                item = self.client.get_queue_item(run.queue_id)
            except NotFound:
                # キューアイテムは一定時間で消えるので、ビルド一覧から queueId で探す
                build = self.client.find_build_by_queue_id(job, run.queue_id)
                if build is None:
                    set_status(run, R_FAILURE, "キューアイテムもビルドも見つからず追跡できません")
                    return
                run.build_number = build.get("number")
                run.build_url = build.get("url")
                set_status(run, R_RUNNING)
                return
            if item.get("cancelled"):
                set_status(run, R_CANCELLED, "Jenkins のキューでキャンセルされました")
                return
            executable = item.get("executable")
            if not executable:
                return
            run.build_number = executable.get("number")
            run.build_url = executable.get("url")
            set_status(run, R_RUNNING)
            # 同じ tick 内で結果まで確認できれば反映する（待ちはしない）
        if run.status == R_RUNNING and run.build_number is not None:
            build = self.client.get_build(job, run.build_number)
            run.build_url = build.get("url") or run.build_url
            if build.get("building"):
                return
            result = build.get("result")
            if result is None:
                return
            set_status(run, RESULT_MAP.get(result, R_FAILURE), None if result in RESULT_MAP else f"結果: {result}")

    # ------------------------------------------------------------------ 即時実行
    def kick_now(self, db: Session, run: Run) -> Run:
        """API から即時キックする（保留解除・今すぐ実行・再実行）。

        呼び出し側で run を確保済み（triggered_at を設定済み）にしておくこと。遅延判定はしない。
        dispatcher の tick とはロックで待ち合わせず、確保の有無で排他する（tick が長引いても画面を待たせない）。
        """
        db.refresh(run)
        if run.status != R_SCHEDULED or run.triggered_at is None:
            return run
        return self.kick_claimed(db, run, utcnow())
