"""Security Authentication Dependencies.

This module provides placeholder stub dependencies for route-level dependency injection.
These stubs are designed to be overridden during application setup with concrete
authentication implementations.
"""

from zcore.security.protocols import UserProtocol


async def get_current_user_stub() -> UserProtocol:
    """Stub dependency for retrieving the currently authenticated user.

    This function acts as a dependency injection anchor. Downstream applications
    must override this stub using `app.dependency_overrides` during bootstrapping.

    Returns:
        An object implementing the UserProtocol.

    Raises:
        NotImplementedError: If invoked directly without a configured dependency override.
    """
    raise NotImplementedError(
        "Dependency override is required for get_current_user_stub"
    )
