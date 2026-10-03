"""cron 式の解釈（app/scheduler/cron.py）。"""

import itertools
from datetime import datetime

import pytest

from app.scheduler.cron import CronExpr, CronSyntaxError, is_valid


def nxt(expr, start, n=4):
    return [d.strftime("%Y-%m-%d %a %H:%M") for d in itertools.islice(CronExpr(expr).iter_local(datetime.fromisoformat(start)), n)]


def test_daily_and_start_is_inclusive():
    assert nxt("0 3 * * *", "2026-01-01 03:00", 2) == ["2026-01-01 Thu 03:00", "2026-01-02 Fri 03:00"]
    assert nxt("0 3 * * *", "2026-01-01 03:01", 1) == ["2026-01-02 Fri 03:00"]


def test_weekdays_names_and_sunday_as_7():
    assert nxt("30 2 * * 1-5", "2026-01-02 00:00") == ["2026-01-02 Fri 02:30", "2026-01-05 Mon 02:30", "2026-01-06 Tue 02:30", "2026-01-07 Wed 02:30"]
    assert nxt("30 2 * * mon-fri", "2026-01-02 00:00") == nxt("30 2 * * 1-5", "2026-01-02 00:00")
    assert nxt("0 4 * * 7", "2026-01-01 00:00", 2) == nxt("0 4 * * 0", "2026-01-01 00:00", 2) == ["2026-01-04 Sun 04:00", "2026-01-11 Sun 04:00"]
    assert nxt("0 4 * * SAT,SUN", "2026-01-01 00:00", 2) == ["2026-01-03 Sat 04:00", "2026-01-04 Sun 04:00"]


def test_steps_ranges_and_lists():
    assert nxt("*/15 9 * * *", "2026-01-01 09:20") == ["2026-01-01 Thu 09:30", "2026-01-01 Thu 09:45", "2026-01-02 Fri 09:00", "2026-01-02 Fri 09:15"]
    assert nxt("0 */6 * * *", "2026-01-01 01:00", 3) == ["2026-01-01 Thu 06:00", "2026-01-01 Thu 12:00", "2026-01-01 Thu 18:00"]
    assert nxt("0 8-18/5 * * *", "2026-01-01 00:00", 3) == ["2026-01-01 Thu 08:00", "2026-01-01 Thu 13:00", "2026-01-01 Thu 18:00"]
    assert nxt("5/20 0 * * *", "2026-01-01 00:00", 3) == ["2026-01-01 Thu 00:05", "2026-01-01 Thu 00:25", "2026-01-01 Thu 00:45"]
    assert nxt("0 0 1,15 * *", "2026-01-02 00:00", 2) == ["2026-01-15 Thu 00:00", "2026-02-01 Sun 00:00"]


def test_months_and_impossible_dates():
    assert nxt("0 0 29 FEB *", "2026-01-01 00:00", 1) == ["2028-02-29 Tue 00:00"]
    assert nxt("0 0 31 * *", "2026-04-01 00:00", 1) == ["2026-05-31 Sun 00:00"]
    assert nxt("0 0 30 2 *", "2026-01-01 00:00", 1) == []  # 2月30日は無いので見つからない（無限ループしない）


def test_day_of_month_or_day_of_week():
    # 両方を指定したら「どちらか」（一般的な cron と同じ）
    assert nxt("0 3 13 * 5", "2026-01-01 00:00") == ["2026-01-02 Fri 03:00", "2026-01-09 Fri 03:00", "2026-01-13 Tue 03:00", "2026-01-16 Fri 03:00"]
    # 片方が * ならもう片方だけ
    assert nxt("0 3 * * 5", "2026-01-01 00:00", 2) == ["2026-01-02 Fri 03:00", "2026-01-09 Fri 03:00"]
    assert nxt("0 3 13 * *", "2026-01-01 00:00", 2) == ["2026-01-13 Tue 03:00", "2026-02-13 Fri 03:00"]
    # */2 は「制限あり」（曜日 0,2,4,6 = 日・火・木・土）として扱う
    assert nxt("0 3 * * */2", "2026-01-01 00:00", 3) == ["2026-01-01 Thu 03:00", "2026-01-03 Sat 03:00", "2026-01-04 Sun 03:00"]


@pytest.mark.parametrize("expr", ["", "0 3 * *", "0 3 * * * *", "60 3 * * *", "0 24 * * *", "0 0 0 * *", "0 0 * 13 *",
                                  "0 0 * * 8", "a 0 * * *", "0 0 * * FOO", "*/0 * * * *", "5-1 * * * *", "1,,2 * * * *"])
def test_invalid(expr):
    assert not is_valid(expr)
    with pytest.raises(CronSyntaxError):
        CronExpr(expr)
