"""Time formatting helpers."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT_TIMEZONE = "UTC"


def backup_timestamp(value: datetime) -> str:
    """Format a timestamp for backup file names."""
    return value.strftime("%Y%m%d_%H%M%S")


def normalize_timezone_name(value: str) -> str:
    """Return a valid IANA time zone name accepted by Python."""
    timezone_name = value.strip()
    if not timezone_name:
        raise ZoneInfoNotFoundError("Zona horaria vacia")
    try:
        ZoneInfo(timezone_name)
        return timezone_name
    except ZoneInfoNotFoundError:
        casefolded_name = timezone_name.casefold()
        for candidate in sorted(available_timezones()):
            if candidate.casefold() == casefolded_name:
                ZoneInfo(candidate)
                return candidate
        raise


def local_datetime(value: datetime, timezone_name: str) -> datetime:
    """Convert a datetime to the configured local time zone."""
    source = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo(DEFAULT_TIMEZONE)
    return source.astimezone(timezone)


def format_local_datetime(value: datetime | None, timezone_name: str) -> str:
    """Format a datetime in the configured local time zone."""
    if value is None:
        return "-"
    return local_datetime(value, timezone_name).strftime("%Y-%m-%d %H:%M:%S")
