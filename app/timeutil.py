"""時刻ユーティリティ。DB には naive な UTC を保存し、表示・cron 評価は Asia/Tokyo。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import get_settings

UTC = timezone.utc


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().app_tz)


def utcnow() -> datetime:
    """naive UTC の現在時刻。"""
    return datetime.now(UTC).replace(tzinfo=None)


def to_utc_naive(dt: datetime) -> datetime:
    """aware なら UTC に変換、naive ならローカル時刻（Asia/Tokyo）として解釈する。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt.astimezone(UTC).replace(tzinfo=None)


def to_local(dt_utc_naive: datetime) -> datetime:
    return dt_utc_naive.replace(tzinfo=UTC).astimezone(local_tz())


def iso_z(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def local_midnight_utc(d: date) -> datetime:
    return to_utc_naive(datetime.combine(d, time.min, tzinfo=local_tz()))


def local_today() -> date:
    return datetime.now(local_tz()).date()


def parse_datetime_input(value: str) -> datetime:
    """UI/API からの日時文字列を naive UTC に変換する（タイムゾーン無しはローカル扱い）。"""
    s = value.strip().replace("Z", "+00:00")
    return to_utc_naive(datetime.fromisoformat(s))


__all__ = [
    "UTC",
    "iso_z",
    "local_midnight_utc",
    "local_today",
    "local_tz",
    "parse_datetime_input",
    "timedelta",
    "to_local",
    "to_utc_naive",
    "utcnow",
]
