"""公開デモ用のデータ（DEMO_MODE=true）。

起動時と DEMO_RESET_HOURS ごとに、すべてのデータを消して、今日の日付に合わせたデモ用データを入れ直す。
Jenkins はモック（tests/fixtures/jenkins の架空のジョブ）だけを使う。
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import audit
from app.config import PROJECT_ROOT, Settings
from app.models import (
    ACTIVE,
    DRAFT,
    ENDED,
    ITEM_MEMO,
    PAUSED,
    R_FAILURE,
    R_HOLDING,
    R_SCHEDULED,
    R_SKIPPED,
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
from app.scheduler.history import schedule_title
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
    """いろいろな機能を使っている状態のデモ用データ（日付は今日を基準にする）。

    - 同じレーンで、日によって件名・パラメータの違うスケジューラ（リリース候補ビルドの RC1 / RC2）
    - 他スケジューラ停止（Core の hotfix 検証の期間は、通常の nightly を止める）
    - この回だけ変更（Core の明日の nightly だけ 05:00・BUILD_TYPE=profile に）
    - 有効・一時停止・ドラフト・終了のスケジューラ、パラメータの変数、メモ
    - 過去の実行履歴（件名・メモ・送ったパラメータ付き。削除済みのスケジューラの回も）
    - パラメータ定義の警告（iOS）、保留の回、ビルドできないジョブ、Jenkins 側の cron の残存、テキストのレーンの予定
    """
    rng = random.Random(42)
    today = local_today()
    now = utcnow()
    load_seed(db, PROJECT_ROOT / "seed.toml.example", settings.default_overlap_policy)
    targets = {t.job_path: t for t in db.query(Target).all()}
    for t in targets.values():
        sync_target(db, client, t, settings.timer_trigger_lookback_days)
    db.commit()

    def local(d, hh, mm=0):
        return to_utc_naive(datetime.combine(d, time(hh, mm), tzinfo=local_tz()))

    def md_hm(dt):
        lt = dt.replace(tzinfo=timezone.utc).astimezone(local_tz())
        return f"{lt.month}/{lt.day} {lt.hour:02d}:{lt.minute:02d}"

    def sched(job, overrides=None, **kw) -> Schedule:
        t = targets[job]
        s = Schedule(target=t, missed_policy="skip", grace_minutes=10, baseline_schema_hash=t.schema_hash, **kw)
        for name, value in (overrides or {}).items():
            s.overrides.append(ParamOverride(param_name=name, value_template=value))
        db.add(s)
        db.flush()
        return s

    def build_url(job, number):
        return f"http://mock-jenkins{''.join('/job/' + p for p in job.split('/'))}/{number}/"

    def past_run(target, at, params, title, note=None, schedule=None, status=None, number=None):
        """過去の実行結果（成功が多く、ときどき失敗・不安定）。件名・メモ・送ったパラメータも残す。"""
        status = status or rng.choices([R_SUCCESS, R_FAILURE, R_UNSTABLE], [0.82, 0.1, 0.08])[0]
        number = number or rng.randint(100, 999)
        db.add(Run(
            schedule_id=schedule.id if schedule else None, target_id=target.id, scheduled_at=at, status=status,
            triggered_at=at, finished_at=at + timedelta(minutes=rng.randint(8, 40)), params_json=params,
            title_snapshot=title, note_snapshot=note, queue_id=number, build_number=number, build_url=build_url(target.job_path, number),
            reason={R_FAILURE: "テスト失敗 3 件", R_UNSTABLE: "警告のあるテスト 2 件"}.get(status),
        ))

    def history(s: Schedule, days: int, params) -> None:
        start = local(today - timedelta(days=days), 0)
        for n, at in enumerate(iter_occurrences(s.cron_expr, start, now - timedelta(minutes=5)), start=1):
            lt_date = at.replace(tzinfo=timezone.utc).astimezone(local_tz()).date().isoformat()
            past_run(s.target, at, params(lt_date), schedule_title(s), s.note, schedule=s, number=100 + n)

    # ---- ビルドセット ----
    core = sched("buildset/core-pipeline", label="nightly", start_date=today - timedelta(days=14), mode="cron", cron_expr="0 3 * * *", status=ACTIVE,
                 overrides={"VERSION": "{{schedule.title}}-{{run.date}}", "BUILD_TYPE": "release"},
                 note="毎晩の通常ビルド。\nhotfix の検証期間は止まります。")
    history(core, 14, lambda d: {"BRANCH": "main", "BUILD_TYPE": "release", "RUN_TESTS": "true", "VERSION": f"nightly-{d}"})
    # 他スケジューラ停止: hotfix の検証期間（2日間）は、Core の nightly を止める
    sched("buildset/core-pipeline", label="v2.4.1 hotfix 検証", start_date=today + timedelta(days=3), end_date=today + timedelta(days=4),
          mode="cron", cron_expr="0 2 * * *", status=ACTIVE, exclusive=True,
          overrides={"BRANCH": "hotfix/2.4.1", "BUILD_TYPE": "debug", "VERSION": "2.4.1-hotfix-{{run.date}}"},
          note="hotfix ブランチの検証。この2日間は nightly を止める（他スケジューラ停止）。")
    android = sched("buildset/android-pipeline", start_date=today - timedelta(days=30), mode="cron", cron_expr="30 2 * * 1-5", status=ACTIVE,
                    note="平日だけ。休日は Jenkins を止めている")
    history(android, 14, lambda d: {"BRANCH": "main", "BUILD_TYPE": "debug", "RUN_TESTS": "true", "VERSION": ""})
    # パラメータ定義の警告の例: iOS のジョブには RUN_TESTS が無い（RUN_TESTS2 に変わった）
    ios = sched("buildset/ios-pipeline", label="v2.4.0", start_date=today - timedelta(days=3), end_date=today + timedelta(days=9),
                mode="cron", cron_expr="0 */6 * * *", status=ACTIVE, overrides={"VERSION": "{{schedule.title}}", "RUN_TESTS": "true"},
                note="RC 期間は6時間ごと。Jenkins 側でパラメータ名が変わったので要確認（警告の例）")
    history(ios, 3, lambda d: {"BRANCH": "release/2.4", "BUILD_TYPE": "release", "VERSION": "v2.4.0"})
    sched("buildset/web-pipeline", start_date=today - timedelta(days=7), mode="cron", cron_expr="0 1 * * *", status=PAUSED,
          note="Web 側の改修中のため一時停止")
    sched("buildset/server-pipeline", label="負荷試験", start_date=today + timedelta(days=4), end_date=today + timedelta(days=6),
          mode="cron", cron_expr="0 22 * * *", status=DRAFT, overrides={"BUILD_TYPE": "profile"}, note="インフラチームの承認待ち（ドラフト）")
    sched("buildset/windows-pipeline", start_date=today - timedelta(days=2), mode="cron", cron_expr="0 4 * * 6", status=DRAFT)
    sched("buildset/tools-pipeline", label="週次ツール更新", start_date=today - timedelta(days=21), mode="cron", cron_expr="0 5 * * 1", status=ACTIVE,
          note="Jenkins 側に古い cron が残っている（⏰ の例）。Jenkins の設定から消すこと")

    # ---- リリース関連: 同じレーンで、日によって件名・パラメータの違うスケジューラ ----
    rc = targets["release/release-candidate"]
    for label, days, version, channel, note in (
        ("v2.4.0 RC1", 2, "2.4.0-rc.1", "beta", "社内向けベータ配信"),
        ("v2.4.0 RC2", 5, "2.4.0-rc.2", "rc", "RC1 の不具合修正版。QA 確認後に公開"),
    ):
        d = today + timedelta(days=days)
        sched(rc.job_path, label=label, start_date=d, end_date=d, mode="once", once_at=local(d, 9), status=ACTIVE,
              overrides={"VERSION": version, "CHANNEL": channel, "RELEASE_NOTE": note}, note=note)
    # 削除済みのスケジューラの実行履歴（スケジューラを消しても、件名・パラメータ・メモ付きで残る）
    for n, (days, status) in enumerate(((10, R_SUCCESS), (9, R_UNSTABLE), (8, R_SUCCESS)), start=1):
        past_run(rc, local(today - timedelta(days=days), 9), {"VERSION": f"2.3.0-rc.{n}", "CHANNEL": "rc", "RELEASE_NOTE": "", "DRY_RUN": "false"},
                 "v2.3.0 RC 検証", "前回のリリース候補（スケジューラは削除済み）", status=status, number=40 + n)
    sched("release/store-submit", label="v2.4.0 申請", start_date=today + timedelta(days=10), end_date=today + timedelta(days=10),
          mode="once", once_at=local(today + timedelta(days=10), 10), status=DRAFT, overrides={"VERSION": "2.4.0", "STORE": "both"},
          note="両ストアへ同時申請。承認後に有効化する")

    # ---- その他 ----
    report = sched("misc/nightly-report", label="日次レポート", start_date=today - timedelta(days=6), mode="cron", cron_expr="0 7 * * *", status=ACTIVE,
                   overrides={"REPORT_DATE": "{{run.date}}", "MAIL_TO": "team@example.com"})
    history(report, 6, lambda d: {"REPORT_DATE": d, "MAIL_TO": "team@example.com"})
    # 終了したスケジューラ（「終了したものも表示」で出る）
    old = sched("misc/nightly-report", label="旧レポート形式", start_date=today - timedelta(days=20), end_date=today - timedelta(days=7),
                mode="cron", cron_expr="0 7 * * *", status=ENDED, overrides={"MAIL_TO": "old-list@example.com"}, note="新しい形式に移行したので終了")
    history_start = today - timedelta(days=13)
    for k in range(7):
        d = history_start + timedelta(days=k)
        past_run(old.target, local(d, 7), {"REPORT_DATE": d.isoformat(), "MAIL_TO": "old-list@example.com"}, "旧レポート形式", old.note, schedule=old)

    for s in db.query(Schedule).all():
        planner.fill_runs(db, s, now, settings.run_horizon_days)
    db.flush()

    # この回だけ変更: Core の明日の nightly だけ、05:00・BUILD_TYPE=profile に変える
    tomorrow_3 = local(today + timedelta(days=1), 3)
    original = db.query(Run).filter(Run.schedule_id == core.id, Run.scheduled_at == tomorrow_3).one_or_none()
    if original is not None:
        moved = local(today + timedelta(days=1), 5)
        original.status, original.finished_at = R_SKIPPED, now
        original.reason = f"この回だけ変更 → {md_hm(moved)}"
        db.add(Run(schedule_id=core.id, target_id=core.target_id, scheduled_at=moved, status=R_SCHEDULED, replaces_run_id=original.id,
                   override_params={"BUILD_TYPE": "profile"}, reason=f"{md_hm(tomorrow_3)} の回から変更"))

    # 保留の例: キック直前の確認で、Jenkins の選択肢に無い値だったため止めた回
    db.add(Run(schedule_id=None, target_id=targets["buildset/ios-pipeline"].id, scheduled_at=now - timedelta(hours=2), status=R_HOLDING,
               params_json={"BUILD_TYPE": "nightly"}, reason="パラメータ BUILD_TYPE の値「nightly」は選択肢にありません（デモ用の例）"))

    # ---- テキストのレーン（予定） ----
    plan_cat = db.query(Category).filter(Category.name == "リリース関連").one()
    memo_item = Target(kind=ITEM_MEMO, job_path=None, display_name="リリース計画", category_id=plan_cat.id, color="#9b7ccc", sort_order=0)
    db.add(memo_item)
    other_cat = db.query(Category).filter(Category.name == "その他").one()
    ops_item = Target(kind=ITEM_MEMO, job_path=None, display_name="運用メモ", category_id=other_cat.id, color="#8a94a6", sort_order=99)
    db.add(ops_item)
    db.flush()
    for item, label, start, end, note in (
        (memo_item, "v2.4 コードフリーズ", 1, 5, "マージは承認制。\n担当: 開発リーダー"),
        (memo_item, "QA 期間", 3, 9, "回帰テストと受け入れテスト。\n不具合は課題管理に起票"),
        (memo_item, "v2.4 リリース", 10, 10, "ストア申請後、段階公開を開始"),
        (memo_item, "v2.3 振り返り", -4, -4, "良かった点・改善点の共有"),
        (ops_item, "Jenkins メンテナンス", 6, 6, "22:00〜24:00 は Jenkins を止める。\nこの時間のビルドは入れない"),
        (ops_item, "リリース当番週", 0, 6, "当番: 開発チーム A"),
    ):
        db.add(Schedule(target=item, label=label, start_date=today + timedelta(days=start), end_date=today + timedelta(days=end), mode="memo", status=ACTIVE, note=note))
    audit.record(db, audit.SYSTEM, "demo.reset", "system", None, {"date": today.isoformat()})
    db.commit()
    log.info("デモ用データを入れました")
