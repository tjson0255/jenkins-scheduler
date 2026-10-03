"""時刻ユーティリティ。DB には naive な UTC を保存し、表示・cron 評価は Asia/Tokyo。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import get_settings

UTC = timezone.utc


# 夏時間が無い（1951年以降）ので、固定のオフセットで正確に表せるタイムゾーン
FIXED_OFFSET_ZONES = {"Asia/Tokyo": 9, "Japan": 9}


@lru_cache(maxsize=8)
def _zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # Windows にはタイムゾーンのデータが無い（tzdata パッケージを入れていない）ので、日本時間は固定の +09:00 で扱う
        if name in FIXED_OFFSET_ZONES:
            return timezone(timedelta(hours=FIXED_OFFSET_ZONES[name]), "JST")
        raise ValueError(
            f"タイムゾーン {name} のデータがありません。APP_TZ を Asia/Tokyo にするか、pip install tzdata を実行してください"
        ) from None


def local_tz() -> tzinfo:
    return _zone(get_settings().app_tz)


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
