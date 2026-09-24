"""Structured Exception Handlers.

This module maps custom application exceptions (`AppException`) to standardized API JSON responses.
It captures diagnostics, warning contexts, and metadata, formatting them using the system
response wrapper envelope prior to transmission.
"""

from collections.abc import Sequence
from typing import Any

import structlog
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from zcore.exceptions.base import AppException
from zcore.utils.helpers import json_dumps
from zcore.web.response import ResponseWrapper

log = structlog.get_logger()


class ZCoreJSONResponse(JSONResponse):
    """Unified JSONResponse utilizing the framework's JSON serializer."""

    def render(self, content: Any) -> bytes:
        """Render response content into JSON bytes using the custom encoder.

        Args:
            content: The data structure to serialize.

        Returns:
            UTF-8 encoded JSON byte stream.
        """
        return json_dumps(content).encode("utf-8")


def _format_error_location(loc: Sequence[str | int]) -> str:
    """Format Pydantic error location tuple into a readable dot-separated path.

    Args:
        loc: Sequence of string keys or integer indices.

    Returns:
        Dot-separated string representing the field location path.
    """
    return ".".join(str(part) for part in loc if part != "")


def _sanitize_error_item(err: dict[str, Any]) -> dict[str, Any]:
    """Sanitize individual Pydantic error dictionaries for serialization safety.

    Args:
        err: Raw Pydantic error dictionary.

    Returns:
        A sanitized dictionary containing field path, error message, and error type.
    """
    field_path = _format_error_location(err.get("loc", ()))
    message = err.get("msg", "Invalid value")
    error_type = err.get("type", "value_error")

    sanitized: dict[str, Any] = {
        "field": field_path or "root",
        "message": message,
        "type": error_type,
    }

    ctx_info = err.get("ctx")
    if isinstance(ctx_info, dict):
        safe_ctx = {
            k: str(v)
            for k, v in ctx_info.items()
            if isinstance(v, (str, int, float, bool))
        }
        if safe_ctx:
            sanitized["context"] = safe_ctx

    return sanitized


async def app_exception_handler(request: Request, exc: AppException) -> ZCoreJSONResponse:
    """Handle custom domain-level `AppException` instances.

    Args:
        request: The incoming HTTP request.
        exc: The captured domain exception.

    Returns:
        Structured ZCoreJSONResponse enclosing the error details.
    """
    log.warning(
        "AppException raised",
        error_type=type(exc).__name__,
        status_code=exc.status_code,
        message=exc.message,
        payload=exc.payload,
        path=request.url.path,
        method=request.method,
    )

    response_payload = ResponseWrapper[None](
        success=False,
        message=exc.message,
        data=None,
        meta={"error_type": exc.__class__.__name__, "payload": exc.payload},
    )

    return ZCoreJSONResponse(
        status_code=exc.status_code,
        content=response_payload.model_dump(),
    )


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> ZCoreJSONResponse:
    """Handle FastAPI and Pydantic request payload/query validation errors.

    Args:
        request: The incoming HTTP request.
        exc: The captured RequestValidationError containing Pydantic error breakdowns.

    Returns:
        Structured 422 ZCoreJSONResponse containing sanitized field-level error messages.
    """
    raw_errors = exc.errors()
    sanitized_errors = [_sanitize_error_item(err) for err in raw_errors]

    if sanitized_errors:
        first_err = sanitized_errors[0]
        field_desc = f" on '{first_err['field']}'" if first_err.get("field") else ""
        summary_message = f"Validation error{field_desc}: {first_err['message']}"
    else:
        summary_message = "Request validation failed"

    log.warning(
        "Request validation failed",
        status_code=422,
        error_count=len(sanitized_errors),
        path=request.url.path,
        method=request.method,
    )

    response_payload = ResponseWrapper[None](
        success=False,
        message=summary_message,
        data=None,
        meta={
            "error_type": "RequestValidationError",
            "errors": sanitized_errors,
        },
    )

    return ZCoreJSONResponse(
        status_code=422,
        content=response_payload.model_dump(),
    )