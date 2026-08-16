"""JSON Schema Validation Utility.

This module provides tools to validate JSON documents. It integrates standard
`jsonschema` Draft-7 schemas to evaluate payload boundaries or check the structural
validity of dynamically defined schemas.
"""

from typing import Any

from jsonschema import Draft7Validator, exceptions, validate

from zcore.exceptions.base import ValidationError


def validate_json_schema(data: Any, schema: dict[str, Any] | None = None) -> None:
    """Validate JSON data against a Draft-7 schema.

    If no schema dictionary is supplied, this function evaluates the `data` itself
    as a schema definition, verifying that it is structurally valid according to the
    Draft-7 meta-schema.

    Args:
        data: The target data instance to evaluate, or a schema dictionary to check
            if the `schema` parameter is None.
        schema: The target JSON Schema dictionary to validate the data against.
            Defaults to None.

    Raises:
        ValidationError: If the data violates schema constraints or the target
            schema dictionary is structurally invalid.
    """
    if data is None:
        return
    try:
        if schema is None:
            Draft7Validator.check_schema(data)
        else:
            validate(instance=data, schema=schema)
    except exceptions.ValidationError as e:
        raise ValidationError(
            message=f"JSON Schema validation failed: {e.message}",
            payload={"path": list(e.path), "schema": e.schema},
        )
    except exceptions.SchemaError as e:
        raise ValidationError(
            message="Internal System Error: The defined schema is corrupted or invalid.",
            payload={"error": e.message},
        )
