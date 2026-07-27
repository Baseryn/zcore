"""Dynamic and Generic Authentication Base Layer.

This module provides the generic `BaseAuth` class to coordinate token decoding, 
caching logic, and dynamic context injection during FastAPI request cycles.
"""

from typing import Any, Generic, Type, TypeVar
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from zcore.cache import BaseCache
from zcore.context import ctx
from zcore.exceptions import AuthError
from zcore.security.security import Security

T = TypeVar("T", bound=BaseModel)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


class BaseAuth(Generic[T]):
    """Generic Template-Method authentication class.

    Encapsulates core verification paths, automated caching lookups, active-state assertions,
    and dynamically binds schema attributes directly onto the shared ZContext.
    """

    def __init__(
        self,
        user_schema: Type[T],
        identity_claim: str = "sub",
        token_type: str = "access",
        cache_prefix: str = "auth",
        cache_ttl: int = 300
    ) -> None:
        """Initialize the BaseAuth instance.

        Args:
            user_schema: The validated Pydantic model representation of the user.
            identity_claim: The claim attribute denoting user identity. Defaults to "sub".
            token_type: Target validated string within claims. Defaults to "access".
            cache_prefix: Cache prefix namespace. Defaults to "auth".
            cache_ttl: Expiration lifespan of cache items. Defaults to 300.
        """
        self.user_schema = user_schema
        self.identity_claim = identity_claim
        self.token_type = token_type
        self.cache = BaseCache(prefix=cache_prefix)
        self.cache_ttl = cache_ttl

    async def fetch_user(self, identity: str) -> Any:
        """Fetch the active user model from persistent storage.

        Must be implemented by concrete subclass implementations in the application layer.

        Args:
            identity: Uniquely identifying string value (e.g. username or phone number).

        Raises:
            NotImplementedError: If not overridden by the subclass.
        """
        raise NotImplementedError

    async def __call__(self, request: Request, token: str = Depends(oauth2_scheme)) -> T:
        """Execute core request interception authentication workflow.

        Args:
            request: The incoming FastAPI request instance.
            token: The extracted string token from authorization headers.

        Returns:
            The parsed Pydantic schema model representing the user.

        Raises:
            AuthError: If signature evaluation, user status validation, or type checks fail.
        """
        try:
            payload = Security.decode_jwt(token)
            identity = payload.get(self.identity_claim)
            if not identity or payload.get("type") != self.token_type:
                raise AuthError(message="Invalid token structure or type.")
        except Exception:
            raise AuthError(message="Invalid token or token has expired.")

        cache_key = f"user:{identity}"
        user_data = await self.cache.get(cache_key, target_type=self.user_schema)

        if not user_data:
            db_user = await self.fetch_user(identity)
            if not db_user or not getattr(db_user, "is_active", True):
                raise AuthError(message="User inactive or not found.")

            user_data = self.user_schema.model_validate(db_user)
            await self.cache.set(cache_key, user_data.model_dump(mode="json"), ttl=self.cache_ttl)

        if not getattr(user_data, "is_active", True):
            raise AuthError(message="User inactive")

        for field_name in user_data.model_fields:
            value = getattr(user_data, field_name)
            if field_name == "id":
                ctx.user_id = value
            elif field_name == "all_restricted_fields":
                ctx.restricted_fields = frozenset(value) if value else frozenset()
            else:
                ctx.set(field_name, value)

        return user_data