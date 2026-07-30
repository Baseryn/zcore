import uuid

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import FastAPI

from zcore.kernel.di import container, _current_scope_id
from zcore.db.setup import db_manager
from zcore.context.context import _request_context_store


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

class DatabaseRollback(ZTestFixture):
    def __init__(self) -> None:
        self.connection = None
        self.transaction = None
        self.session = None
        self._scope_token = None
        self._original_session_method = None

    async def setUp(self) -> None:
        if not db_manager._engine:
            raise RuntimeError("DatabaseManager engine is uninitialized.")
        
        self.connection = await db_manager._engine.connect()
        self.transaction = await self.connection.begin()
        self.session = AsyncSession(
            bind=self.connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint"
        )

        scope_id = str(uuid.uuid4())
        self._scope_token = _current_scope_id.set(scope_id)
        container.register_scoped_instance(AsyncSession, self.session)

        @asynccontextmanager
        async def mock_session_manager() -> AsyncGenerator[AsyncSession, None]:
            yield self.session

        self._original_session_method = db_manager.session
        db_manager.session = mock_session_manager

    async def tearDown(self) -> None:
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
        extra_context: dict[str, Any] | None = None
    ) -> None:
        self.user_id = user_id
        self.scopes = scopes or []
        self.extra_context = extra_context or {}
        self._token = None

    async def setUp(self) -> None:
        current_store = _request_context_store.get()
        new_store = dict(current_store)
        new_store["user_id"] = self.user_id
        new_store["scopes"] = self.scopes
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
        self.lifespan_ctx = None

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