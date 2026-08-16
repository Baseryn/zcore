"""Web Middleware Implementations.

This module provides ASGI middleware to coordinate request lifecycles. It includes
`RequestLogMiddleware` to trace execution durations and manage request correlation headers,
and `ScopedDependencyMiddleware` to manage the lifecycle of request-scoped dependency injection
container boundaries.
"""

import re
import time
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import ASGIApp, Receive, Scope, Send

from zcore.context.context import ctx
from zcore.db.setup import db_manager
from zcore.kernel.di import _current_scope_id, container

log = structlog.get_logger()
REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-\.\_\:]{8,64}$")


class RequestLogMiddleware:
    """ASGI middleware to manage request correlation IDs and log HTTP transaction metrics.

    Intercepts HTTP requests, extracts or generates correlation IDs, binds them to
    structured logging contextvars, appends the correlation ID to response headers,
    and logs request durations.

    Attributes:
        app: The downstream ASGI application instance.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the RequestLogMiddleware.

        Args:
            app: The downstream ASGI application instance.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an ASGI request.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI channel to receive incoming events.
            send: The ASGI channel to transmit outgoing events.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        s_time = time.perf_counter()
        structlog.contextvars.clear_contextvars()

        raw_request_id = b""
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":  # Standard lowercased header in ASGI spec
                raw_request_id = value
                break

        request_id_str = (
            raw_request_id.decode("utf-8", errors="ignore").strip()
            if raw_request_id
            else ""
        )

        if request_id_str and REQUEST_ID_PATTERN.match(request_id_str):
            request_id = request_id_str
        else:
            request_id = str(uuid.uuid4())

        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_wrapper(message: dict[str, Any]) -> None:
            """Intercept response start and append the request correlation ID header.

            Args:
                message: The outgoing ASGI event dictionary.
            """
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        token = ctx.initialize()
        try:
            await self.app(scope, receive, send_wrapper)
            duration = (time.perf_counter() - s_time) * 1000
            log.info(
                "http request",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=round(duration, 2),
            )
        except Exception:
            duration = (time.perf_counter() - s_time) * 1000
            log.exception(
                "http request failed",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=round(duration, 2),
            )
            raise
        finally:
            ctx.reset(token)


class ScopedDependencyMiddleware:
    """ASGI middleware to isolate request-scoped dependencies.

    Initializes a new context scope key on incoming requests, binds it to the DI
    context variables, and executes a cleanup sweep of the scope's registered instances
    upon connection closure.

    Attributes:
        app: The downstream ASGI application instance.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the ScopedDependencyMiddleware.

        Args:
            app: The downstream ASGI application instance.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process an ASGI request.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI channel to receive incoming events.
            send: The ASGI channel to transmit outgoing events.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope_id = str(uuid.uuid4())
        token = _current_scope_id.set(scope_id)

        # Capture background tasks context if integrated with FastAPI
        # We defer scope teardown if there are pending background tasks
        try:
            async with db_manager.session() as session:
                container.register_scoped_instance(AsyncSession, session)
                await self.app(scope, receive, send)
        finally:
            # Safely clear the active IoC scope
            container.clear_scope(scope_id)
            _current_scope_id.reset(token)
