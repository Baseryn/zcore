"""Asynchronous Event Dispatcher Engine.

This module provides concurrent event execution capabilities. It manages
subscription listings and executes synchronous or asynchronous handlers concurrently
utilizing the asyncio task gather protocols.
"""

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import structlog

logger = structlog.getLogger("zcore.events")

T = TypeVar("T")
EventHandler = Callable[..., Coroutine[Any, Any, Any] | Any]


def on_event(event_name: str) -> Callable[[Any], Any]:
    """Decorator to mark a service method as an active subscriber for an event channel.

    Args:
        event_name: The target event identifier string.
    """

    def decorator(func: Any) -> Any:
        func._event_listener = event_name
        return func

    return decorator


class EventDispatcher:
    """Coordinates event registration and execution.

    Allows disparate application layers to subscribe to and trigger system occurrences
    using loosely-coupled event keys.

    Attributes:
        _subscribers: Mapping of string event names to lists of registered handler callbacks.
    """

    def __init__(self) -> None:
        """Initialize the EventDispatcher."""
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Register an event handler to listen for a specific event key.

        Args:
            event_name: The target event identifier.
            handler: A callback handler to invoke when the event is triggered.
        """
        self._subscribers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        """Remove an event handler registration from a specific event key.

        Args:
            event_name: The target event identifier.
            handler: The callback handler to deregister.
        """
        if event_name in self._subscribers and handler in self._subscribers[event_name]:
            self._subscribers[event_name].remove(handler)

    def register_listeners(self, cls: type[Any], container: Any) -> None:
        """Inspect and dynamically register marked listener methods on the target service class.

        Ensures that the target class is resolved through the IoC container dynamically
        at execution time to preserve scoped lifecycles.

        Args:
            cls: The target service class type to inspect.
            container: The active IoCContainer instance used for class resolution.
        """
        for name, method in inspect.getmembers(
            cls, predicate=inspect.iscoroutinefunction
        ):
            event_name = getattr(method, "_event_listener", None)
            if event_name:

                async def lazy_handler(*args: Any, method_name: str = name, **kwargs: Any) -> Any:
                    instance = container.resolve(cls)
                    return await getattr(instance, method_name)(*args, **kwargs)

                self.subscribe(event_name, lazy_handler)

    async def dispatch(self, event_name: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Asynchronously dispatch an event, executing all handlers concurrently.

        Schedules coroutine-based listeners onto the async event loop using `asyncio.gather`
        to prevent blocking, while executing standard synchronous subscribers inline. Exceptions
        encountered inside callbacks are caught and logged to safeguard dispatch execution flow.

        Args:
            event_name: The event identifier to trigger.
            *args: Positional arguments to forward to subscribers.
            **kwargs: Keyword arguments to forward to subscribers.

        Returns:
            A list containing the return values compiled from all invoked event handlers.
        """
        handlers = self._subscribers.get(event_name, [])
        if not handlers:
            return []

        async_tasks = []
        sync_results = []

        for handler in handlers:
            try:
                if inspect.iscoroutinefunction(handler):
                    async_tasks.append(handler(*args, **kwargs))
                else:
                    sync_results.append(handler(*args, **kwargs))
            except Exception as e:
                logger.error(
                    f"Error preparing event handler {handler.__name__} for event '{event_name}': {e}"
                )

        async_results = []
        if async_tasks:
            results = await asyncio.gather(*async_tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error(
                        f"Unhandled exception in async event listener for event '{event_name}': {res}"
                    )
                    async_results.append(None)
                else:
                    async_results.append(res)

        return sync_results + async_results
