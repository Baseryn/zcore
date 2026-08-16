"""Plugin Architecture Protocol.

This module defines the structured contract interface that all ZCore plugins must satisfy
to register with the central application orchestrator Kernel.
"""

from typing import Protocol, runtime_checkable

from fastapi import FastAPI


@runtime_checkable
class Plugin(Protocol):
    """Protocol structure modeling standard Plugin lifecycle interfaces.

    Any module intended to run as a plugin component within the ZCore kernel context
    must comply with this runtime-checkable protocol interface.

    Attributes:
        name: Unique string identifying the plugin.
        version: Semantic version description of the plugin.
        dependencies: Identifiers listing direct upstream plugin dependency requirements.
    """

    name: str
    version: str
    dependencies: list[str]

    def setup(self, app: FastAPI) -> None:
        """Execute core module registration tasks and attach routes to the app.

        Args:
            app: The parent FastAPI application instance.
        """
        ...

    async def before_startup(self) -> None:
        """Perform pre-initialization operations before the application startup phases."""
        ...

    async def on_startup(self) -> None:
        """Execute core logic during application startup."""
        ...

    async def after_startup(self) -> None:
        """Execute post-initialization processes after application startup concludes."""
        ...

    async def on_shutdown(self) -> None:
        """Perform cleanup and release external resources during application shutdown."""
        ...
