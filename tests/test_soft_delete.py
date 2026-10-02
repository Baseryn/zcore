import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from zcore.db.repository import BaseRepository
from zcore.db.search import FilterItem, SearchRequest
from zcore.db.setup import Base
from zcore.db.soft_delete import SoftDeleteMixin
from zcore.exceptions.base import EntityNotFound
from zcore.service.base import BaseService


class SoftDeleteTestModel(Base, SoftDeleteMixin):
    __tablename__ = f"test_soft_delete_{uuid.uuid4().hex[:6]}"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))


class RegularTestModel(Base):
    __tablename__ = f"test_regular_{uuid.uuid4().hex[:6]}"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))


class SoftDeleteCreateSchema(BaseModel):
    name: str


class SoftDeleteUpdateSchema(BaseModel):
    name: str | None = None


class SoftDeleteRepository(BaseRepository[SoftDeleteTestModel]):
    def __init__(self, db: Any) -> None:
        super().__init__(SoftDeleteTestModel, db)


class RegularRepository(BaseRepository[RegularTestModel]):
    def __init__(self, db: Any) -> None:
        super().__init__(RegularTestModel, db)


class SoftDeleteHookService(BaseService[SoftDeleteTestModel]):
    def __init__(self, repository: SoftDeleteRepository) -> None:
        super().__init__(SoftDeleteTestModel, repository)
        self.hooks_called: list[str] = []

    async def pre_delete(self, id: Any, force: bool = False) -> None:
        self.hooks_called.append(f"pre_delete_force_{force}")

    async def post_delete(self, model: SoftDeleteTestModel, force: bool = False) -> None:
        self.hooks_called.append(f"post_delete_force_{force}")

    async def pre_delete_multi(self, ids: list[Any], force: bool = False) -> None:
        self.hooks_called.append(f"pre_delete_multi_force_{force}")

    async def post_delete_multi(self, models: list[SoftDeleteTestModel], force: bool = False) -> None:
        self.hooks_called.append(f"post_delete_multi_force_{force}")

    async def pre_restore(self, id: Any) -> None:
        self.hooks_called.append("pre_restore")

    async def post_restore(self, model: SoftDeleteTestModel) -> None:
        self.hooks_called.append("post_restore")

    async def pre_restore_multi(self, ids: list[Any]) -> None:
        self.hooks_called.append("pre_restore_multi")

    async def post_restore_multi(self, models: list[SoftDeleteTestModel]) -> None:
        self.hooks_called.append("post_restore_multi")


@pytest_asyncio.fixture(autouse=True)
async def setup_test_tables(test_engine: Any) -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def test_soft_delete_instance_state_transitions() -> None:
    entity = SoftDeleteTestModel(id=1, name="Item Alpha")

    assert entity.deleted_at is None
    assert entity.is_deleted is False

    entity.soft_delete()
    assert entity.deleted_at is not None
    assert isinstance(entity.deleted_at, datetime)
    assert entity.deleted_at.tzinfo is not None
    assert entity.is_deleted is True

    entity.restore()
    assert entity.deleted_at is None
    assert entity.is_deleted is False


@pytest.mark.anyio
async def test_soft_delete_repository_query_filtering(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)

    item1 = await repo.create(SoftDeleteCreateSchema(name="Active 1"))
    item2 = await repo.create(SoftDeleteCreateSchema(name="Active 2"))
    item3 = await repo.create(SoftDeleteCreateSchema(name="To Delete"))

    await repo.delete(item3.id)

    active_items = await repo.get_list()
    assert len(active_items) == 2
    active_ids = {i.id for i in active_items}
    assert item1.id in active_ids
    assert item2.id in active_ids
    assert item3.id not in active_ids

    fetched_active = await repo.get(id=item1.id)
    assert fetched_active is not None
    assert fetched_active.id == item1.id

    fetched_deleted = await repo.get(id=item3.id)
    assert fetched_deleted is None


@pytest.mark.anyio
async def test_soft_delete_repository_count_and_exist(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)

    item1 = await repo.create(SoftDeleteCreateSchema(name="Visible"))
    item2 = await repo.create(SoftDeleteCreateSchema(name="Hidden"))

    await repo.delete(item2.id)

    assert await repo.count() == 1
    assert await repo.count(SoftDeleteTestModel.name == "Visible") == 1
    assert await repo.count(SoftDeleteTestModel.name == "Hidden") == 0

    assert await repo.exist(id=item1.id) is True
    assert await repo.exist(id=item2.id) is False
    assert await repo.exist(name="Hidden") is False


@pytest.mark.anyio
async def test_soft_delete_repository_get_by_ids(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)

    item1 = await repo.create(SoftDeleteCreateSchema(name="Item 1"))
    item2 = await repo.create(SoftDeleteCreateSchema(name="Item 2"))
    item3 = await repo.create(SoftDeleteCreateSchema(name="Item 3"))

    await repo.delete(item2.id)

    results = await repo.get_by_ids(ids=[item1.id, item2.id, item3.id])
    assert len(results) == 2
    result_ids = {r.id for r in results}
    assert item1.id in result_ids
    assert item3.id in result_ids
    assert item2.id not in result_ids


@pytest.mark.anyio
async def test_soft_delete_search_engine_integration(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)

    item1 = await repo.create(SoftDeleteCreateSchema(name="Product Alpha"))
    item2 = await repo.create(SoftDeleteCreateSchema(name="Product Beta"))
    item3 = await repo.create(SoftDeleteCreateSchema(name="Product Gamma"))

    await repo.delete(item2.id)

    req = SearchRequest(
        filters=[
            FilterItem(field="name", op="startswith", value="Product")
        ]
    )
    search_results = await repo.search(req)
    assert len(search_results) == 2
    result_ids = {r.id for r in search_results}
    assert item1.id in result_ids
    assert item3.id in result_ids
    assert item2.id not in result_ids


@pytest.mark.anyio
async def test_soft_delete_single_and_restore(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)
    item = await repo.create(SoftDeleteCreateSchema(name="Lifecycle Item"))

    deleted = await repo.delete(item.id, force=False)
    assert deleted is not None
    assert deleted.is_deleted is True
    assert await repo.get(id=item.id) is None
    assert await repo.count() == 0

    restored = await repo.restore(item.id)
    assert restored is not None
    assert restored.id == item.id
    assert restored.is_deleted is False
    assert await repo.get(id=item.id) is not None
    assert await repo.count() == 1


@pytest.mark.anyio
async def test_soft_delete_force_delete_single(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)
    item = await repo.create(SoftDeleteCreateSchema(name="Permanent Delete"))

    deleted = await repo.delete(item.id, force=True)
    assert deleted is not None
    assert await repo.get(id=item.id) is None

    restored = await repo.restore(item.id)
    assert restored is None


@pytest.mark.anyio
async def test_soft_delete_force_delete_multi(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)
    item1 = await repo.create(SoftDeleteCreateSchema(name="Perm 1"))
    item2 = await repo.create(SoftDeleteCreateSchema(name="Perm 2"))
    item3 = await repo.create(SoftDeleteCreateSchema(name="Soft 3"))

    deleted_hard = await repo.delete_multi([item1.id, item2.id], force=True)
    assert len(deleted_hard) == 2

    deleted_soft = await repo.delete_multi([item3.id], force=False)
    assert len(deleted_soft) == 1
    assert deleted_soft[0].is_deleted is True

    restored = await repo.restore_multi([item1.id, item2.id, item3.id])
    assert len(restored) == 1
    assert restored[0].id == item3.id
    assert restored[0].is_deleted is False


@pytest.mark.anyio
async def test_soft_delete_restore_multi(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)
    item1 = await repo.create(SoftDeleteCreateSchema(name="Batch 1"))
    item2 = await repo.create(SoftDeleteCreateSchema(name="Batch 2"))
    item3 = await repo.create(SoftDeleteCreateSchema(name="Batch 3"))

    await repo.delete_multi([item1.id, item2.id, item3.id], force=False)
    assert await repo.count() == 0

    restored = await repo.restore_multi([item1.id, item3.id])
    assert len(restored) == 2
    restored_ids = {r.id for r in restored}
    assert item1.id in restored_ids
    assert item3.id in restored_ids
    assert item2.id not in restored_ids
    assert await repo.count() == 2


@pytest.mark.anyio
async def test_soft_delete_non_supported_model(db_session: Any) -> None:
    repo = RegularRepository(db_session)
    item = await repo.create(SoftDeleteCreateSchema(name="Regular Item"))

    assert repo._supports_soft_delete() is False

    deleted = await repo.delete(item.id, force=False)
    assert deleted is not None
    assert await repo.get(id=item.id) is None

    restored = await repo.restore(item.id)
    assert restored is None

    restored_multi = await repo.restore_multi([item.id])
    assert restored_multi == []


@pytest.mark.anyio
async def test_soft_delete_service_orchestration(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)
    service = SoftDeleteHookService(repo)

    item1 = await service.create(SoftDeleteCreateSchema(name="Service Item 1"))
    item2 = await service.create(SoftDeleteCreateSchema(name="Service Item 2"))

    deleted_soft = await service.delete(item1.id, force=False)
    assert deleted_soft.is_deleted is True
    assert "pre_delete_force_False" in service.hooks_called
    assert "post_delete_force_False" in service.hooks_called

    restored = await service.restore(item1.id)
    assert restored.is_deleted is False
    assert "pre_restore" in service.hooks_called
    assert "post_restore" in service.hooks_called

    await service.delete(item2.id, force=True)
    assert "pre_delete_force_True" in service.hooks_called
    assert "post_delete_force_True" in service.hooks_called

    with pytest.raises(EntityNotFound):
        await service.restore(item2.id)


@pytest.mark.anyio
async def test_soft_delete_service_multi_orchestration(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)
    service = SoftDeleteHookService(repo)

    item1 = await service.create(SoftDeleteCreateSchema(name="Multi 1"))
    item2 = await service.create(SoftDeleteCreateSchema(name="Multi 2"))

    deleted_multi = await service.delete_multi([item1.id, item2.id], force=False)
    assert len(deleted_multi) == 2
    assert "pre_delete_multi_force_False" in service.hooks_called
    assert "post_delete_multi_force_False" in service.hooks_called

    restored_multi = await service.restore_multi([item1.id, item2.id])
    assert len(restored_multi) == 2
    assert "pre_restore_multi" in service.hooks_called
    assert "post_restore_multi" in service.hooks_called