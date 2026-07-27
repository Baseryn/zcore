"""Custom API Route and Serialization.

This module provides specialized request, response, and routing behaviors for the ZCore
web layer. It delegates dynamic schema pruning and input validation to the bock-end 
Zchema infrastructure.
"""

from typing import Callable, Coroutine, Any, get_origin, get_args
from fastapi import Request, Response
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from zcore.utils.helpers import json_dumps
from zcore.web.response import ResponseWrapper
from zcore.context.context import ctx


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


def find_input_schema(dependant: Any) -> type[BaseModel] | None:
    """Analyze FastAPI dependency models to find the body input Pydantic schema."""
    for param in getattr(dependant, "body_params", []):
        annotation = getattr(param.field_info, "annotation", None) or getattr(param, "type_", None)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    return None


def find_output_schema(response_model: Any) -> type[BaseModel] | None:
    """Recursively analyze route response models to extract the target Pydantic schema."""
    if response_model is None:
        return None
    origin = get_origin(response_model)
    args = get_args(response_model)
    if origin is None:
        if isinstance(response_model, type) and issubclass(response_model, BaseModel):
            return response_model
        return None
    for arg in args:
        schema = find_output_schema(arg)
        if schema:
            return schema
    return None


class ZCoreAPIRoute(APIRoute):
    """Custom API Route extending standard FastAPI route executions.

    Configures routes to use the `ZCoreJSONResponse` class and dynamically generates 
    security-pruned schema definitions on demand when `?schema=true` is requested.
    """

    expose_schema: bool
    target_model: type[BaseModel] | None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        
        self.expose_schema = False
        if self.openapi_extra and isinstance(self.openapi_extra, dict):
            if self.openapi_extra.get("expose_schema"):
                self.expose_schema = True

        self.target_model = None

        if self.expose_schema:
            if self.methods and any(m in ["POST", "PUT", "PATCH"] for m in self.methods):
                self.target_model = find_input_schema(self.dependant)
            else:
                self.target_model = find_output_schema(self.response_model)

        current_class = self.response_class
        if hasattr(current_class, "value"):
            current_class = getattr(current_class, "value")
        if current_class == JSONResponse:
            self.response_class = ZCoreJSONResponse

    def get_route_handler(self) -> Callable[[Request], Coroutine[None, None, Response]]:
        """Intercept and extend the default routing execution handler."""
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            zcore_request = ZCoreRequest(request.scope, request._receive)
            
            method_action_map = {
                "post": "create",
                "get": "view",
                "put": "update",
                "patch": "patch",
                "delete": "delete"
            }
            current_method = request.method.lower()
            ctx.set("action", method_action_map.get(current_method, current_method))
            
            if self.expose_schema and zcore_request.query_params.get("schema") == "true":
                if not self.target_model:
                    return JSONResponse(
                        status_code=400,
                        content={"success": False, "message": "No schema defined for this endpoint."}
                    )
                
                # Dynamic generation dynamically invokes __get_pydantic_json_schema__ under active context
                schema_dict = self.target_model.model_json_schema()
                
                response_payload = ResponseWrapper(
                    success=True,
                    message="Schema generated successfully",
                    data=schema_dict,
                    meta={"restricted_fields": list(ctx.restricted_fields)}
                )
                return JSONResponse(content=response_payload.model_dump())

            response: Response = await original_route_handler(zcore_request)
            
            hidden_fields = ctx.restricted_fields
            if hidden_fields and "application/json" in response.headers.get("content-type", "").lower():
                vary = response.headers.get("vary", "")
                target_vary_headers = ["Authorization", "Cookie"]
                existing_vary = [v.strip().lower() for v in vary.split(",")] if vary else []
                
                new_vary_elements = [v for v in target_vary_headers if v.lower() not in existing_vary]
                if new_vary_elements:
                    if vary:
                        response.headers["vary"] = f"{vary}, {', '.join(new_vary_elements)}"
                    else:
                        response.headers["vary"] = ", ".join(new_vary_elements)
                return response
            
            return response

        return custom_route_handler