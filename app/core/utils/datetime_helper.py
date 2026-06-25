from datetime import datetime, timezone

def utc_now() -> datetime:
    """Returns a timezone-naive UTC datetime object representing the current UTC time."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
