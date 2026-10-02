import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from zcore.config import settings
from zcore.exceptions.base import (
    AppException,
    AuthError,
    DuplicateEntity,
    EntityNotFound,
    ForbiddenError,
    ValidationError,
)
from zcore.exceptions.handlers import (
    app_exception_handler,
    http_exception_handler,
    register_exception_handlers,
    request_validation_exception_handler,
    response_validation_exception_handler,
    unhandled_exception_handler,
)
from zcore.utils.timezone import format_iso_with_app_timezone
from zcore.web.response import ResponseWrapper


class CustomPaymentFailed(AppException):
    status_code = 402


class SampleInputSchema(BaseModel):
    name: str = Field(min_length=3)
    age: int = Field(ge=18)


class SampleOutputSchema(BaseModel):
    id: int
    name: str


@pytest.mark.parametrize(
    "exc_class, status_code, message, payload",
    [
        (EntityNotFound, 404, "Not Found", {"id": "123"}),
        (DuplicateEntity, 409, "Already Exists", {"key": "unique"}),
        (AuthError, 401, "No Authentication", None),
        (ForbiddenError, 403, "Access Blocked", {"role": "guest"}),
        (ValidationError, 400, "Validation Failed", {"field": "email"}),
        (AppException, 500, "Internal Server Error", None),
    ],
)
def test_exception_status_codes(
    exc_class: type[AppException],
    status_code: int,
    message: str,
    payload: dict[str, Any] | None,
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
        (DuplicateEntity("Conflict record", {"field": "email"}), 409, "Conflict record", {"field": "email"}),
        (ForbiddenError("Access denied", None), 403, "Access denied", None),
    ],
)
async def test_app_exception_handler(
    exc_to_raise: AppException,
    expected_status: int,
    expected_msg: str,
    expected_meta_payload: dict[str, Any] | None,
) -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/trigger")
    def trigger_error() -> None:
        raise exc_to_raise

    transport = ASGITransport(app=app, raise_app_exceptions=False)
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
async def test_request_validation_exception_handler_body() -> None:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

    @app.post("/items")
    def create_item(payload: SampleInputSchema) -> dict[str, Any]:
        return {"data": payload.model_dump()}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/items", json={"name": "a", "age": 15})
        assert response.status_code == 422

        body = response.json()
        assert body["success"] is False
        assert "Validation error on 'body.name'" in body["message"]
        assert body["data"] is None
        assert body["meta"]["error_type"] == "RequestValidationError"
        assert len(body["meta"]["errors"]) == 2

        fields = [err["field"] for err in body["meta"]["errors"]]
        assert "body.name" in fields
        assert "body.age" in fields


@pytest.mark.anyio
async def test_request_validation_exception_handler_query() -> None:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

    @app.get("/search")
    def search_items(limit: int = Query(..., ge=1, le=100)) -> dict[str, int]:
        return {"limit": limit}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/search?limit=200")
        assert response.status_code == 422

        body = response.json()
        assert body["success"] is False
        assert "Validation error on 'query.limit'" in body["message"]
        assert body["meta"]["error_type"] == "RequestValidationError"
        assert body["meta"]["errors"][0]["field"] == "query.limit"


@pytest.mark.anyio
async def test_http_exception_handler() -> None:
    app = FastAPI()
    app.add_exception_handler(HTTPException, http_exception_handler)

    @app.get("/http-error")
    def trigger_http() -> None:
        raise HTTPException(
            status_code=403,
            detail="Operation forbidden for current role",
            headers={"X-Security-Reason": "InsufficientPrivileges"},
        )

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/http-error")
        assert response.status_code == 403
        assert response.headers["x-security-reason"] == "InsufficientPrivileges"

        body = response.json()
        assert body["success"] is False
        assert body["message"] == "Operation forbidden for current role"
        assert body["meta"]["error_type"] == "HTTPException"
        assert body["meta"]["status_code"] == 403


@pytest.mark.anyio
@pytest.mark.parametrize("debug_state", [True, False])
async def test_response_validation_exception_handler(debug_state: bool) -> None:
    app = FastAPI()
    app.add_exception_handler(ResponseValidationError, response_validation_exception_handler)

    @app.get("/invalid-output", response_model=SampleOutputSchema)
    def invalid_output() -> dict[str, Any]:
        return {"id": "not_an_int"}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with patch.object(settings, "DEBUG", debug_state):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/invalid-output")
            assert response.status_code == 500

            body = response.json()
            assert body["success"] is False
            assert body["meta"]["error_type"] == "ResponseValidationError"
            assert body["meta"]["status_code"] == 500

            if debug_state:
                assert "Response validation failed" in body["message"]
                assert "errors" in body["meta"]
            else:
                assert body["message"] == "Internal server error"
                assert "errors" not in body["meta"]


@pytest.mark.anyio
@pytest.mark.parametrize("debug_state", [True, False])
async def test_unhandled_exception_handler(debug_state: bool) -> None:
    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_exception_handler)

    @app.get("/crash")
    def crash_server() -> None:
        raise ZeroDivisionError("division by zero detected")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with patch.object(settings, "DEBUG", debug_state):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/crash")
            assert response.status_code == 500

            body = response.json()
            assert body["success"] is False
            assert body["meta"]["error_type"] == "ZeroDivisionError"
            assert body["meta"]["status_code"] == 500

            if debug_state:
                assert "division by zero detected" in body["message"]
            else:
                assert body["message"] == "Internal server error"


@pytest.mark.anyio
async def test_register_exception_handlers_all() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/app-err")
    def trigger_app_err() -> None:
        raise EntityNotFound("Missing Record")

    @app.post("/val-err")
    def trigger_val_err(data: SampleInputSchema) -> dict[str, Any]:
        return data.model_dump()

    @app.get("/http-err")
    def trigger_http_err() -> None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/crash-err")
    def trigger_crash_err() -> None:
        raise RuntimeError("Fatal crash")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/app-err")
        assert r1.status_code == 404
        assert r1.json()["meta"]["error_type"] == "EntityNotFound"

        r2 = await client.post("/val-err", json={})
        assert r2.status_code == 422
        assert r2.json()["meta"]["error_type"] == "RequestValidationError"

        r3 = await client.get("/http-err")
        assert r3.status_code == 401
        assert r3.json()["meta"]["error_type"] == "HTTPException"

        r4 = await client.get("/crash-err")
        assert r4.status_code == 500
        assert r4.json()["meta"]["error_type"] == "RuntimeError"


@pytest.mark.anyio
async def test_register_exception_handlers_selective() -> None:
    app = FastAPI()
    register_exception_handlers(
        app,
        include_app_exceptions=True,
        include_validation_exceptions=False,
        include_http_exceptions=False,
        include_response_validation_exceptions=False,
        include_unhandled_exceptions=False,
    )

    @app.get("/app-err")
    def trigger_app_err() -> None:
        raise ForbiddenError("Blocked")

    @app.post("/val-err")
    def trigger_val_err(data: SampleInputSchema) -> dict[str, Any]:
        return data.model_dump()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/app-err")
        assert r1.status_code == 403
        assert r1.json()["success"] is False

        r2 = await client.post("/val-err", json={})
        assert r2.status_code == 422
        assert "detail" in r2.json()


@pytest.mark.anyio
async def test_exception_logging_integration() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/log-app")
    def log_app() -> None:
        raise ValidationError("Invalid field data", payload={"field": "sku"})

    @app.get("/log-unhandled")
    def log_unhandled() -> None:
        raise ValueError("Invalid arithmetic calculation")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    with patch("zcore.exceptions.handlers.log") as mock_log:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/log-app")
            mock_log.debug.assert_called_once_with(
                "AppException handled",
                error_type="ValidationError",
                status_code=400,
                message="Invalid field data",
                payload={"field": "sku"},
                path="/log-app",
                method="GET",
            )


@pytest.mark.anyio
async def test_custom_app_exception_subclass() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/payment")
    def trigger_payment_error() -> None:
        raise CustomPaymentFailed("Payment declined", payload={"reason": "insufficient_funds"})

    transport = ASGITransport(app=app, raise_app_exceptions=False)
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

    test_uuid = uuid.uuid4()
    test_dt = datetime.fromisoformat("2026-07-30T16:25:00")
    test_dec = Decimal("123.45")

    @app.get("/complex")
    def trigger_complex() -> None:
        raise ValidationError(
            "Complex error payload",
            payload={"uuid": test_uuid, "time": test_dt, "decimal": test_dec},
        )

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/complex")
        assert response.status_code == 400
        body = response.json()
        assert body["meta"]["payload"]["uuid"] == str(test_uuid)
        assert body["meta"]["payload"]["time"] == format_iso_with_app_timezone(test_dt)
        assert body["meta"]["payload"]["decimal"] == "123.45"


@pytest.mark.anyio
async def test_boundary_null_values() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)

    @app.get("/null-error")
    def trigger_null() -> None:
        raise AppException("", payload=None)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
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
    register_exception_handlers(app)

    @app.get("/err-1")
    def err1() -> None:
        raise EntityNotFound("Resource missing")

    @app.post("/err-2")
    def err2(data: SampleInputSchema) -> dict[str, Any]:
        return data.model_dump()

    @app.get("/err-3")
    def err3() -> None:
        raise HTTPException(status_code=403, detail="Forbidden area")

    @app.get("/err-4")
    def err4() -> None:
        raise RuntimeError("Boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path, method, payload in [
            ("/err-1", "GET", None),
            ("/err-2", "POST", {}),
            ("/err-3", "GET", None),
            ("/err-4", "GET", None),
        ]:
            if method == "GET":
                res = await client.get(path)
            else:
                res = await client.post(path, json=payload)

            validated = ResponseWrapper[None].model_validate(res.json())
            assert validated.success is False
            assert validated.data is None
            assert isinstance(validated.message, str)
            assert isinstance(validated.meta, dict)
            assert "error_type" in validated.meta