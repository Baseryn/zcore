"""ZCore Application Exceptions and Handlers Package."""

from typing import TYPE_CHECKING, Any

from zcore.exceptions.base import (
    AppException,
    AuthError,
    DuplicateEntity,
    EntityNotFound,
    ForbiddenError,
    ValidationError,
)

if TYPE_CHECKING:
    from zcore.exceptions.handlers import (
        ZCoreJSONResponse,
        app_exception_handler,
        http_exception_handler,
        register_exception_handlers,
        request_validation_exception_handler,
        response_validation_exception_handler,
        unhandled_exception_handler,
    )

__all__ = [
    "AppException",
    "AuthError",
    "DuplicateEntity",
    "EntityNotFound",
    "ForbiddenError",
    "ValidationError",
    "ZCoreJSONResponse",
    "app_exception_handler",
    "http_exception_handler",
    "register_exception_handlers",
    "request_validation_exception_handler",
    "response_validation_exception_handler",
    "unhandled_exception_handler",
]


def __getattr__(name: str) -> Any:
    """Lazily import exception handlers to prevent circular dependencies with the web layer."""
    if name in (
        "ZCoreJSONResponse",
        "app_exception_handler",
        "http_exception_handler",
        "register_exception_handlers",
        "request_validation_exception_handler",
        "response_validation_exception_handler",
        "unhandled_exception_handler",
    ):
        import zcore.exceptions.handlers as handlers_module

        return getattr(handlers_module, name)

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")