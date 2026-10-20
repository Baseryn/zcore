"""Custom API Route and Serialization.

This module provides specialized request, response, and routing behaviors for the ZCore
web layer. It delegates dynamic schema pruning and input validation to the back-end
Zchema infrastructure.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from zcore.context.context import ctx
from zcore.utils.helpers import json_dumps


class ZCoreRequest(Request):
    """Custom HTTP Request subclass caching body byte strings.

    Prevents downstream multiread lockups by caching evaluated request payload bytes.
    """

    _cached_body: bytes

    async def body(self) -> bytes:
        """Asynchronously retrieve and cache the request body bytes.

        Returns:
            The raw request body byte string.
        """
        if not hasattr(self, "_cached_body"):
            self._cached_body = await super().body()
        return self._cached_body


class ZCoreJSONResponse(JSONResponse):
    """Custom JSONResponse that utilizes the unified serialization stream.

    Since Zchema models recursively handle their own pruning during model
    serialization natively, this class simply serializes the payload using
    our custom encoder.
    """

    def render(self, content: Any) -> bytes:
        """Render the response content into a JSON byte string."""
        return json_dumps(content).encode("utf-8")


class ZCoreAPIRoute(APIRoute):
    """Custom API Route extending standard FastAPI route executions.

    Configures routes to use the `ZCoreJSONResponse` class, dynamically binds
    operational action context, and manages response caching headers based on
    security restrictions.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        current_class = self.response_class
        if hasattr(current_class, "value"):
            current_class = current_class.value
        if current_class == JSONResponse:
            self.response_class = ZCoreJSONResponse

    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        """Intercept and extend the default routing execution handler."""
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            zcore_request = ZCoreRequest(request.scope, request._receive)

            custom_action = None
            if self.openapi_extra and isinstance(self.openapi_extra, dict):
                custom_action = self.openapi_extra.get("action")

            if not custom_action:
                method_action_map = {
                    "post": "create",
                    "get": "view",
                    "put": "update",
                    "patch": "update",
                    "delete": "delete",
                }
                current_method = request.method.lower()
                custom_action = method_action_map.get(current_method, current_method)

            ctx.set("action", custom_action)

            response: Response = await original_route_handler(zcore_request)

            hidden_fields = ctx.restricted_fields
            if (
                hidden_fields
                and "application/json" in response.headers.get("content-type", "").lower()
            ):
                vary = response.headers.get("vary", "")
                target_vary_headers = ["Authorization", "Cookie"]
                existing_vary = [v.strip().lower() for v in vary.split(",")] if vary else []

                new_vary_elements = [
                    v for v in target_vary_headers if v.lower() not in existing_vary
                ]
                if new_vary_elements:
                    if vary:
                        response.headers["vary"] = f"{vary}, {', '.join(new_vary_elements)}"
                    else:
                        response.headers["vary"] = ", ".join(new_vary_elements)
                return response

            return response

        return custom_route_handler
