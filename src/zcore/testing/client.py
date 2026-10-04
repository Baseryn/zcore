"""ZCore Test Client and Automated Test Harness Infrastructure.

This module provides `ZTestClient` which wraps an ASGI application in a complete
test harness supporting isolated transactions, clean dependency overrides, polymorphic
identity simulation, event sandboxing, and zero-boilerplate schema generation.
"""

import asyncio
import concurrent.futures
import uuid
from abc import ABC
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from zcore.config import settings
from zcore.context.context import ctx
from zcore.db.setup import Base, db_manager
from zcore.security.dependencies import (
    get_current_user_stub,
    get_optional_user_stub,
)
from zcore.testing.fixtures import (
    AppLifespan,
    ContainerSandbox,
    DatabaseRollback,
    DependencyOverride,
    EventDispatcherSandbox,
    UserContext,
    ZTest,
    ZTestFixture,
)


def setup_test_database(
    metadata: Any | None = None,
    db_url: str | None = None,
    engine: AsyncEngine | None = None,
    drop_first: bool = True,
) -> None:
    """Synchronously create or recreate test database tables across any execution context.

    Automatically detects running event loops and executes table schema creation safely
    to support Pytest session-level setup fixtures with zero boilerplate.

    Args:
        metadata: SQLAlchemy MetaData instance containing model tables. Defaults to Base.metadata.
        db_url: Database connection URI. Defaults to Settings.DATABASE_TEST_URL.
        engine: Pre-constructed AsyncEngine instance.
        drop_first: If True, purges existing tables before recreation. Defaults to True.
    """
    target_metadata = metadata or Base.metadata
    target_url = db_url or getattr(
        settings, "DATABASE_TEST_URL", "sqlite+aiosqlite:///zcore_test.db"
    )

    async def _recreate() -> None:
        target_engine = engine
        if target_engine is None:
            if not getattr(db_manager, "_engine", None):
                db_manager.init_app(db_url=target_url)
            target_engine = db_manager._engine

        if target_engine is not None:
            async with target_engine.begin() as conn:
                if drop_first:
                    await conn.run_sync(target_metadata.drop_all)
                await conn.run_sync(target_metadata.create_all)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(asyncio.run, _recreate()).result()
    else:
        asyncio.run(_recreate())


class ZTestClient:
    """Asynchronous test client coordinating database transactions, context, and dependencies.

    Enables testing of native ZCore or vanilla FastAPI applications with automated rollback
    protection, authentication mocking, and event sandboxing.
    """

    def __init__(
        self,
        app: FastAPI,
        user_id: Any | None = None,
        scopes: list[str] | None = None,
        is_superuser: bool = False,
        use_db: bool = True,
        engine: AsyncEngine | None = None,
        db_dependency: Any | Sequence[Any] | None = None,
        user_dependency: Any | Sequence[Any] | None = None,
        user_model: type[BaseModel] | None = None,
        extra_context: dict[str, Any] | None = None,
        extra_user_attrs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the ZTestClient instance.

        Args:
            app: Target FastAPI application.
            user_id: Polymorphic identifier for the authenticated user (int, str, UUID).
            scopes: Security scopes to grant to the authenticated user.
            is_superuser: Boolean indicating whether superuser privileges are active.
            use_db: If True, wraps executions within transactional rollback savepoints.
            engine: Custom AsyncEngine instance to use instead of the global database manager.
            db_dependency: Single or sequence of database session dependencies to override.
            user_dependency: Single or sequence of custom authentication dependencies to override.
            user_model: Custom Pydantic model class to validate and instantiate the mock user.
            extra_context: Key-value parameters to seed into the execution context store.
            extra_user_attrs: Additional properties to populate on the mock user instance.
        """
        self.app = app
        self._client: httpx.AsyncClient | None = None

        fixtures: list[ZTestFixture] = [
            ContainerSandbox(),
            EventDispatcherSandbox(),
            AppLifespan(app),
        ]

        if use_db:
            fixtures.append(
                DatabaseRollback(
                    engine=engine,
                    db_dependency=db_dependency,
                    app=app,
                )
            )

        if user_id is not None:
            scopes_list = scopes or []
            fixtures.append(
                UserContext(
                    user_id=user_id,
                    scopes=scopes_list,
                    is_superuser=is_superuser,
                    extra_context=extra_context,
                )
            )

            user_attrs = {
                "id": user_id,
                "is_active": True,
                "is_superuser": is_superuser,
                "scopes": scopes_list,
                **(extra_user_attrs or {}),
            }

            async def get_mock_user() -> Any:
                ctx.user_id = user_id
                ctx.set("scopes", scopes_list)
                if is_superuser:
                    ctx.set("is_superuser", True)
                for k, v in (extra_user_attrs or {}).items():
                    ctx.set(k, v)

                if user_model is not None:
                    return user_model.model_validate(user_attrs)

                class GenericMockUser:
                    id = user_id
                    is_active = True

                    def __init__(self, attrs: dict[str, Any]) -> None:
                        self.id = user_id
                        self.is_active = True
                        self.is_superuser = is_superuser
                        self.scopes = scopes_list
                        for k, v in attrs.items():
                            setattr(self, k, v)

                GenericMockUser.is_superuser = is_superuser
                GenericMockUser.scopes = scopes_list

                return GenericMockUser(extra_user_attrs or {})

            auth_stubs: list[Any] = [get_current_user_stub, get_optional_user_stub]
            if user_dependency is not None:
                if isinstance(user_dependency, (list, tuple, set)):
                    auth_stubs.extend(user_dependency)
                else:
                    auth_stubs.append(user_dependency)

            for stub in auth_stubs:
                fixtures.append(DependencyOverride(app, stub, get_mock_user))

        self._orchestrator = ZTest(*fixtures)

    async def __aenter__(self) -> httpx.AsyncClient:
        """Enter test scope, initialize sandbox fixtures, and return configured AsyncClient."""
        await self._orchestrator.setUp()
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        await self._client.__aenter__()
        return self._client

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit test scope, close HTTP client, and trigger teardown on sandbox fixtures."""
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
        await self._orchestrator.tearDown()


class BaseZTest(ABC):
    """Declarative base class for structuring async test suites."""

    app: FastAPI = None
    user_id: Any = None
    is_active: bool = True
    is_superuser: bool = False
    scopes: list[str] | None = None
    engine: AsyncEngine | None = None
    db_dependency: Any | Sequence[Any] | None = None
    user_dependency: Any | Sequence[Any] | None = None
    user_model: type[BaseModel] | None = None
    extra_user_attrs: dict[str, Any] | None = None
    extra_context: dict[str, Any] | None = None

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        """Execute test case inside an automated ZTestClient context manager."""
        user_id_val = self.user_id if self.user_id is not None else uuid.uuid4()
        async with ZTestClient(
            app=self.app,
            user_id=user_id_val,
            scopes=self.scopes,
            is_superuser=self.is_superuser,
            engine=self.engine,
            db_dependency=self.db_dependency,
            user_dependency=self.user_dependency,
            user_model=self.user_model,
            extra_context=self.extra_context,
            extra_user_attrs=self.extra_user_attrs,
        ) as client:
            yield client