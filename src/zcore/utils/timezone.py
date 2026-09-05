"""ZCore Dynamic Timezone and DateTime Management Module.

This module provides timezone-aware datetime utilities leveraging Python's
standard `zoneinfo.ZoneInfo` (IANA database) with built-in fallback to `datetime.timezone.utc`.
"""

import functools
import zoneinfo
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from pydantic import PlainSerializer

from zcore.config import settings

logger = structlog.get_logger()


@functools.lru_cache(maxsize=16)
def get_app_timezone() -> Any:
    """Retrieve and cache the active application ZoneInfo object based on settings.

    Returns:
        A timezone instance representing the application's configured timezone,
        falling back to UTC if the configured identifier is invalid.
    """
    tz_name = getattr(settings, "TIMEZONE", "UTC")
    if not tz_name or str(tz_name).upper() == "UTC":
        return UTC
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception as e:
        logger.warning(
            "Invalid timezone configured, falling back to UTC",
            timezone=tz_name,
            error=str(e),
        )
        return UTC


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
    return datetime.now(UTC)


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
        dt = dt.replace(tzinfo=UTC)

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