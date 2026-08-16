"""Unified API Response Envelope.

This module provides the generic `ResponseWrapper` schema, which formats both
success and failure API responses consistently across the ZCore framework.
"""

from typing import Any, Generic, TypeVar

from zcore.web.projection import Zchema

T = TypeVar("T")


class ResponseWrapper(Zchema, Generic[T]):
    """Standardized generic JSON API envelope container.

    Enforces a consistent payload structure for all application endpoints.

    Attributes:
        success: Boolean flag indicating if the operation was successful.
        message: Diagnostic string describing the outcome of the request.
        data: The typed payload content of the response.
        meta: Optional unstructured key-value metadata.
    """

    success: bool = True
    message: str = "Success"
    data: T | None = None
    meta: dict[str, Any] | None = None

    @classmethod
    def success_response(
        cls, data: T, message: str = "Success", meta: dict | None = None
    ) -> "ResponseWrapper[T]":
        """Construct a successful ResponseWrapper instance.

        Args:
            data: The primary typed payload to wrap.
            message: Custom output message detailing the outcome. Defaults to "Success".
            meta: Unstructured metadata dictionary. Defaults to None.

        Returns:
            A populated ResponseWrapper container.
        """
        return cls(success=True, message=message, data=data, meta=meta)
