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
    @abstractmethod
    async def setUp(self) -> None:
        pass

    @abstractmethod
    async def tearDown(self) -> None:
        pass


class ContainerSandbox(ZTestFixture):
    def __init__(self) -> None:
        self._singletons = {}
        self._scoped = {}
        self._factories = {}

    async def setUp(self) -> None:
        self._singletons = dict(container._singletons)
        self._scoped = dict(container._scoped_definitions)
        self._factories = dict(container._factories)

    async def tearDown(self) -> None:
        container._singletons = self._singletons
        container._scoped_definitions = self._scoped
        container._factories = self._factories


class EventDispatcherSandbox(ZTestFixture):
    def __init__(self, dispatcher: EventDispatcher | None = None) -> None:
        self._dispatcher = dispatcher
        self._subscribers_snapshot: dict[str, list[Any]] = {}

    async def setUp(self) -> None:
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
        if self._dispatcher is not None:
            self._dispatcher._subscribers.clear()
            for event, handlers in self._subscribers_snapshot.items():
                self._dispatcher._subscribers[event] = list(handlers)


class DatabaseRollback(ZTestFixture):
    def __init__(
        self,
        engine: AsyncEngine | None = None,
        db_dependency: Any | Sequence[Any] | None = None,
        app: FastAPI | None = None,
    ) -> None:
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
    def __init__(
        self,
        user_id: Any,
        scopes: list[str] | None = None,
        is_superuser: bool = False,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        self.user_id = user_id
        self.scopes = scopes or []
        self.is_superuser = is_superuser
        self.extra_context = extra_context or {}
        self._token: Any = None

    async def setUp(self) -> None:
        current_store = _request_context_store.get()
        new_store = dict(current_store)
        new_store["user_id"] = self.user_id
        new_store["scopes"] = self.scopes
        new_store["is_superuser"] = self.is_superuser
        for key, val in self.extra_context.items():
            new_store[key] = val
        self._token = _request_context_store.set(new_store)

    async def tearDown(self) -> None:
        if self._token:
            _request_context_store.reset(self._token)


class DependencyOverride(ZTestFixture):
    def __init__(self, app: FastAPI, stub: Any, override_func: Any) -> None:
        self.app = app
        self.stub = stub
        self.override_func = override_func

    async def setUp(self) -> None:
        self.app.dependency_overrides[self.stub] = self.override_func

    async def tearDown(self) -> None:
        if self.stub in self.app.dependency_overrides:
            del self.app.dependency_overrides[self.stub]


class AppLifespan(ZTestFixture):
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.lifespan_ctx: Any = None

    async def setUp(self) -> None:
        self.lifespan_ctx = self.app.router.lifespan_context(self.app)
        await self.lifespan_ctx.__aenter__()

    async def tearDown(self) -> None:
        if self.lifespan_ctx:
            await self.lifespan_ctx.__aexit__(None, None, None)


class ZTest(ZTestFixture):
    def __init__(self, *fixtures: ZTestFixture) -> None:
        self.fixtures = list(fixtures)

    async def setUp(self) -> None:
        for fixture in self.fixtures:
            await fixture.setUp()

    async def tearDown(self) -> None:
        for fixture in reversed(self.fixtures):
            await fixture.tearDown()