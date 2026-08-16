"""Security Protocol Definitions.

This module defines the runtime-checkable `UserProtocol` which establishes the
structural contract required of ZCore user entities to integrate with authorization systems.
"""

import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class UserProtocol(Protocol):
    """Protocol establishing standard user characteristics.

    Any user model within the ZCore framework must implement this protocol to
    integrate with security middleware and route decorators.

    Attributes:
        id: Unique identifier key of the user.
        is_active: Boolean indicating if the user account is active.
        is_superuser: Boolean indicating if the user has superuser privileges.
    """

    id: uuid.UUID
    is_active: bool
    is_superuser: bool
