"""
ZCore Context Management Module.

This module provides a robust, thread-safe, and coroutine-aware context storage
system using Python's `contextvars` library. It is designed to manage
request-scoped state, such as authentication identifiers and security filters,
ensuring state isolation across asynchronous task boundaries and preventing
leaks between concurrent executions.
"""

import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

_request_context_store: ContextVar[dict[str, Any]] = ContextVar(
    "request_context_store", default={}
)


class ZContext:
    """
    Unified interface for managing asynchronous execution contexts.

    Encapsulates the logic for storing and retrieving scoped data. This class
    supports dynamic key-value storage while providing strongly-typed properties
    for common framework-level attributes like user identity and data restrictions.
    """

    @classmethod
    def set(cls, key: str, value: Any) -> None:
        """
        Stores a value in the current execution context.

        Args:
            key: The unique identifier for the context entry.
            value: The data to persist in the current scope.
        """
        current_store = _request_context_store.get()
        new_store = dict(current_store)
        new_store[key] = value
        _request_context_store.set(new_store)

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """
        Retrieves a value from the current execution context.

        Args:
            key: The identifier for the requested context entry.
            default: The fallback value if the key is not found.

        Returns:
            The associated value or the specified default.
        """
        return _request_context_store.get().get(key, default)

    @classmethod
    def remove(cls, key: str) -> None:
        """
        Deletes a specific entry from the current execution context.

        Args:
            key: The identifier to be removed from the context store.
        """
        current_store = _request_context_store.get()
        if key in current_store:
            new_store = dict(current_store)
            new_store.pop(key, None)
            _request_context_store.set(new_store)

    @classmethod
    def initialize(cls) -> Token[dict[str, Any]]:
        """
        Resets the context store to an empty state for the current scope.

        Returns:
            A contextvars Token used for restoring the previous state.
        """
        return _request_context_store.set({})

    @classmethod
    def reset(cls, token: Token[dict[str, Any]]) -> None:
        """
        Restores the context store to a state corresponding to the provided token.

        Args:
            token: A valid token returned by a previous context operation.
        """
        _request_context_store.reset(token)

    @property
    def user_id(self) -> uuid.UUID | None:
        """
        Retrieves the authenticated user identifier from the current context.

        Returns:
            The user's UUID if authenticated, otherwise None.
        """
        return self.get("user_id")

    @user_id.setter
    def user_id(self, value: uuid.UUID | str | None) -> None:
        """
        Sets and validates the user identifier for the current context.

        Args:
            value: A UUID instance, a valid UUID string, or None to clear.

        Raises:
            ValueError: If a string input is not a valid UUID format.
            TypeError: If the input type is unsupported.
        """
        if value is None:
            self.set("user_id", None)
            return

        if isinstance(value, str):
            try:
                validated_id = uuid.UUID(value)
            except ValueError as e:
                raise ValueError(f"Invalid UUID string: '{value}'") from e
        elif isinstance(value, uuid.UUID):
            validated_id = value
        else:
            raise TypeError("user_id must be a uuid.UUID, a valid UUID string, or None")

        self.set("user_id", validated_id)

    @property
    def restricted_fields(self) -> frozenset[str]:
        """
        Accesses the collection of data fields restricted in the current context.

        Returns:
            An immutable frozenset of restricted field paths.
        """
        return self.get("restricted_fields", frozenset())

    @restricted_fields.setter
    def restricted_fields(self, value: Iterable[str] | None) -> None:
        """
        Updates the restricted fields, ensuring immutability through frozenset.

        Args:
            value: An iterable of field paths or None to clear restrictions.
        """
        if value is None:
            self.set("restricted_fields", frozenset())
        else:
            self.set("restricted_fields", frozenset(value))

    @contextmanager
    def scope(self, **kwargs: Any) -> Any:
        """
        Context manager for localized state overrides within a block.

        Args:
            **kwargs: Attributes to set temporarily in the context.

        Yields:
            None
        """
        token = _request_context_store.set(dict(_request_context_store.get()))
        try:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
                else:
                    self.set(key, value)
            yield
        finally:
            _request_context_store.reset(token)


ctx = ZContext()
