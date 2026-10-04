"""データモデル（仕様書 6章）。日時はすべて naive UTC。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.timeutil import utcnow


class Base(DeclarativeBase):
    pass


# target.kind（レーンの種類）
ITEM_JENKINS = "jenkins"  # Jenkins ジョブをキックする
ITEM_MEMO = "memo"  # 予定。Jenkins には接続せず、予定を書くだけ
ITEM_KINDS = (ITEM_JENKINS, ITEM_MEMO)

# schedule.mode
MODE_CRON, MODE_ONCE, MODE_MEMO = "cron", "once", "memo"

# schedule.status
DRAFT, ACTIVE, PAUSED, ENDED, CANCELLED = "draft", "active", "paused", "ended", "cancelled"
SCHEDULE_STATUSES = (DRAFT, ACTIVE, PAUSED, ENDED, CANCELLED)

# run.status
R_SCHEDULED = "scheduled"
R_HOLDING = "holding"
R_QUEUED = "queued"
R_RUNNING = "running"
R_SUCCESS = "success"
R_UNSTABLE = "unstable"
R_FAILURE = "failure"
R_ABORTED = "aborted"
R_SKIPPED = "skipped"
R_MISSED = "missed"
R_CANCELLED = "cancelled"
PENDING_RUN_STATUSES = (R_SCHEDULED, R_HOLDING)
ACTIVE_RUN_STATUSES = (R_QUEUED, R_RUNNING)
FINISHED_RUN_STATUSES = (R_SUCCESS, R_UNSTABLE, R_FAILURE, R_ABORTED, R_SKIPPED, R_MISSED, R_CANCELLED)


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    targets: Mapped[list[Target]] = relationship(back_populates="category")


class Target(Base):
    __tablename__ = "target"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), default=ITEM_JENKINS, server_default=ITEM_JENKINS)
    job_path: Mapped[str | None] = mapped_column(String(500), unique=True, nullable=True)  # 予定は NULL
    display_name: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int] = mapped_column(ForeignKey("category.id"))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    overlap_policy: Mapped[str] = mapped_column(String(10), default="skip")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Jenkins 側の状態（ポーリング結果）
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # ジョブ無し・buildable=false 等
    timer_trigger_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 同時編集の検出用。利用者が変更するたびに増やし、保存時に読み込んだ時点の値と照合する
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    category: Mapped[Category] = relationship(back_populates="targets")

    @property
    def is_memo(self) -> bool:
        return self.kind == ITEM_MEMO

    schedules: Mapped[list[Schedule]] = relationship(
        back_populates="target", cascade="all, delete-orphan"
    )


class Schedule(Base):
    __tablename__ = "schedule"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("target.id"))
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    mode: Mapped[str] = mapped_column(String(10))  # cron | once | memo（テキストのレーンに書いた予定）
    cron_expr: Mapped[str | None] = mapped_column(String(100), nullable=True)
    once_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default=DRAFT)
    params_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    missed_policy: Mapped[str] = mapped_column(String(10), default="run_late")
    grace_minutes: Mapped[int] = mapped_column(Integer, default=10)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 実装上の補助カラム
    baseline_schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pinned_params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    generated_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 同時編集の検出用
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    target: Mapped[Target] = relationship(back_populates="schedules")

    @property
    def is_memo(self) -> bool:
        return self.mode == MODE_MEMO

    overrides: Mapped[list[ParamOverride]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )
    runs: Mapped[list[Run]] = relationship(back_populates="schedule", cascade="all, delete-orphan")

    def override_map(self) -> dict[str, str]:
        return {o.param_name: o.value_template for o in self.overrides}


class ParamOverride(Base):
    __tablename__ = "param_override"
    __table_args__ = (UniqueConstraint("schedule_id", "param_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedule.id", ondelete="CASCADE"))
    param_name: Mapped[str] = mapped_column(String(200))
    value_template: Mapped[str] = mapped_column(Text, default="")

    schedule: Mapped[Schedule] = relationship(back_populates="overrides")


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_path: Mapped[str] = mapped_column(String(500), index=True)
    schema_hash: Mapped[str] = mapped_column(String(64), index=True)
    definitions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (UniqueConstraint("schedule_id", "scheduled_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_id: Mapped[int] = mapped_column(ForeignKey("target.id", ondelete="CASCADE"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(10), default=R_SCHEDULED, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    build_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    build_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retry_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # その回だけの変更（置き換え）: 置き換え先の run が、元の run の id と、その回だけのパラメータを持つ
    replaces_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    override_params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 実行した時点のスケジューラの件名・メモ（スケジューラを変更・削除しても、履歴で何を実行したか分かるように）
    title_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    schedule: Mapped[Schedule | None] = relationship(back_populates="runs")
    target: Mapped[Target] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class UserSession(Base):
    """ログイン状態。ブラウザの Cookie にはランダムなトークンだけを渡し、DB にはそのハッシュを置く。"""

    __tablename__ = "user_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # トークンの SHA-256
    username: Mapped[str] = mapped_column(String(200), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppState(Base):
    """dispatcher の最終 tick など、プロセス再起動をまたいで残す小さな状態。"""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
