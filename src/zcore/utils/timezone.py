"""ZCore Dynamic Timezone and DateTime Management Module.

This module provides timezone-aware datetime utilities leveraging Python's
standard `zoneinfo.ZoneInfo` (IANA database) with built-in fallback to `datetime.timezone.utc`.
"""

import functools
import zoneinfo
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import PlainSerializer

from zcore.config import settings


@functools.lru_cache(maxsize=16)
def get_app_timezone() -> Any:
    """Retrieve and cache the active application ZoneInfo object based on settings.

    Returns:
        A timezone instance representing the application's configured timezone.
    """
    tz_name = getattr(settings, "TIMEZONE", "UTC")
    if not tz_name or str(tz_name).upper() == "UTC":
        return timezone.utc
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def now() -> datetime:
    """Generate current timezone-aware datetime in the application's configured timezone.

    Returns:
        Current datetime instance bound to the application timezone.
    """
    return datetime.now(get_app_timezone())


def utc_now() -> datetime:
    """Generate current timezone-aware datetime in UTC.

    Returns:
        Current datetime instance bound to UTC.
    """
    return datetime.now(timezone.utc)


def to_app_timezone(dt: datetime | None) -> datetime | None:
    """Convert any datetime object to the application's configured timezone.

    If the input datetime is naive (lacks tzinfo), it is assumed to be stored in UTC
    before undergoing offset transformation.

    Args:
        dt: The source datetime object to convert, or None.

    Returns:
        The converted datetime in the application's timezone, or None if input was None.
    """
    if dt is None:
        return None

    target_tz = get_app_timezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(target_tz)


def format_iso_with_app_timezone(dt: datetime | None) -> str | None:
    """Convert a datetime to application timezone and format as an ISO 8601 string.

    Args:
        dt: The source datetime object to convert and format.

    Returns:
        ISO 8601 formatted string with offset, or None if input is None.
    """
    if dt is None:
        return None
    converted = to_app_timezone(dt)
    return converted.isoformat() if converted is not None else None


ZDateTime = Annotated[
    datetime,
    PlainSerializer(
        format_iso_with_app_timezone, return_type=str, when_used="json"
    ),
]

__all__ = [
    "ZDateTime",
    "format_iso_with_app_timezone",
    "get_app_timezone",
    "now",
    "to_app_timezone",
    "utc_now",
]