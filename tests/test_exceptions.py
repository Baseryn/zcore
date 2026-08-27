import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zcore.exceptions.base import (
    AppException,
    AuthError,
    DuplicateEntity,
    EntityNotFound,
    ForbiddenError,
    ValidationError,
)
from zcore.exceptions.handlers import app_exception_handler
from zcore.web.response import ResponseWrapper


class CustomPaymentFailed(AppException):
    status_code = 402

@pytest.mark.parametrize(
    "exc_class, status_code, message, payload",
    [
        (EntityNotFound, 404, "Not Found", {"id": "123"}),
        (DuplicateEntity, 409, "Already Exists", {"key": "unique"}),
        (AuthError, 401, "No Authentication", None),
        (ForbiddenError, 403, "Access Blocked", {"role": "guest"}),
        (ValidationError, 400, "Validation Failed", {"field": "email"}),
        (AppException, 500, "Internal Server Error", None),
    ]
)
def test_exception_status_codes(
    exc_class: type[AppException],
    status_code: int,
    message: str,
    payload: dict[str, Any] | None
) -> None:
    exc = exc_class(message, payload=payload)
    assert exc.status_code == status_code
    assert exc.message == message
    assert exc.payload == payload

@pytest.mark.anyio
@pytest.mark.parametrize(
    "exc_to_raise, expected_status, expected_msg, expected_meta_payload",
    [
        (EntityNotFound("Item missing", {"id": "abc"}), 404, "Item missing", {"id": "abc"}),
        (ValidationError("Bad request", {"reason": "missing field"}), 400, "Bad request", {"reason": "missing field"}),
        (AuthError("Unauthorized session", None), 401, "Unauthorized session", None),
    ]
)
async def test_app_exception_handler_middleware(
    exc_to_raise: AppException,
    expected_status: int,
    expected_msg: str,
    expected_meta_payload: dict[str, Any] | None
) -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/trigger")
    def trigger_error() -> None:
        raise exc_to_raise

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/trigger")
        assert response.status_code == expected_status
        
        body = response.json()
        assert body["success"] is False
        assert body["message"] == expected_msg
        assert body["data"] is None
        assert body["meta"]["error_type"] == exc_to_raise.__class__.__name__
        assert body["meta"]["payload"] == expected_meta_payload

@pytest.mark.anyio
async def test_exception_logging_integration() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/trigger-log")
    def trigger_log() -> None:
        raise ValidationError("Failed validation", payload={"field": "username"})

    transport = ASGITransport(app=app)
    with patch("zcore.exceptions.handlers.log") as mock_log:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/trigger-log")
            mock_log.warning.assert_called_once_with(
                "AppException raised",
                type="ValidationError",
                status_code=400,
                message="Failed validation",
                payload={"field": "username"},
                path="/trigger-log",
                method="GET"
            )

@pytest.mark.anyio
async def test_custom_app_exception_subclass() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/payment")
    def trigger_payment_error() -> None:
        raise CustomPaymentFailed("Payment declined", payload={"reason": "insufficient_funds"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/payment")
        assert response.status_code == 402
        body = response.json()
        assert body["success"] is False
        assert body["message"] == "Payment declined"
        assert body["meta"]["error_type"] == "CustomPaymentFailed"
        assert body["meta"]["payload"] == {"reason": "insufficient_funds"}

@pytest.mark.anyio
async def test_complex_payload_serialization() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    uid = uuid.uuid4()
    now = datetime.fromisoformat("2026-07-30T16:25:00")
    dec = Decimal("123.45")

    @app.get("/complex")
    def trigger_complex() -> None:
        raise ValidationError("Complex error", payload={"uuid": uid, "time": now, "decimal": dec})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/complex")
        assert response.status_code == 400
        body = response.json()
        assert body["meta"]["payload"]["uuid"] == str(uid)
        assert body["meta"]["payload"]["time"] == now.isoformat()
        assert body["meta"]["payload"]["decimal"] == "123.45"

@pytest.mark.anyio
async def test_boundary_null_values() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/null-error")
    def trigger_null() -> None:
        raise AppException("", payload=None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/null-error")
        assert response.status_code == 500
        body = response.json()
        assert body["success"] is False
        assert body["message"] == ""
        assert body["meta"]["payload"] is None

@pytest.mark.anyio
async def test_strict_response_wrapper_compliance() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/compliance")
    def trigger_compliance() -> None:
        raise ValidationError("Validate me", payload={"some": "error"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/compliance")
        body = response.json()
        validated = ResponseWrapper[None].model_validate(body)
        assert validated.success is False
        assert validated.message == "Validate me"
        assert validated.meta is not None
        assert validated.meta["error_type"] == "ValidationError"