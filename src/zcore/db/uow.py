"""Unit of Work Pattern Implementation.

This module coordinates transactional business boundaries, ensuring that change sets
are executed or discarded atomically and that domain events are decoupled from commit
operations by delaying dispatch until database transactions succeed.
"""

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from zcore.kernel.events import EventDispatcher

logger = structlog.get_logger()


class UnitOfWork:
    """Coordinates database commits and buffers associated application domain events.

    Ensures that domain events are only dispatched after their associated database
    modifications have successfully committed. Implements the asynchronous context manager
    protocol to automate transactional commit and rollback behaviors.

    Attributes:
        session: The underlying asynchronous database connection.
        dispatcher: The central system dispatcher used for publishing events.
        _pending_events: A list of buffered tuple events (event_name, payload) awaiting
            successful commit.
    """

    def __init__(self, session: AsyncSession, dispatcher: EventDispatcher) -> None:
        """Initialize the UnitOfWork.

        Args:
            session: The active async database session.
            dispatcher: The system event dispatcher.
        """
        self.session = session
        self.dispatcher = dispatcher
        self._pending_events: list[tuple[str, Any]] = []

    def register_event(self, event_name: str, payload: Any) -> None:
        """Queue a domain event for post-commit dispatch.

        Args:
            event_name: The name/identifier of the event to queue.
            payload: Relevant data to transmit when dispatching the event.
        """
        self._pending_events.append((event_name, payload))

    async def commit(self) -> None:
        """Commit the database session and dispatch all pending events.

        This method attempts to commit the database session. If the commit succeeds,
        buffered domain events are popped and dispatched. If the commit fails, the
        session is rolled back and the original database exception is raised.

        Raises:
            Exception: Any exception encountered during the transactional database commit.
        """
        try:
            await self.session.commit()
        except Exception as e:
            logger.error(f"Transaction commit failed in UnitOfWork: {e}")
            await self.session.rollback()
            raise

        # Dispatch events safely after a guaranteed commit
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
        """Roll back the database session and clear all pending domain events."""
        await self.session.rollback()
        self._pending_events.clear()

    async def __aenter__(self) -> "UnitOfWork":
        """Enter the asynchronous context manager block.

        Configures the session metadata to signify it is now managed within a
        Unit of Work lifecycle boundary.

        Returns:
            The active UnitOfWork instance.
        """
        self.session.info["uow_managed"] = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the asynchronous context manager block.

        Automatically performs a rollback if an unhandled exception is encountered,
        or commits the transaction otherwise.

        Args:
            exc_type: The type of exception raised inside the block, if any.
            exc_val: The exception instance raised, if any.
            exc_tb: The traceback associated with the exception, if any.
        """
        self.session.info["uow_managed"] = False
        if exc_type:
            await self.rollback()
        else:
            await self.commit()
