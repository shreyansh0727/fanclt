from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def next_occurrence(local_time: str, days: list[int], tz_name: str, after: datetime) -> datetime:
    """First moment strictly after `after` matching HH:MM on one of `days`
    (0=Monday), evaluated in `tz_name`. Returned as UTC."""
    tz = ZoneInfo(tz_name)
    hour, minute = (int(part) for part in local_time.split(":"))
    local_now = after.astimezone(tz)

    for offset in range(8):
        day = (local_now + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=tz)
        if candidate > local_now:
            return candidate.astimezone(timezone.utc)

    # Unreachable for a non-empty `days`, but keeps the type checker honest.
    return (local_now + timedelta(days=7)).astimezone(timezone.utc)
