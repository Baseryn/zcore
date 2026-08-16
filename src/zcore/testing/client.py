import uuid
from abc import ABC
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from zcore.security import get_current_user_stub
from zcore.testing.fixtures import (
    AppLifespan,
    ContainerSandbox,
    DatabaseRollback,
    DependencyOverride,
    UserContext,
    ZTest,
)


class ZTestClient:
    def __init__(
        self,
        app: FastAPI,
        user_id: Any | None = None,
        scopes: list[str] | None = None,
        is_superuser: bool = False,
        use_db: bool = True,
        extra_context: dict[str, Any] | None = None,
        extra_user_attrs: dict[str, Any] | None = None,
    ) -> None:
        self.app = app
        self._client = None

        fixtures = [ContainerSandbox(), AppLifespan(app)]

        if use_db:
            fixtures.append(DatabaseRollback())

        if user_id:
            scopes_list = scopes or []
            fixtures.append(
                UserContext(
                    user_id=user_id, scopes=scopes_list, extra_context=extra_context
                )
            )

            async def get_mock_user() -> Any:
                user_id_val = user_id
                scopes_val = scopes_list
                is_superuser_val = is_superuser

                class GenericMockUser:
                    id = user_id_val
                    is_active = True
                    is_superuser = is_superuser_val
                    scopes = scopes_val

                    def __init__(self, extra_attrs: dict[str, Any]):
                        for k, v in extra_attrs.items():
                            setattr(self, k, v)

                return GenericMockUser(extra_user_attrs or {})

            fixtures.append(
                DependencyOverride(app, get_current_user_stub, get_mock_user)
            )

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
    scopes: list[str] = None
    extra_user_attrs: dict[str, Any] = None
    extra_context: dict[str, Any] = None

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        user_id_val = self.user_id or uuid.uuid4()
        async with ZTestClient(
            app=self.app,
            user_id=user_id_val,
            scopes=self.scopes,
            is_superuser=self.is_superuser,
            extra_context=self.extra_context,
            extra_user_attrs=self.extra_user_attrs,
        ) as client:
            yield client
