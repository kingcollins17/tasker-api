from datetime import datetime, timezone
from zoneinfo import ZoneInfo

def now() -> datetime:
    """Returns a timezone-naive datetime object representing the current local time in Lagos, Africa."""
    return lagos_now()

def lagos_tz():
    return ZoneInfo("Africa/Lagos")

def lagos_now() -> datetime:
    """Returns a timezone-naive datetime object representing the current local time in Lagos, Africa."""
    return datetime.now(lagos_tz()).replace(tzinfo=None)

def to_lagos_naive(dt: datetime) -> datetime:
    """Safely converts any datetime (aware or naive) into a naive Lagos datetime for comparison."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(lagos_tz()).replace(tzinfo=None)