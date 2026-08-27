import asyncio
import contextlib
import os
import shutil
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from zcore.cache.base import close_cache
from zcore.config import Settings, initialize_settings
from zcore.db.setup import Base, db_manager, get_db
from zcore.kernel.di import _current_scope_id, container
from zcore.kernel.engine import Kernel


class MockSessionContext:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncSession:
        return self.session

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

class MockRedisPubSub:
    def __init__(self, queue: asyncio.Queue) -> None:
        self.queue = queue

    async def psubscribe(self, pattern: str) -> None:
        pass

    async def punsubscribe(self, pattern: str) -> None:
        pass

    async def listen(self) -> AsyncGenerator[dict, None]:
        while True:
            msg = await self.queue.get()
            yield msg

    async def close(self) -> None:
        pass

class MockRedis:
    def __init__(self) -> None:
        self.storage: dict[str, Any] = {}
        self.pubsub_queue: asyncio.Queue = asyncio.Queue()

    async def get(self, key: str) -> Any:
        return self.storage.get(key)

    async def set(self, key: str, value: Any, ex: Any = None) -> None:
        self.storage[key] = value

    async def delete(self, key: str) -> None:
        self.storage.pop(key, None)

    def pubsub(self) -> MockRedisPubSub:
        return MockRedisPubSub(self.pubsub_queue)

    async def publish(self, channel: str, message: str) -> None:
        await self.pubsub_queue.put({
            "type": "pmessage",
            "channel": channel,
            "data": message
        })

    async def close(self) -> None:
        pass

@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"

@pytest.fixture(scope="session")
def test_storage_dir() -> str:
    return os.getenv("TEST_STORAGE_PATH", "./test_storage")

@pytest.fixture(scope="session")
def test_db_url() -> str:
    return os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

@pytest.fixture(scope="session", autouse=True)
def clean_test_storage(test_storage_dir: str) -> Generator[None, None, None]:
    if os.path.exists(test_storage_dir):
        shutil.rmtree(test_storage_dir)
    yield
    if os.path.exists(test_storage_dir):
        shutil.rmtree(test_storage_dir)

@pytest_asyncio.fixture
async def test_engine(test_db_url: str) -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(test_db_url, echo=False)
    db_manager._engine = engine
    db_manager._session_factory = lambda: None
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint"
        )
        original_session_method = db_manager.session
        db_manager.session = lambda: MockSessionContext(session)
        yield session
        await transaction.rollback()
        db_manager.session = original_session_method

@pytest.fixture(autouse=True)
def reset_di_container(test_db_url: str, test_storage_dir: str) -> Generator[None, None, None]:
    container._singletons.clear()
    container._scoped_definitions.clear()
    container._factories.clear()
    container._constructor_cache.clear()
    container._dependency_signature_cache.clear()
    _current_scope_id.set(None)
    test_settings = Settings(
        DATABASE_URL=test_db_url,
        SECRET_KEY=os.getenv("TEST_SECRET_KEY", "test-secret-key-12345678901234567890123456789012"),
        STORAGE_PATH=test_storage_dir
    )
    initialize_settings(test_settings)
    yield

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch: pytest.MonkeyPatch) -> Generator[MockRedis, None, None]:
    mock_client = MockRedis()
    monkeypatch.setattr("zcore.cache.base._shared_redis_client", mock_client)
    monkeypatch.setattr("zcore.web.streams._stream_redis_client", mock_client)
    yield mock_client

@pytest_asyncio.fixture(autouse=True)
async def cleanup_background_tasks() -> AsyncGenerator[None, None]:
    yield
    await close_cache()
    tasks = [t for t in asyncio.all_tasks() if t != asyncio.current_task()]
    for task in tasks:
        task_name = str(task)
        if "start_eviction_loop" in task_name or "listen_to_redis" in task_name:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

@pytest_asyncio.fixture
async def test_app(db_session: AsyncSession) -> AsyncGenerator[FastAPI, None]:
    app = FastAPI()
    kernel = Kernel()
    kernel.setup(app)
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session
    app.dependency_overrides[get_db] = override_get_db
    yield app
    app.dependency_overrides.clear()

@pytest_asyncio.fixture
async def async_client(test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client