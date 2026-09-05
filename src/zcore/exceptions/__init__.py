"""ZCore Application Exceptions Package."""

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
    from zcore.exceptions.handlers import app_exception_handler

__all__ = [
    "AppException",
    "AuthError",
    "DuplicateEntity",
    "EntityNotFound",
    "ForbiddenError",
    "ValidationError",
    "app_exception_handler",
]


def __getattr__(name: str) -> Any:
    """Lazily import exception handlers to prevent circular dependencies with the web layer."""
    if name == "app_exception_handler":
        from zcore.exceptions.handlers import app_exception_handler

        return app_exception_handler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")