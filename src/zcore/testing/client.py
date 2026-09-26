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
from zcore.security.dependencies import get_current_user_stub
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
                    is_superuser = is_superuser
                    scopes = scopes_list

                    def __init__(self, attrs: dict[str, Any]):
                        for k, v in attrs.items():
                            setattr(self, k, v)

                return GenericMockUser(extra_user_attrs or {})

            auth_stubs: list[Any] = [get_current_user_stub]
            if user_dependency is not None:
                if isinstance(user_dependency, (list, tuple, set)):
                    auth_stubs.extend(user_dependency)
                else:
                    auth_stubs.append(user_dependency)

            for stub in auth_stubs:
                fixtures.append(DependencyOverride(app, stub, get_mock_user))

        self._orchestrator = ZTest(*fixtures)

    async def __aenter__(self) -> httpx.AsyncClient:
        await self._orchestrator.setUp()
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        await self._client.__aenter__()
        return self._client

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
        await self._orchestrator.tearDown()


class BaseZTest(ABC):
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