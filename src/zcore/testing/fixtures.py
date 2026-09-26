"""ZCore Testing Fixtures and Sandboxing Engine.

This module provides test isolation fixtures covering IoC containers, event listeners,
asynchronous database transactions using rollback savepoints, dynamic context injection,
and lifecycle management. It supports both native ZCore configurations and custom engines
or dependencies for vanilla FastAPI applications.
"""

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from zcore.context.context import _request_context_store
from zcore.db.setup import db_manager
from zcore.kernel.di import _current_scope_id, container
from zcore.kernel.events import EventDispatcher


class ZTestFixture(ABC):
    """Abstract base class modeling lifecycle hooks for testing fixtures."""

    @abstractmethod
    async def setUp(self) -> None:
        """Execute setup actions prior to test execution."""
        pass

    @abstractmethod
    async def tearDown(self) -> None:
        """Execute cleanup actions after test execution concludes."""
        pass


class ContainerSandbox(ZTestFixture):
    """Fixture preserving IoC container state boundaries across test executions."""

    def __init__(self) -> None:
        """Initialize the container sandbox snapshot buffers."""
        self._singletons: dict[Any, Any] = {}
        self._scoped: dict[Any, Any] = {}
        self._factories: dict[Any, Any] = {}

    async def setUp(self) -> None:
        """Take an immutable snapshot of active container bindings."""
        self._singletons = dict(container._singletons)
        self._scoped = dict(container._scoped_definitions)
        self._factories = dict(container._factories)

    async def tearDown(self) -> None:
        """Restore container bindings to their pre-test state."""
        container._singletons = self._singletons
        container._scoped_definitions = self._scoped
        container._factories = self._factories


class EventDispatcherSandbox(ZTestFixture):
    """Fixture preventing event subscriber accumulation and cross-test listener leakage."""

    def __init__(self, dispatcher: EventDispatcher | None = None) -> None:
        """Initialize the event dispatcher sandbox.

        Args:
            dispatcher: Target EventDispatcher instance to sandbox.
                Defaults to resolving from the IoC container if available.
        """
        self._dispatcher = dispatcher
        self._subscribers_snapshot: dict[str, list[Any]] = {}

    async def setUp(self) -> None:
        """Take an isolated snapshot of registered event subscribers."""
        if self._dispatcher is None:
            try:
                self._dispatcher = container.resolve(EventDispatcher)
            except Exception:
                self._dispatcher = None

        if self._dispatcher is not None:
            self._subscribers_snapshot = {
                event: list(handlers)
                for event, handlers in self._dispatcher._subscribers.items()
            }

    async def tearDown(self) -> None:
        """Restore event subscribers to their pristine pre-test configuration."""
        if self._dispatcher is not None:
            self._dispatcher._subscribers.clear()
            for event, handlers in self._subscribers_snapshot.items():
                self._dispatcher._subscribers[event] = list(handlers)


class DatabaseRollback(ZTestFixture):
    """Fixture wrapping test executions in an atomic transaction rolled back via savepoints.

    Supports native ZCore database management as well as arbitrary user-provided engines
    and dependency overrides for vanilla FastAPI projects.
    """

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        db_dependency: Any | Sequence[Any] | None = None,
        app: FastAPI | None = None,
    ) -> None:
        """Initialize the database rollback fixture.

        Args:
            engine: Custom SQLAlchemy AsyncEngine instance. Defaults to db_manager._engine.
            db_dependency: Single or sequence of database session dependencies to override.
            app: Target FastAPI application instance requiring dependency overrides.
        """
        self.engine = engine
        self.db_dependency = (
            [db_dependency]
            if db_dependency and not isinstance(db_dependency, (list, tuple))
            else list(db_dependency or [])
        )
        self.app = app

        self.connection: Any = None
        self.transaction: Any = None
        self.session: AsyncSession | None = None
        self._scope_token: Any = None
        self._original_session_method: Any = None

    async def setUp(self) -> None:
        """Establish isolated connection, transaction savepoint, and register session overrides."""
        target_engine = self.engine or getattr(db_manager, "_engine", None)
        if target_engine is None:
            raise RuntimeError(
                "No database engine available for DatabaseRollback. "
                "Provide an engine to ZTestClient or initialize db_manager first."
            )

        self.connection = await target_engine.connect()
        self.transaction = await self.connection.begin()
        self.session = AsyncSession(
            bind=self.connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        scope_id = str(uuid.uuid4())
        self._scope_token = _current_scope_id.set(scope_id)
        container.register_scoped_instance(AsyncSession, self.session)

        @asynccontextmanager
        async def mock_session_manager() -> AsyncGenerator[AsyncSession, None]:
            yield self.session

        if getattr(db_manager, "_engine", None) is target_engine:
            self._original_session_method = db_manager.session
            db_manager.session = mock_session_manager

        if self.app is not None and self.db_dependency:
            for dep in self.db_dependency:
                self.app.dependency_overrides[dep] = lambda: self.session

    async def tearDown(self) -> None:
        """Revert transaction, close connections, and unregister session overrides."""
        if self.app is not None and self.db_dependency:
            for dep in self.db_dependency:
                self.app.dependency_overrides.pop(dep, None)

        if self._original_session_method:
            db_manager.session = self._original_session_method

        if self._scope_token:
            scope_id = _current_scope_id.get()
            if scope_id:
                container.clear_scope(scope_id)
            _current_scope_id.reset(self._scope_token)

        if self.session:
            await self.session.close()
        if self.transaction:
            await self.transaction.rollback()
        if self.connection:
            await self.connection.close()


class UserContext(ZTestFixture):
    """Fixture managing user identity, active permissions, and metadata in ZContext."""

    def __init__(
        self,
        user_id: Any,
        scopes: list[str] | None = None,
        is_superuser: bool = False,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        """Initialize user context fixture.

        Args:
            user_id: Polymorphic unique identifier of the simulated user.
            scopes: Security scopes granted to the simulated user.
            is_superuser: Flag denoting administrator privileges.
            extra_context: Arbitrary key-value parameters to seed into the context store.
        """
        self.user_id = user_id
        self.scopes = scopes or []
        self.is_superuser = is_superuser
        self.extra_context = extra_context or {}
        self._token: Any = None

    async def setUp(self) -> None:
        """Seed context parameters for the upcoming test scope."""
        current_store = _request_context_store.get()
        new_store = dict(current_store)
        new_store["user_id"] = self.user_id
        new_store["scopes"] = self.scopes
        new_store["is_superuser"] = self.is_superuser
        for key, val in self.extra_context.items():
            new_store[key] = val
        self._token = _request_context_store.set(new_store)

    async def tearDown(self) -> None:
        """Revert context parameters to their original pre-test state."""
        if self._token:
            _request_context_store.reset(self._token)


class DependencyOverride(ZTestFixture):
    """Fixture managing dynamic dependency substitutions on a FastAPI application."""

    def __init__(self, app: FastAPI, stub: Any, override_func: Any) -> None:
        """Initialize the dependency override fixture.

        Args:
            app: Target FastAPI application.
            stub: Target dependency callable or anchor to override.
            override_func: Replacement callable providing simulated behavior.
        """
        self.app = app
        self.stub = stub
        self.override_func = override_func

    async def setUp(self) -> None:
        """Register the dependency substitution."""
        self.app.dependency_overrides[self.stub] = self.override_func

    async def tearDown(self) -> None:
        """Remove the dependency substitution."""
        if self.stub in self.app.dependency_overrides:
            del self.app.dependency_overrides[self.stub]


class AppLifespan(ZTestFixture):
    """Fixture orchestrating application startup and teardown lifespan transitions."""

    def __init__(self, app: FastAPI) -> None:
        """Initialize application lifespan fixture.

        Args:
            app: Target FastAPI application to run lifespan against.
        """
        self.app = app
        self.lifespan_ctx: Any = None

    async def setUp(self) -> None:
        """Invoke application startup lifespan hooks."""
        self.lifespan_ctx = self.app.router.lifespan_context(self.app)
        await self.lifespan_ctx.__aenter__()

    async def tearDown(self) -> None:
        """Invoke application shutdown lifespan hooks."""
        if self.lifespan_ctx:
            await self.lifespan_ctx.__aexit__(None, None, None)


class ZTest(ZTestFixture):
    """Composite fixture orchestrating sequential setup and reversed teardown sweeps."""

    def __init__(self, *fixtures: ZTestFixture) -> None:
        """Initialize composite test coordinator.

        Args:
            *fixtures: Ordered sequence of ZTestFixture instances to coordinate.
        """
        self.fixtures = list(fixtures)

    async def setUp(self) -> None:
        """Execute setup routines for all registered fixtures sequentially."""
        for fixture in self.fixtures:
            await fixture.setUp()

    async def tearDown(self) -> None:
        """Execute teardown routines for all registered fixtures in reverse order."""
        for fixture in reversed(self.fixtures):
            await fixture.tearDown()