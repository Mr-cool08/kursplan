"""Format stored timestamps for the Swedish website."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


SWEDISH_TIMEZONE = ZoneInfo("Europe/Stockholm")


def format_swedish_time(value):
    """Display UTC/offset timestamps in Swedish time, including DST changes.

    Older timestamps without an offset are interpreted as UTC, matching the
    booking history's storage convention rather than the server's local zone.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return "Tid saknas"
    if not isinstance(value, datetime):
        return "Tid saknas"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SWEDISH_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")
