"""Fine-Grained Role and Scope Permission Controls.

This module implements base permission abstractions for FastAPI endpoints. It supports 
checking general permissions, object-level checks, and validating active OAuth/OIDC scopes,
with optional bypass configurations for superusers.
"""

from abc import ABC, abstractmethod
from typing import Any
from fastapi import Request, Depends

from zcore.context import ctx
from zcore.exceptions import ForbiddenError, AuthError
from zcore.security.protocols import UserProtocol
from zcore.security.dependencies import get_current_user_stub


class BasePermission(ABC):
    """Abstract Base Class for defining route authorization rules.

    Can be injected directly into routing operations as callable dependencies.
    """

    @abstractmethod
    async def has_permission(self, request: Request, user: UserProtocol) -> bool:
        """Evaluate if the current user is authorized to execute the request.

        Args:
            request: The incoming FastAPI HTTP request.
            user: The user model conforming to UserProtocol.

        Returns:
            True if the user is authorized, False otherwise.
        """
        pass

    async def has_object_permission(self, request: Request, user: UserProtocol, obj: Any) -> bool:
        """Evaluate if the current user has access to a specific object resource.

        Args:
            request: The incoming FastAPI HTTP request.
            user: The user model conforming to UserProtocol.
            obj: The database record or entity instance to check.

        Returns:
            True if the user is authorized to access the object, False otherwise.
        """
        return True

    async def __call__(
        self, 
        request: Request, 
        user: UserProtocol = Depends(get_current_user_stub)
    ) -> UserProtocol:
        """FastAPI Dependency call hook orchestrating standard security assertions.

        Ensures that user identities exist, are marked active, and pass declared 
        permission checks.

        Args:
            request: The incoming FastAPI HTTP request.
            user: The resolved UserProtocol instance from the dependency provider.

        Returns:
            The validated UserProtocol instance.

        Raises:
            AuthError: If the user is unauthenticated or inactive.
            ForbiddenError: If permission criteria evaluate to False.
        """
        if not user:
            raise AuthError(message="Authentication required")
        
        if not user.is_active:
            raise AuthError(message="User is inactive")
            
        if not await self.has_permission(request, user):
            raise ForbiddenError(message="Access denied")
            
        return user


class HasScopes(BasePermission):
    """Permission checker enforcing specific scope validation.

    Requires that the authenticated user possesses the declared set of security scopes
    to pass.

    Attributes:
        required_scopes: A set of scopes required to pass validation.
        allow_superuser: If True, bypasses validation checks for superusers.
    """

    def __init__(self, *required_scopes: str, allow_superuser: bool = True):
        """Initialize the HasScopes instance.

        Args:
            *required_scopes: Variadic argument of scope key strings to require.
            allow_superuser: Flag to bypass evaluation for superuser accounts.
                Defaults to True.
        """
        self.required_scopes = set(required_scopes)
        self.allow_superuser = allow_superuser

    async def has_permission(self, request: Request, user: UserProtocol) -> bool:
        """Validate user scopes against the required set of scopes.

        Resolves scopes dynamically from the context scope mapping or user properties.

        Args:
            request: The active incoming FastAPI HTTP request.
            user: The user model conforming to UserProtocol.

        Returns:
            True if all required scopes are matching or if the user is a superuser, 
            False otherwise.
        """
        if self.allow_superuser and getattr(user, "is_superuser", False):
            return True
            
        # Dynamically fetch scopes from the requests' ZContext or the user object directly
        user_scopes = ctx.get("scopes") or getattr(user, "scopes", None) or set()
        
        # Coerce scope structures to ensure compatible validation checks
        if isinstance(user_scopes, (list, tuple, frozenset)):
            user_scopes = set(user_scopes)
        elif isinstance(user_scopes, str):
            user_scopes = {user_scopes}
            
        return self.required_scopes.issubset(user_scopes)