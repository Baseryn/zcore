import pytest
import asyncio
import uuid
from typing import Any, AsyncGenerator
from fastapi import FastAPI, Depends
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession

from zcore import Base, container, settings, get_db, ctx, db_manager
from zcore.testing import ZTestClient, BaseZTest
from zcore.security import get_current_user_stub, UserProtocol

class DummyTask(Base):
    __tablename__ = "dummy_tasks"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(100))

dummy_app = FastAPI()

@dummy_app.post("/tasks")
async def create_task(title: str, db: AsyncSession = Depends(get_db)):
    task = DummyTask(title=title)
    db.add(task)
    await db.flush()
    return {"id": str(task.id), "title": task.title}

@dummy_app.get("/me")
async def get_me(user: UserProtocol = Depends(get_current_user_stub)):
    return {
        "id": str(user.id),
        "is_superuser": user.is_superuser,
        "scopes": getattr(user, "scopes", []),
        "phone": getattr(user, "phone_number", None)
    }

@pytest.fixture(scope="function", autouse=True)
async def setup_test_engine():
    db_manager.init_app(db_url=settings.DATABASE_TEST_URL)
    async with db_manager._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with db_manager._engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db_manager.close()

@pytest.mark.asyncio
async def test_database_rollback_isolation():
    task_id = None
    async with ZTestClient(dummy_app, use_db=True) as client:
        res = await client.post("/tasks?title=CleanCode")
        assert res.status_code == 200
        task_id = res.json()["id"]
        
    async with ZTestClient(dummy_app, use_db=True) as client:
        async with db_manager.session() as session:
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
    
    async with ZTestClient(dummy_app, use_db=False) as client:
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
    ) as client:
        assert ctx.restricted_fields == frozenset(restricted)

@pytest.mark.asyncio
class TestClassBasedExecution(BaseZTest):
    app = dummy_app
    user_id = uuid.uuid4()
    is_superuser = True
    scopes = ["admin:delete"]
    extra_user_attrs = {"phone_number": "09121111111"}
    
    async def test_class_based_test_run(self):
        async with self.run() as client:
            res = await client.get("/me")
            assert res.status_code == 200
            data = res.json()
            assert data["id"] == str(self.user_id)
            assert data["is_superuser"] is True
            assert data["scopes"] == self.scopes
            assert data["phone"] == "09121111111"