"""cron 式（標準5フィールド）の検証・プレビュー・要約。Asia/Tokyo で評価する。"""

from __future__ import annotations

import re
from datetime import datetime

from croniter import croniter

from app.timeutil import local_tz, to_utc_naive, utcnow, to_local

WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土", "日"]


class CronError(ValueError):
    pass


def validate_cron(expr: str | None) -> str:
    expr = (expr or "").strip()
    fields = expr.split()
    if len(fields) != 5:
        raise CronError("cron は標準の5フィールド（分 時 日 月 曜日）で指定してください")
    # Jenkins 独自の H 記法は非対応（曜日・月の英字名は H で始まらない）
    if any(tok.upper().startswith("H") for fld in fields for tok in fld.split(",")):
        raise CronError("Jenkins 独自の H 記法には対応していません。具体的な分・時に置き換えてください")
    if not croniter.is_valid(expr):
        raise CronError(f"cron 式が不正です: {expr}")
    return expr


def iter_occurrences(expr: str, start_utc: datetime, end_utc: datetime):
    """[start_utc, end_utc) に含まれる発火時刻（naive UTC）を返す。"""
    base = to_local(start_utc).replace(second=0, microsecond=0)
    # base 自体も対象に含めるため 1 秒前から数える
    from datetime import timedelta

    it = croniter(expr, base - timedelta(seconds=1))
    while True:
        nxt: datetime = it.get_next(datetime)
        nxt_utc = to_utc_naive(nxt)
        if nxt_utc >= end_utc:
            return
        if nxt_utc >= start_utc:
            yield nxt_utc


def preview(expr: str, count: int = 5, start_utc: datetime | None = None) -> list[datetime]:
    expr = validate_cron(expr)
    start = start_utc or utcnow()
    it = croniter(expr, to_local(start))
    return [to_utc_naive(it.get_next(datetime)) for _ in range(count)]


def summarize(expr: str | None) -> str:
    """プリセットに当てはまる cron を日本語で要約する。"""
    if not expr:
        return ""
    f = expr.split()
    if len(f) != 5:
        return expr
    mi, hr, dom, mon, dow = f
    if mi.isdigit() and hr.isdigit() and dom == "*" and mon == "*":
        hm = f"{int(hr):02d}:{int(mi):02d}"
        if dow == "*":
            return f"毎日 {hm}"
        if dow in ("1-5", "MON-FRI", "mon-fri"):
            return f"平日 {hm}"
        if dow.isdigit() and 0 <= int(dow) <= 7:
            return f"毎週{WEEKDAYS[int(dow)]} {hm}"
        if re.fullmatch(r"\d(,\d)+", dow):
            return "毎週" + "・".join(WEEKDAYS[int(x)] for x in dow.split(",")) + f" {hm}"
    m = re.fullmatch(r"\*/(\d+)", hr)
    if mi.isdigit() and m and dom == "*" and mon == "*" and dow == "*":
        return f"{int(m.group(1))}時間ごと（{int(mi):02d}分）"
    return expr


__all__ = ["CronError", "iter_occurrences", "local_tz", "preview", "summarize", "validate_cron"]
