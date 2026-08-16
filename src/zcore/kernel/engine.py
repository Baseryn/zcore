"""Kernel Orchestration Subsystem.

This module coordinates application modules, managing registration, dependency resolution,
and topological sorting of Plugins. It orchestrates async startup and shutdown Lifespan hooks
associated with FastAPI applications.
"""

import graphlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from zcore.kernel.di import container
from zcore.kernel.events import EventDispatcher
from zcore.kernel.plugins import Plugin


class Kernel:
    """The central core coordinator for the application lifespan and plugins.

    Assembles registered plugins into a directed acyclic graph (DAG), resolves
    inter-plugin dependencies, and handles sequential lifespan transitions.

    Attributes:
        plugins: Registered plugin instances indexable by name.
        dispatcher: The central async application event dispatcher.
        _sorted_plugins: Cache of sorted plugin instances after topological sorting.
    """

    def __init__(self) -> None:
        """Initialize a new empty Kernel instance."""
        self.plugins: dict[str, Plugin] = {}
        self.dispatcher = EventDispatcher()
        self._sorted_plugins: list[Plugin] = []

    def add_plugin(self, plugin: Plugin) -> None:
        """Register an application plugin instance in the local registry.

        Args:
            plugin: The plugin object conforming to the Plugin protocol interface.
        """
        self.plugins[plugin.name] = plugin

    def _resolve_dependencies(self) -> list[Plugin]:
        """Resolve and sort application plugins topologically using dependency paths.

        Constructs a dependency graph from declared plugin structures and validates the
        integrity of the resolution path.

        Returns:
            A list of Plugin instances ordered according to their dependency paths.

        Raises:
            RuntimeError: If a cyclic reference is detected in the graph, or if a declared
                plugin dependency is missing from active configurations.
        """
        graph: dict[str, list[str]] = {}
        for name, plugin in self.plugins.items():
            graph[name] = plugin.dependencies

        sorter = graphlib.TopologicalSorter(graph)
        try:
            order = list(sorter.static_order())
        except graphlib.CycleError as e:
            raise RuntimeError(f"Cyclic dependency detected among plugins: {e}")

        resolved: list[Plugin] = []
        for name in order:
            if name in self.plugins:
                resolved.append(self.plugins[name])
            else:
                for p in self.plugins.values():
                    if name in p.dependencies:
                        raise RuntimeError(
                            f"Missing dependency: '{name}' required by plugin '{p.name}'"
                        )
        return resolved

    def setup(self, app: FastAPI) -> None:
        """Initialize plugin structures and register core singletons in the DI container.

        Constructs resolved plugin lists, invokes registration setup hooks, and publishes
        the primary EventDispatcher singleton into the shared container.

        Args:
            app: The root FastAPI application instance.
        """
        self._sorted_plugins = self._resolve_dependencies()
        for plugin in self._sorted_plugins:
            plugin.setup(app)

        container.register_singleton(EventDispatcher, self.dispatcher)

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncGenerator[None, None]:
        """Asynchronous context manager governing the active application lifespan.

        Sequentially invokes startup lifecycle phases on sorted plugin units
        and guarantees clean, reversed tear-down triggers upon termination.

        Args:
            app: The active running FastAPI instance.

        Yields:
            An active execution state context.
        """
        if not self._sorted_plugins:
            self._sorted_plugins = self._resolve_dependencies()

        for plugin in self._sorted_plugins:
            await plugin.before_startup()

        for plugin in self._sorted_plugins:
            await plugin.on_startup()

        for plugin in self._sorted_plugins:
            await plugin.after_startup()

        try:
            yield
        finally:
            for plugin in reversed(self._sorted_plugins):
                await plugin.on_shutdown()
