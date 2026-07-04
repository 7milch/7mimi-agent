from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    return datetime.now(tz=JST)


def iso_now() -> str:
    return now_jst().isoformat(timespec="seconds")


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
