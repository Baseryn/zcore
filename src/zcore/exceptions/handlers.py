"""Structured Exception Handlers and Unified Registration.

This module provides explicit exception handlers that normalize internal exceptions,
Pydantic validation errors, Starlette/FastAPI HTTP exceptions, and unhandled errors
into the unified ZCore `ResponseWrapper` envelope with quiet, non-intrusive logging for client errors.
"""

from collections.abc import Sequence
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from zcore.config import settings
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
            k: str(v) for k, v in ctx_info.items() if isinstance(v, (str, int, float, bool))
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
    log_func = log.debug if exc.status_code < 500 else log.error
    log_func(
        "AppException handled",
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

    log.debug(
        "Request validation error",
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


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> ZCoreJSONResponse:
    """Handle standard Starlette and FastAPI `HTTPException` instances.

    Args:
        request: The incoming HTTP request.
        exc: The captured HTTP exception.

    Returns:
        Structured ZCoreJSONResponse mapping the HTTP status code and detail.
    """
    message = str(exc.detail) if exc.detail else "An HTTP error occurred"

    log_func = log.debug if exc.status_code < 500 else log.error
    log_func(
        "HTTPException handled",
        status_code=exc.status_code,
        message=message,
        path=request.url.path,
        method=request.method,
    )

    response_payload = ResponseWrapper[None](
        success=False,
        message=message,
        data=None,
        meta={
            "error_type": "HTTPException",
            "status_code": exc.status_code,
        },
    )

    return ZCoreJSONResponse(
        status_code=exc.status_code,
        content=response_payload.model_dump(),
        headers=exc.headers,
    )


async def response_validation_exception_handler(
    request: Request, exc: ResponseValidationError
) -> ZCoreJSONResponse:
    """Handle internal endpoint response schema validation mismatches.

    Args:
        request: The incoming HTTP request.
        exc: The captured ResponseValidationError.

    Returns:
        Structured 500 ZCoreJSONResponse detailing schema divergence.
    """
    is_debug = getattr(settings, "DEBUG", False)
    raw_errors = exc.errors() if is_debug else []
    sanitized_errors = [_sanitize_error_item(err) for err in raw_errors]

    log.error(
        "Response validation failed against declared schema",
        status_code=500,
        path=request.url.path,
        method=request.method,
        errors=sanitized_errors if is_debug else None,
    )

    message = (
        "Internal server error: Response validation failed" if is_debug else "Internal server error"
    )

    meta: dict[str, Any] = {
        "error_type": "ResponseValidationError",
        "status_code": 500,
    }
    if is_debug and sanitized_errors:
        meta["errors"] = sanitized_errors

    response_payload = ResponseWrapper[None](
        success=False,
        message=message,
        data=None,
        meta=meta,
    )

    return ZCoreJSONResponse(
        status_code=500,
        content=response_payload.model_dump(),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> ZCoreJSONResponse:
    """Catch-all fallback handler for uncaught runtime exceptions.

    Args:
        request: The incoming HTTP request.
        exc: The unhandled runtime exception instance.

    Returns:
        Structured 500 ZCoreJSONResponse with safe diagnostic messaging.
    """
    is_debug = getattr(settings, "DEBUG", False)

    log.exception(
        "Unhandled server exception intercepted",
        error=str(exc),
        error_type=type(exc).__name__,
        path=request.url.path,
        method=request.method,
    )

    message = f"Internal server error: {exc!s}" if is_debug else "Internal server error"

    meta: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "status_code": 500,
    }

    response_payload = ResponseWrapper[None](
        success=False,
        message=message,
        data=None,
        meta=meta,
    )

    return ZCoreJSONResponse(
        status_code=500,
        content=response_payload.model_dump(),
    )


def register_exception_handlers(
    app: FastAPI,
    include_app_exceptions: bool = True,
    include_validation_exceptions: bool = True,
    include_http_exceptions: bool = True,
    include_response_validation_exceptions: bool = True,
    include_unhandled_exceptions: bool = True,
) -> None:
    """Explicitly attach standardized ZCore exception handlers to a FastAPI application.

    Args:
        app: The target FastAPI application instance.
        include_app_exceptions: Register handler for custom domain `AppException`. Defaults to True.
        include_validation_exceptions: Register handler for `RequestValidationError`. Defaults to True.
        include_http_exceptions: Register handler for Starlette/FastAPI `HTTPException`. Defaults to True.
        include_response_validation_exceptions: Register handler for `ResponseValidationError`. Defaults to True.
        include_unhandled_exceptions: Register catch-all handler for unhandled `Exception`. Defaults to True.
    """
    if include_app_exceptions:
        app.add_exception_handler(AppException, app_exception_handler)

    if include_validation_exceptions:
        app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

    if include_http_exceptions:
        app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    if include_response_validation_exceptions:
        app.add_exception_handler(ResponseValidationError, response_validation_exception_handler)

    if include_unhandled_exceptions:
        app.add_exception_handler(Exception, unhandled_exception_handler)
