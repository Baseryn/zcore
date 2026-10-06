"""Unit of Work Pattern Implementation.

This module coordinates transactional business boundaries across distributed and
modular monolith services. It supports re-entrant and nested execution scopes through
depth-aware propagation, ensuring that changes are committed atomically by the root
boundary while buffering domain events until transactions succeed.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from zcore.kernel.events import EventDispatcher

logger = structlog.get_logger()


class UnitOfWork:
    """Coordinates database commits and buffers associated application domain events.

    Ensures that domain events are only dispatched after their associated database
    modifications have successfully committed. Implements re-entrant depth-aware
    context management to safely coordinate nested transactions across independent
    domain modules.

    Attributes:
        session: The underlying asynchronous database connection.
        dispatcher: The central system dispatcher used for publishing events.
        _pending_events: Direct reference to accumulated domain events awaiting dispatch.
    """

    def __init__(self, session: AsyncSession, dispatcher: EventDispatcher) -> None:
        """Initialize the UnitOfWork.

        Args:
            session: The active async database session.
            dispatcher: The system event dispatcher.
        """
        self.session = session
        self.dispatcher = dispatcher

        if not isinstance(getattr(self.session, "info", None), dict):
            self.session.info = {}

        if "uow_events" not in self.session.info:
            self.session.info["uow_events"] = []

        self._pending_events: list[tuple[str, Any]] = self.session.info["uow_events"]

    def register_event(self, event_name: str, payload: Any) -> None:
        """Queue a domain event for post-commit dispatch.

        Events registered across nested unit-of-work scopes are buffered into the
        session store and dispatched collectively upon the successful root commit.

        Args:
            event_name: The name/identifier of the event to queue.
            payload: Relevant data to transmit when dispatching the event.
        """
        self._pending_events.append((event_name, payload))

    async def commit(self) -> None:
        """Commit the database session and dispatch all accumulated domain events.

        In nested transaction scopes, this method flushes pending operations to
        the database without committing the physical transaction until the outermost
        scope concludes.

        Raises:
            Exception: Any exception encountered during transactional commit.
        """
        current_depth = self.session.info.get("uow_depth", 0)
        if current_depth > 1:
            await self.session.flush()
            return

        try:
            await self.session.commit()
        except Exception as e:
            logger.error(f"Transaction commit failed in UnitOfWork: {e}")
            await self.session.rollback()
            raise

        while self._pending_events:
            event_name, payload = self._pending_events.pop(0)
            try:
                await self.dispatcher.dispatch(event_name, payload)
            except Exception as ex:
                logger.error(
                    f"UnitOfWork event handler failed for event '{event_name}': {ex}",
                    exc_info=True,
                )

    async def rollback(self) -> None:
        """Roll back the database session, clear event buffers, and reset transaction depth."""
        self.session.info["uow_depth"] = 0
        self.session.info["uow_managed"] = False
        self._pending_events.clear()
        await self.session.rollback()

    async def __aenter__(self) -> "UnitOfWork":
        """Enter the asynchronous context manager block.

        Increments the active nesting depth on the session and registers the unit-of-work
        lifecycle boundary.

        Returns:
            The active UnitOfWork instance.
        """
        current_depth = self.session.info.get("uow_depth", 0)
        self.session.info["uow_depth"] = current_depth + 1
        self.session.info["uow_managed"] = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the asynchronous context manager block.

        Safely manages transaction completion by rolling back if an error occurred,
        or committing only when exiting the outermost root transaction boundary.

        Args:
            exc_type: The type of exception raised inside the block, if any.
            exc_val: The exception instance raised, if any.
            exc_tb: The traceback associated with the exception, if any.
        """
        current_depth = max(0, self.session.info.get("uow_depth", 1) - 1)
        self.session.info["uow_depth"] = current_depth

        if exc_type is not None:
            self.session.info["uow_managed"] = False
            self.session.info["uow_depth"] = 0
            await self.rollback()
        elif current_depth == 0:
            self.session.info["uow_managed"] = False
            await self.commit()
