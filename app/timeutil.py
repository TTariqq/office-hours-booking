"""Time zone helpers. Instructors type Riyadh times; the database stores UTC."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")


def riyadh_to_utc(date_str, time_str):
    """Turn "YYYY-MM-DD" plus "HH:MM" in Riyadh into an aware UTC datetime.

    Raises ValueError when either string cannot be parsed.
    """
    local = datetime.fromisoformat(f"{date_str}T{time_str}").replace(tzinfo=RIYADH)
    return local.astimezone(timezone.utc)


def as_utc(dt):
    """SQLite drops the time zone, so treat a naive value as UTC and return aware UTC."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_riyadh(dt):
    """The same moment, shown in Riyadh time."""
    return as_utc(dt).astimezone(RIYADH)
