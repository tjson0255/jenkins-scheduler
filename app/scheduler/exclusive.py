"""「この期間は、同じレーンの他のスケジューラを止める」（臨時のスケジューラ用）。

同じレーンに、有効で exclusive なスケジューラがあり、その期間（開始日〜終了日）に入る回は、
他のスケジューラの回をキックしない。run の状態は書き換えず、その都度判定する
（臨時のスケジューラを一時停止・削除・変更すれば、普段の回は自動で元に戻る）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.models import ACTIVE, Schedule
from app.timeutil import to_local


def overriding_schedule(schedules: Iterable[Schedule], schedule: Schedule | None, at_utc: datetime) -> Schedule | None:
    """schedule の、at_utc の回を止める臨時のスケジューラ（無ければ None）。

    exclusive なスケジューラ自身の回・スケジューラの無い回（即時実行・再実行）は止めない。
    """
    if schedule is None or schedule.exclusive or schedule.is_memo:
        return None
    day = to_local(at_utc).date()
    for s in schedules:
        if (s.id != schedule.id and s.exclusive and s.status == ACTIVE and not s.is_memo
                and s.start_date <= day and (s.end_date is None or day <= s.end_date)):
            return s
    return None


def suppressed_reason(by: Schedule) -> str:
    from app.scheduler.history import schedule_title

    return f"「{schedule_title(by)}」を優先（この期間は同じレーンの他のスケジューラを止める設定）"
