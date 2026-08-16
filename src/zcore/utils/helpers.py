"""Common Utility Helper Functions.

This module provides common utility helper functions, including a custom JSON encoder
that safely processes dates, decimals, and UUIDs, corresponding serialization/deserialization
wrappers, and a text transformation utility to generate URL-safe slugs.
"""

import json
import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any

from pydantic import HttpUrl, PlainSerializer

# Serializes HttpUrl objects into clean strings dynamically for Pydantic exports
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
    """JSON encoder capable of serializing advanced Python data types.

    Extends the standard library JSONEncoder to serialize instances of UUID,
    datetime/date/time, and Decimal, falling back to a string representation
    for other unrecognized objects to prevent serialization failures.
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
        if isinstance(obj, (datetime, date, time)):
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
