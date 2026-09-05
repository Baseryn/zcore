import datetime
import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from zcore.config import settings
from zcore.exceptions.base import ValidationError
from zcore.utils.helpers import json_dumps, json_loads, slugify
from zcore.utils.timezone import (
    ZDateTime,
    format_iso_with_app_timezone,
    get_app_timezone,
    now,
    to_app_timezone,
    utc_now,
)
from zcore.utils.validators import validate_json_schema


class DummyUser:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return f"DummyUser({self.name})"


@pytest.mark.parametrize(
    "input_val, expected_output",
    [
        (
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            '"12345678-1234-5678-1234-567812345678"'
        ),
        (
            datetime.datetime(2026, 7, 2, 10, 0, 0),
            '"2026-07-02T10:00:00+00:00"'
        ),
        (
            datetime.date(2026, 7, 2),
            '"2026-07-02"'
        ),
        (
            datetime.time(10, 0, 0),
            '"10:00:00"'
        ),
        (
            Decimal("123.45"),
            '"123.45"'
        ),
        (
            {"user_id": uuid.UUID("12345678-1234-5678-1234-567812345678"), "balance": Decimal("9.99")},
            '{"user_id": "12345678-1234-5678-1234-567812345678", "balance": "9.99"}'
        )
    ]
)
def test_custom_json_encoder(input_val: Any, expected_output: str) -> None:
    serialized = json_dumps(input_val)
    assert json_loads(serialized) == json_loads(expected_output)


def test_custom_json_encoder_fallback_on_custom_object() -> None:
    user = DummyUser("Alice")
    serialized = json_dumps(user)
    assert json_loads(serialized) == "DummyUser(Alice)"


def test_custom_json_encoder_nested_mixed_types() -> None:
    data = {
        "list": [
            uuid.UUID("12345678-1234-5678-1234-567812345678"),
            datetime.date(2026, 7, 2),
            Decimal("10.50")
        ],
        "nested": {
            "time": datetime.time(14, 30, 0),
            "dt": datetime.datetime(2026, 7, 2, 12, 0, 0)
        }
    }
    serialized = json_dumps(data)
    deserialized = json_loads(serialized)
    assert deserialized["list"][0] == "12345678-1234-5678-1234-567812345678"
    assert deserialized["list"][1] == "2026-07-02"
    assert deserialized["list"][2] == "10.50"
    assert deserialized["nested"]["time"] == "14:30:00"
    assert deserialized["nested"]["dt"] == "2026-07-02T12:00:00+00:00"


def test_custom_json_encoder_decimal_precision() -> None:
    small_decimal = Decimal("0.00000000000000000001")
    serialized = json_dumps(small_decimal)
    assert json_loads(serialized) == str(small_decimal)


def test_custom_json_encoder_circular_reference() -> None:
    data: dict[str, Any] = {}
    data["self"] = data
    with pytest.raises(ValueError):
        json_dumps(data)


def test_custom_json_encoder_kwargs() -> None:
    data = {"a": 1, "b": 2}
    serialized = json_dumps(data, indent=4)
    assert "\n" in serialized
    deserialized = json_loads(serialized, parse_float=Decimal)
    assert deserialized == data


@pytest.mark.parametrize(
    "text, expected_slug",
    [
        ("Hello World!", "hello-world"),
        ("  SpAcE  and  _underscores_  ", "space-and-underscores"),
        ("Some - text - here!", "some-text-here"),
        ("---leading-and-trailing---", "leading-and-trailing"),
        ("special#@$chars%^&", "specialchars"),
    ]
)
def test_slugify_logic(text: str, expected_slug: str) -> None:
    assert slugify(text) == expected_slug


def test_slugify_unicode_persian() -> None:
    assert slugify("بخش مقالات ZCore 2026!") == "بخش-مقالات-zcore-2026"


@pytest.mark.parametrize("text", ["", "   ", " \n\t\r "])
def test_slugify_empty_whitespaces(text: str) -> None:
    assert slugify(text) == ""


def test_slugify_newlines() -> None:
    assert slugify("First\nSecond\rThird") == "first-second-third"


def test_slugify_multiple_separators() -> None:
    assert slugify("hello____world----test") == "hello-world-test"


@pytest.mark.parametrize(
    "data, schema, should_pass, expected_error_msg",
    [
        (
            {"type": "object", "properties": {"id": {"type": "integer"}}},
            None,
            True,
            ""
        ),
        (
            {"type": "invalid_type_name_here"},
            None,
            False,
            "Internal System Error: The defined schema is corrupted or invalid."
        ),
        (
            {"id": 42, "name": "ZCore"},
            {"type": "object", "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}, "required": ["id"]},
            True,
            ""
        ),
        (
            {"id": "not_an_integer", "name": "ZCore"},
            {"type": "object", "properties": {"id": {"type": "integer"}}},
            False,
            "JSON Schema validation failed"
        ),
        (
            None,
            {"type": "object"},
            True,
            ""
        ),
    ]
)
def test_validate_json_schema(
    data: Any,
    schema: dict[str, Any] | None,
    should_pass: bool,
    expected_error_msg: str
) -> None:
    if should_pass:
        validate_json_schema(data, schema)
    else:
        with pytest.raises(ValidationError) as exc_info:
            validate_json_schema(data, schema)
        assert expected_error_msg in str(exc_info.value)


def test_validate_json_schema_detailed_payload() -> None:
    schema = {
        "type": "object",
        "properties": {
            "user": {
                "type": "object",
                "properties": {
                    "age": {"type": "integer"}
                }
            }
        }
    }
    data = {"user": {"age": "not_an_integer"}}
    with pytest.raises(ValidationError) as exc_info:
        validate_json_schema(data, schema)
    payload = exc_info.value.payload
    assert payload is not None
    assert payload["path"] == ["user", "age"]
    assert "type" in payload["schema"]


def test_validate_json_schema_corrupt_schema_payload() -> None:
    corrupt_schema = {"type": "invalid_type_definition"}
    with pytest.raises(ValidationError) as exc_info:
        validate_json_schema({}, corrupt_schema)
    payload = exc_info.value.payload
    assert payload is not None
    assert "error" in payload


def test_validate_json_schema_array_constraints() -> None:
    schema = {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "minItems": 2,
                "uniqueItems": True,
                "items": {"type": "string"}
            }
        }
    }
    valid_data = {"tags": ["admin", "user"]}
    validate_json_schema(valid_data, schema)
    invalid_data_unique = {"tags": ["admin", "admin"]}
    with pytest.raises(ValidationError):
        validate_json_schema(invalid_data_unique, schema)
    invalid_data_len = {"tags": ["admin"]}
    with pytest.raises(ValidationError):
        validate_json_schema(invalid_data_len, schema)


def test_validate_json_schema_additional_properties() -> None:
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"}
        },
        "additionalProperties": False
    }
    valid_data = {"id": 1}
    validate_json_schema(valid_data, schema)
    invalid_data = {"id": 1, "extra": "forbidden"}
    with pytest.raises(ValidationError):
        validate_json_schema(invalid_data, schema)


def test_get_app_timezone_valid_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    get_app_timezone.cache_clear()
    monkeypatch.setattr(settings, "TIMEZONE", "UTC")
    assert get_app_timezone() == datetime.UTC

    tehran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30), name="Asia/Tehran")
    monkeypatch.setattr("zoneinfo.ZoneInfo", lambda name: tehran_tz)
    get_app_timezone.cache_clear()
    monkeypatch.setattr(settings, "TIMEZONE", "Asia/Tehran")
    tz = get_app_timezone()
    assert str(tz) == "Asia/Tehran"

    info_before = get_app_timezone.cache_info()
    cached_tz = get_app_timezone()
    info_after = get_app_timezone.cache_info()
    assert cached_tz is tz
    assert info_after.hits == info_before.hits + 1
    get_app_timezone.cache_clear()


def test_get_app_timezone_invalid_fallback_and_warning_log(monkeypatch: pytest.MonkeyPatch) -> None:
    get_app_timezone.cache_clear()
    monkeypatch.setattr(settings, "TIMEZONE", "Invalid/NonExistent_Timezone")
    with patch("zcore.utils.timezone.logger.warning") as mock_warn:
        tz = get_app_timezone()
        assert tz == datetime.UTC
        mock_warn.assert_called_once()
        assert mock_warn.call_args[1]["timezone"] == "Invalid/NonExistent_Timezone"
    get_app_timezone.cache_clear()


def test_now_and_utc_now_timezone_awareness(monkeypatch: pytest.MonkeyPatch) -> None:
    tehran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30), name="Asia/Tehran")
    monkeypatch.setattr("zoneinfo.ZoneInfo", lambda name: tehran_tz)
    get_app_timezone.cache_clear()
    monkeypatch.setattr(settings, "TIMEZONE", "Asia/Tehran")
    current_now = now()
    current_utc = utc_now()

    assert current_now.tzinfo is not None
    assert str(current_now.tzinfo) == "Asia/Tehran"
    assert current_utc.tzinfo == datetime.UTC
    get_app_timezone.cache_clear()


def test_to_app_timezone_naive_and_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    tehran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30), name="Asia/Tehran")
    monkeypatch.setattr("zoneinfo.ZoneInfo", lambda name: tehran_tz)
    get_app_timezone.cache_clear()
    monkeypatch.setattr(settings, "TIMEZONE", "Asia/Tehran")

    assert to_app_timezone(None) is None

    naive_dt = datetime.datetime(2026, 7, 2, 10, 0, 0)
    converted_naive = to_app_timezone(naive_dt)
    assert converted_naive is not None
    assert str(converted_naive.tzinfo) == "Asia/Tehran"
    assert converted_naive.hour == 13
    assert converted_naive.minute == 30

    aware_dt = datetime.datetime(2026, 7, 2, 10, 0, 0, tzinfo=datetime.UTC)
    converted_aware = to_app_timezone(aware_dt)
    assert converted_aware is not None
    assert str(converted_aware.tzinfo) == "Asia/Tehran"
    assert converted_aware == aware_dt
    get_app_timezone.cache_clear()


def test_format_iso_with_app_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    get_app_timezone.cache_clear()
    assert format_iso_with_app_timezone(None) is None

    monkeypatch.setattr(settings, "TIMEZONE", "UTC")
    utc_dt = datetime.datetime(2026, 7, 2, 10, 0, 0)
    assert format_iso_with_app_timezone(utc_dt) == "2026-07-02T10:00:00+00:00"

    tehran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30), name="Asia/Tehran")
    monkeypatch.setattr("zoneinfo.ZoneInfo", lambda name: tehran_tz)
    get_app_timezone.cache_clear()
    monkeypatch.setattr(settings, "TIMEZONE", "Asia/Tehran")
    assert format_iso_with_app_timezone(utc_dt) == "2026-07-02T13:30:00+03:30"
    get_app_timezone.cache_clear()


def test_zdatetime_pydantic_field_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    tehran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30), name="Asia/Tehran")
    monkeypatch.setattr("zoneinfo.ZoneInfo", lambda name: tehran_tz)
    get_app_timezone.cache_clear()
    monkeypatch.setattr(settings, "TIMEZONE", "Asia/Tehran")

    class EventSchema(BaseModel):
        created_at: ZDateTime

    event = EventSchema(created_at=datetime.datetime(2026, 7, 2, 10, 0, 0))
    serialized_dict = event.model_dump(mode="json")
    assert serialized_dict["created_at"] == "2026-07-02T13:30:00+03:30"
    get_app_timezone.cache_clear()


def test_custom_json_encoder_auto_convert_timezone_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    tehran_tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30), name="Asia/Tehran")
    monkeypatch.setattr("zoneinfo.ZoneInfo", lambda name: tehran_tz)
    get_app_timezone.cache_clear()
    dt = datetime.datetime(2026, 7, 2, 10, 0, 0)

    monkeypatch.setattr(settings, "TIMEZONE", "Asia/Tehran")
    monkeypatch.setattr(settings, "AUTO_CONVERT_TIMEZONE", True)
    assert json_loads(json_dumps(dt)) == "2026-07-02T13:30:00+03:30"

    monkeypatch.setattr(settings, "AUTO_CONVERT_TIMEZONE", False)
    assert json_loads(json_dumps(dt)) == "2026-07-02T10:00:00"
    get_app_timezone.cache_clear()