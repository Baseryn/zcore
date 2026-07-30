"""Structured Exception Handlers.

This module maps custom application exceptions (`AppException`) to standardized API JSON responses.
It captures diagnostics, warning contexts, and metadata, formatting them using the system 
response wrapper envelope prior to transmission.
"""

import structlog
from typing import Any
from fastapi import Request
from fastapi.responses import JSONResponse

from zcore.exceptions.base import AppException
from zcore.web.response import ResponseWrapper
from zcore.utils.helpers import json_dumps

log = structlog.get_logger()


class ZCoreJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json_dumps(content).encode("utf-8")

async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Asynchronously intercept application exceptions and build unified JSON responses.

    Captures context parameter diagnostics, outputs metrics to structured log targets, 
    and packs the diagnostic message and metadata payload inside a structured 
    `ResponseWrapper` response envelope.

    Args:
        request: The active incoming HTTP Request.
        exc: The captured application exception class instance to process.

    Returns:
        A formatted JSONResponse containing the structured error metadata envelope.
    """
    log.warning(
        "AppException raised",
        type=type(exc).__name__,
        status_code=exc.status_code,
        message=exc.message,
        payload=exc.payload,
        path=request.url.path,
        method=request.method
    )
    
    response_payload = ResponseWrapper[None](
        success=False,
        message=exc.message,
        data=None,
        meta={
            "error_type": exc.__class__.__name__,
            "payload": exc.payload
        }
    )
    
    return ZCoreJSONResponse(
        status_code=exc.status_code,
        content=response_payload.model_dump()
    )