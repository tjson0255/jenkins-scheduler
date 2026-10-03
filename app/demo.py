"""公開デモ用のデータ（DEMO_MODE=true）。

起動時と DEMO_RESET_HOURS ごとに、すべてのデータを消して、今日の日付に合わせたデモ用データを入れ直す。
Jenkins はモック（tests/fixtures/jenkins の架空のジョブ）だけを使う。
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, time, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import audit
from app.config import PROJECT_ROOT, Settings
from app.models import (
    ACTIVE,
    DRAFT,
    ITEM_MEMO,
    R_FAILURE,
    R_HOLDING,
    R_SUCCESS,
    R_UNSTABLE,
    AppState,
    Base,
    Category,
    ParamOverride,
    Run,
    Schedule,
    Target,
)
from app.scheduler import planner
from app.scheduler.cronutil import iter_occurrences
from app.scheduler.poller import sync_target
from app.seed import load_seed
from app.timeutil import local_today, to_utc_naive, local_tz, utcnow

log = logging.getLogger(__name__)


def reset(db: Session, settings: Settings, client) -> None:
    """デモのデータをすべて消して入れ直す。"""
    for table in reversed(Base.metadata.sorted_tables):
        if table.name != AppState.__tablename__:
            db.execute(delete(table))
    db.commit()
    state = settings.data_path / "mock_state.json"
    state.unlink(missing_ok=True)
    populate(db, settings, client)


def populate(db: Session, settings: Settings, client) -> None:
    rng = random.Random(42)
    today = local_today()
    now = utcnow()
    load_seed(db, PROJECT_ROOT / "seed.toml.example", settings.default_overlap_policy)
    targets = {t.job_path: t for t in db.query(Target).all()}
    for t in targets.values():
        sync_target(db, client, t, settings.timer_trigger_lookback_days)
    db.commit()

    def sched(job, **kw) -> Schedule:
        t = targets[job]
        s = Schedule(target=t, missed_policy="run_late", grace_minutes=10, baseline_schema_hash=t.schema_hash, **kw)
        db.add(s)
        db.flush()
        return s

    def history(s: Schedule, days: int) -> None:
        """過去の実行結果（成功が多く、ときどき失敗・不安定）。"""
        start = to_utc_naive(datetime.combine(today - timedelta(days=days), time.min, tzinfo=local_tz()))
        number = 100
        for at in iter_occurrences(s.cron_expr, start, now - timedelta(minutes=5)):
            number += 1
            status = rng.choices([R_SUCCESS, R_FAILURE, R_UNSTABLE], [0.82, 0.1, 0.08])[0]
            db.add(Run(
                schedule_id=s.id, target_id=s.target_id, scheduled_at=at, status=status,
                triggered_at=at, finished_at=at + timedelta(minutes=rng.randint(8, 40)), params_json={"BRANCH": "main"},
                queue_id=number, build_number=number,
                build_url=f"http://mock-jenkins{''.join('/job/' + p for p in s.target.job_path.split('/'))}/{number}/",
                reason="テスト失敗 3 件" if status == R_FAILURE else None,
            ))

    core = sched("buildset/core-pipeline", label="v2.4.0", start_date=today - timedelta(days=14), mode="cron", cron_expr="0 3 * * *", status=ACTIVE)
    core.overrides.append(ParamOverride(param_name="VERSION", value_template="{{schedule.label}}-{{run.date}}"))
    core.overrides.append(ParamOverride(param_name="BUILD_TYPE", value_template="release"))
    history(core, 14)
    android = sched("buildset/android-pipeline", start_date=today - timedelta(days=30), mode="cron", cron_expr="30 2 * * 1-5", status=ACTIVE)
    history(android, 14)
    ios = sched("buildset/ios-pipeline", label="v2.4.0", start_date=today - timedelta(days=3), end_date=today + timedelta(days=9), mode="cron", cron_expr="0 */6 * * *", status=ACTIVE)
    history(ios, 3)
    sched("buildset/web-pipeline", start_date=today - timedelta(days=7), mode="cron", cron_expr="0 1 * * *", status=ACTIVE)
    sched("buildset/server-pipeline", label="負荷試験", start_date=today + timedelta(days=4), end_date=today + timedelta(days=6), mode="cron", cron_expr="0 22 * * *", status=DRAFT)
    sched("buildset/windows-pipeline", start_date=today - timedelta(days=2), mode="cron", cron_expr="0 4 * * 6", status=DRAFT)
    sched("release/release-candidate", label="v2.4.0 RC", start_date=today + timedelta(days=2), end_date=today + timedelta(days=6), mode="cron", cron_expr="0 9 * * *", status=ACTIVE)
    once_at = to_utc_naive(datetime.combine(today + timedelta(days=10), time(10, 0), tzinfo=local_tz()))
    sched("release/store-submit", label="v2.4.0 申請", start_date=today + timedelta(days=10), end_date=today + timedelta(days=10), mode="once", once_at=once_at, status=DRAFT)

    for s in db.query(Schedule).all():
        planner.fill_runs(db, s, now, settings.run_horizon_days)

    # 保留の例: キック直前の確認で、Jenkins の選択肢に無い値だったため止めた run
    db.add(Run(schedule_id=None, target_id=targets["buildset/ios-pipeline"].id, scheduled_at=now - timedelta(hours=2), status=R_HOLDING,
               params_json={"BUILD_TYPE": "nightly"}, reason="パラメータ BUILD_TYPE の値「nightly」は選択肢にありません（デモ用の例）"))

    # 予定メモ
    plan_cat = db.query(Category).filter(Category.name == "リリース関連").one()
    memo_item = Target(kind=ITEM_MEMO, job_path=None, display_name="リリース計画", category_id=plan_cat.id, color="#9b7ccc", sort_order=0)
    db.add(memo_item)
    db.flush()
    for label, start, end, note in (
        ("v2.4 コードフリーズ", 1, 5, "マージは承認制。\n担当: 開発リーダー"),
        ("QA 期間", 3, 9, "回帰テストと受け入れテスト。\n不具合は Jira に起票"),
        ("v2.4 リリース", 10, 10, "ストア申請後、段階公開を開始"),
        ("v2.3 振り返り", -4, -4, "良かった点・改善点の共有"),
    ):
        db.add(Schedule(target=memo_item, label=label, start_date=today + timedelta(days=start), end_date=today + timedelta(days=end), mode="memo", status=ACTIVE, note=note))
    audit.record(db, audit.SYSTEM, "demo.reset", "system", None, {"date": today.isoformat()})
    db.commit()
    log.info("デモ用データを入れました")
