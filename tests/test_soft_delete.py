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


class SoftDeleteTestModel(Base, SoftDeleteMixin):
    __tablename__ = f"test_soft_delete_{uuid.uuid4().hex[:6]}"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))


class SoftDeleteCreateSchema(BaseModel):
    name: str


class SoftDeleteUpdateSchema(BaseModel):
    name: str | None = None


class SoftDeleteRepository(BaseRepository[SoftDeleteTestModel]):
    def __init__(self, db: Any) -> None:
        super().__init__(SoftDeleteTestModel, db)


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

    item3.soft_delete()
    await db_session.flush()

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

    item2.soft_delete()
    await db_session.flush()

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

    item2.soft_delete()
    await db_session.flush()

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

    item2.soft_delete()
    await db_session.flush()

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
async def test_soft_delete_restore_lifecycle_in_db(db_session: Any) -> None:
    repo = SoftDeleteRepository(db_session)

    item = await repo.create(SoftDeleteCreateSchema(name="Recoverable Item"))

    item.soft_delete()
    await db_session.flush()

    assert await repo.get(id=item.id) is None
    assert await repo.count() == 0

    item.restore()
    await db_session.flush()

    restored_item = await repo.get(id=item.id)
    assert restored_item is not None
    assert restored_item.id == item.id
    assert restored_item.is_deleted is False
    assert await repo.count() == 1