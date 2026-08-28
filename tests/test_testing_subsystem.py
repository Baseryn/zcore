import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any, ClassVar

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import String, event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from zcore import Base, container, ctx, db_manager, get_db, settings
from zcore.context.context import _request_context_store
from zcore.security import UserProtocol, get_current_user_stub
from zcore.testing import (
    BaseZTest,
    ContainerSandbox,
    DatabaseRollback,
    ZTest,
    ZTestClient,
    ZTestFixture,
)


class DummyTask(Base):
    __tablename__ = "dummy_tasks"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(100))

dummy_app = FastAPI()

lifespan_events = []

@asynccontextmanager
async def dummy_lifespan_ctx(app: FastAPI):
    lifespan_events.append("started")
    yield
    lifespan_events.append("shutdown")

lifespan_app = FastAPI(lifespan=dummy_lifespan_ctx)

@dummy_app.post("/tasks")
async def create_task(title: str, db: AsyncSession = Depends(get_db)):
    task = DummyTask(title=title)
    db.add(task)
    await db.flush()
    return {"id": str(task.id), "title": task.title}

@dummy_app.post("/tasks-commit")
async def create_task_commit(title: str, db: AsyncSession = Depends(get_db)):
    task = DummyTask(title=title)
    db.add(task)
    await db.commit()
    return {"id": str(task.id), "title": task.title}

@dummy_app.get("/me")
async def get_me(user: UserProtocol = Depends(get_current_user_stub)):
    return {
        "id": str(user.id),
        "is_superuser": user.is_superuser,
        "scopes": getattr(user, "scopes", []),
        "phone": getattr(user, "phone_number", None)
    }

@pytest.fixture(scope="module", autouse=True)
def setup_test_engine():
    db_manager.init_app(db_url=settings.DATABASE_TEST_URL)
    
    sync_eng = db_manager._engine.sync_engine
    
    @event.listens_for(sync_eng, "connect")
    def do_connect(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(sync_eng, "begin")
    def do_begin(conn):
        conn.exec_driver_sql("BEGIN")
        
    async def create_tables():
        async with db_manager._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
    async def drop_tables():
        async with db_manager._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            
    async def close_manager():
        await db_manager.close()
        
    asyncio.run(create_tables())
    yield
    asyncio.run(drop_tables())
    asyncio.run(close_manager())

@pytest.mark.asyncio
async def test_database_rollback_isolation():
    task_id = None
    async with ZTestClient(dummy_app, use_db=True) as client:
        res = await client.post("/tasks?title=CleanCode")
        assert res.status_code == 200
        task_id = res.json()["id"]
        
    async with ZTestClient(dummy_app, use_db=True) as client, db_manager.session() as session:
        res = await session.execute(
            select(DummyTask).where(DummyTask.id == uuid.UUID(task_id))
        )
        assert res.scalars().first() is None

@pytest.mark.asyncio
async def test_container_sandbox():
    class MockService:
        pass

    interface = MockService
    implementation = MockService()
    
    async with ZTestClient(dummy_app, use_db=False):
        container.register_singleton(interface, implementation)
        assert container.resolve(interface) is implementation
        
    assert container.resolve(interface) is not implementation

@pytest.mark.asyncio
async def test_user_authentication_mocking():
    uid = uuid.uuid4()
    scopes = ["tasks:write", "tasks:read"]
    
    async with ZTestClient(
        dummy_app, 
        user_id=uid, 
        scopes=scopes, 
        is_superuser=True,
        use_db=False
    ) as client:
        res = await client.get("/me")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == str(uid)
        assert data["is_superuser"] is True
        assert data["scopes"] == scopes

@pytest.mark.asyncio
async def test_extra_user_attributes():
    uid = uuid.uuid4()
    phone = "09120000000"
    
    async with ZTestClient(
        dummy_app,
        user_id=uid,
        use_db=False,
        extra_user_attrs={"phone_number": phone}
    ) as client:
        res = await client.get("/me")
        assert res.status_code == 200
        data = res.json()
        assert data["phone"] == phone

@pytest.mark.asyncio
async def test_extra_context_variables():
    uid = uuid.uuid4()
    restricted = ["tasks.secret_key"]
    
    async with ZTestClient(
        dummy_app,
        user_id=uid,
        use_db=False,
        extra_context={"restricted_fields": frozenset(restricted)}
    ):
        assert ctx.restricted_fields == frozenset(restricted)

@pytest.mark.asyncio
class TestClassBasedExecution(BaseZTest):
    app = dummy_app
    user_id = uuid.uuid4()
    is_superuser = True
    scopes: ClassVar[list[str]] = ["admin:delete"]
    extra_user_attrs: ClassVar[dict[str, Any]] = {"phone_number": "09121111111"}
    
    async def test_class_based_test_run(self):
        async with self.run() as client:
            res = await client.get("/me")
            assert res.status_code == 200
            data = res.json()
            assert data["id"] == str(self.user_id)
            assert data["is_superuser"] is True
            assert data["scopes"] == self.scopes
            assert data["phone"] == "09121111111"

@pytest.mark.asyncio
async def test_database_rollback_nested_commit():
    task_id = None
    async with ZTestClient(dummy_app, use_db=True) as client:
        res = await client.post("/tasks-commit?title=ExplicitCommit")
        assert res.status_code == 200
        task_id = res.json()["id"]

    async with ZTestClient(dummy_app, use_db=True) as client, db_manager.session() as session:
        res = await session.execute(
            select(DummyTask).where(DummyTask.id == uuid.UUID(task_id))
        )
        assert res.scalars().first() is None

@pytest.mark.asyncio
async def test_database_rollback_uninitialized_engine(monkeypatch: pytest.MonkeyPatch):
    original_engine = db_manager._engine
    monkeypatch.setattr(db_manager, "_engine", None)
    
    rollback_fixture = DatabaseRollback()
    with pytest.raises(RuntimeError) as exc:
        await rollback_fixture.setUp()
    assert str(exc.value) == "DatabaseManager engine is uninitialized."
    
    monkeypatch.setattr(db_manager, "_engine", original_engine)

@pytest.mark.asyncio
async def test_container_sandbox_deep_restore():
    class PreExistingService:
        pass
        
    class OverriddenService:
        pass
        
    container.register_singleton(PreExistingService, "original_singleton_value")
    container.register_scoped(OverriddenService, OverriddenService)
    
    sandbox = ContainerSandbox()
    await sandbox.setUp()
    
    container.register_singleton(PreExistingService, "new_temporary_value")
    assert container.resolve(PreExistingService) == "new_temporary_value"
    
    await sandbox.tearDown()
    assert container.resolve(PreExistingService) == "original_singleton_value"

@pytest.mark.asyncio
async def test_app_lifespan_execution():
    global lifespan_events
    lifespan_events.clear()
    
    async with ZTestClient(lifespan_app, use_db=False):
        assert "started" in lifespan_events
        assert "shutdown" not in lifespan_events
        
    assert "shutdown" in lifespan_events

@pytest.mark.asyncio
async def test_ztest_orchestrator_running_sequence():
    execution_order = []
    
    class DummySeqFixture(ZTestFixture):
        def __init__(self, identifier: str):
            self.identifier = identifier
            
        async def setUp(self) -> None:
            execution_order.append(f"{self.identifier}_setup")
            
        async def tearDown(self) -> None:
            execution_order.append(f"{self.identifier}_teardown")
            
    fixture_a = DummySeqFixture("A")
    fixture_b = DummySeqFixture("B")
    
    orchestrator = ZTest(fixture_a, fixture_b)
    await orchestrator.setUp()
    assert execution_order == ["A_setup", "B_setup"]
    
    await orchestrator.tearDown()
    assert execution_order == ["A_setup", "B_setup", "B_teardown", "A_teardown"]

@pytest.mark.asyncio
async def test_user_context_exception_safety():
    original_store = dict(_request_context_store.get())
    
    try:
        async with ZTestClient(dummy_app, user_id=uuid.uuid4(), use_db=False):
            raise ValueError("Forced error within execution block")
    except ValueError:
        pass
        
    assert _request_context_store.get() == original_store

@pytest.mark.asyncio
async def test_nested_ztest_clients():
    user_id_outer = uuid.uuid4()
    user_id_inner = uuid.uuid4()
    
    async with ZTestClient(dummy_app, user_id=user_id_outer, use_db=False):
        assert ctx.user_id == user_id_outer
        
        async with ZTestClient(dummy_app, user_id=user_id_inner, use_db=False):
            assert ctx.user_id == user_id_inner
            
        assert ctx.user_id == user_id_outer

@pytest.mark.asyncio
async def test_dependency_override_custom_preservation():
    class DevelopmentCustomDep:
        pass
        
    dummy_app.dependency_overrides[DevelopmentCustomDep] = lambda: "developer_preset"
    
    async with ZTestClient(dummy_app, user_id=uuid.uuid4(), use_db=False):
        assert dummy_app.dependency_overrides.get(DevelopmentCustomDep)() == "developer_preset"
        
    assert dummy_app.dependency_overrides.get(DevelopmentCustomDep)() == "developer_preset"
    del dummy_app.dependency_overrides[DevelopmentCustomDep]