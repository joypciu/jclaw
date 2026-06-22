"""Timezone-aware timestamp formatting."""
from datetime import datetime, timezone as tz
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def format_local_time(utc_iso: str, timezone: str) -> str:
    """Convert a UTC ISO timestamp to a localized display string."""
    try:
        zoneinfo = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, KeyError):
        zoneinfo = tz.utc

    try:
        dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
        local = dt.astimezone(zoneinfo)
        return local.strftime("%b %-d, %Y, %-I:%M %p")
    except (ValueError, OSError):
        return utc_iso
