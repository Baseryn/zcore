"""ZCore Exception Definitions.

This module defines the primary domain-specific exception hierarchy used throughout
the framework. These exceptions are mapped to standard HTTP status codes, allowing
consistent translation from interior business failures into client-facing HTTP responses.
"""


class AppException(Exception):
    """Base exception class for all custom runtime errors within the ZCore application.

    Acts as the parent class for errors that need to be captured by global HTTP handlers
    and returned as standardized API error responses.

    Attributes:
        status_code: The target HTTP status code mapped to this exception. Defaults to 500.
        message: A diagnostic string describing the nature of the execution failure.
        payload: Optional structured metadata dictionary providing additional context.
    """

    status_code: int = 500

    def __init__(self, message: str, payload: dict | None = None) -> None:
        """Initialize the AppException.

        Args:
            message: A descriptive diagnostic warning detailing the exception.
            payload: Optional dictionary housing contextual parameters or error breakdowns.
                Defaults to None.
        """
        super().__init__(message)
        self.message = message
        self.payload = payload


class EntityNotFound(AppException):
    """Raised when a requested database record or entity identifier is missing.

    Attributes:
        status_code: Map status code to 404 (Not Found).
    """

    status_code = 404


class DuplicateEntity(AppException):
    """Raised when a unique constraint validation fails, such as duplicate records.

    Attributes:
        status_code: Map status code to 409 (Conflict).
    """

    status_code = 409


class AuthError(AppException):
    """Raised when client requests fail authentication processes.

    Attributes:
        status_code: Map status code to 401 (Unauthorized).
    """

    status_code = 401


class ForbiddenError(AppException):
    """Raised when an authenticated client lacks necessary permissions to proceed.

    Attributes:
        status_code: Map status code to 403 (Forbidden).
    """

    status_code = 403


class ValidationError(AppException):
    """Raised when an incoming schema or business parameter violates constraints.

    Attributes:
        status_code: Map status code to 400 (Bad Request).
    """

    status_code = 400
