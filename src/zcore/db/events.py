"""Database Lifecycle Event Dispatcher.

This module provides a secure bridge between database operations and the core application
event dispatcher. It ensures that transactional and lifecycle events can be dispatched
safely without blocking synchronous execution paths or greenlets.
"""

from typing import Any

import structlog

from zcore.kernel.events import EventDispatcher

logger = structlog.get_logger()

_global_dispatcher: EventDispatcher | None = None


def register_db_event_dispatcher(dispatcher: EventDispatcher) -> None:
    """Register the global event dispatcher instance for database lifecycle events.

    This function configures the central event dispatcher used by database modules to
    notify downstream systems of transactional changes or lifecycle transitions.

    Args:
        dispatcher: The core `EventDispatcher` instance to register.
    """
    global _global_dispatcher
    _global_dispatcher = dispatcher


async def dispatch_db_event(event_name: str, payload: Any) -> None:
    """Safely dispatch database lifecycle events within the active async event loop.

    This function dispatches database events asynchronously, catching and logging any
    exceptions encountered during execution to safeguard calling databases from handler failures.

    Args:
        event_name: The unique string identifier of the target event.
        payload: The contextual data or entity state accompanying the event.
    """
    if not _global_dispatcher:
        return
    try:
        await _global_dispatcher.dispatch(event_name, payload)
    except Exception as e:
        logger.error(
            f"Failed to dispatch database event '{event_name}': {e}", exc_info=True
        )
