"""Common Utility Helper Functions.

This module provides common utility helper functions, including a custom JSON encoder
that safely processes dates, decimals, UUIDs, and timezone-aware datetimes, corresponding
serialization/deserialization wrappers, and a text transformation utility to generate URL-safe slugs.
"""

import json
import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

from pydantic import HttpUrl, PlainSerializer

from zcore.config import settings
from zcore.utils.timezone import format_iso_with_app_timezone

SafeUrl = Annotated[HttpUrl, PlainSerializer(lambda v: str(v), return_type=str)]


def slugify(text: str) -> str:
    """Transform a text string into an URL-safe slug representation.

    Strips surrounding whitespace, normalizes casing to lowercase, removes
    non-alphanumeric characters, and replaces spaces and underscores with single dashes.

    Args:
        text: The raw text string to slugify.

    Returns:
        The URL-safe, sanitized, and hyphenated slug string.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text


class CustomJSONEncoder(json.JSONEncoder):
    """JSON encoder capable of serializing advanced Python data types with timezone awareness.

    Extends the standard library JSONEncoder to serialize instances of UUID,
    datetime/date/time, and Decimal, applying application-level timezone conversions
    on datetime objects according to framework settings.
    """

    def default(self, obj: Any) -> Any:
        """Coerce complex python objects into JSON-serializable types.

        Args:
            obj: The active object to serialize.

        Returns:
            The serialized Python primitive value representation of the object.
        """
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime):
            if getattr(settings, "AUTO_CONVERT_TIMEZONE", True):
                return format_iso_with_app_timezone(obj)
            return obj.isoformat()
        if isinstance(obj, (date, time)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def json_dumps(obj: Any, **kwargs: Any) -> str:
    """Serialize a Python data structure to a JSON string using the CustomJSONEncoder.

    Args:
        obj: The target object to serialize.
        **kwargs: Additional options forwarded to `json.dumps`.

    Returns:
        The serialized JSON string payload.
    """
    return json.dumps(obj, cls=CustomJSONEncoder, **kwargs)


def json_loads(s: str | bytes, **kwargs: Any) -> Any:
    """De-serialize a JSON string or bytes payload back into Python structures.

    Args:
        s: The target JSON string or binary representation to parse.
        **kwargs: Additional options forwarded to `json.loads`.

    Returns:
        The parsed Python primitive values or collection structures.
    """
    return json.loads(s, **kwargs)