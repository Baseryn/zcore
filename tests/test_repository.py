import uuid
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import load_only

from zcore.db.repository import BaseRepository
from zcore.db.setup import Base

class RepoTestModel(Base):
    __tablename__ = f"repo_test_model_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)

class RepoTestCreateSchema(BaseModel):
    name: str
    description: str | None = None

class RepoTestUpdateSchema(BaseModel):
    name: str | None = None
    description: str | None = None

class RepoTestRepository(BaseRepository[RepoTestModel]):
    def __init__(self, db: Any) -> None:
        super().__init__(RepoTestModel, db)

@pytest_asyncio.fixture(autouse=True)
async def setup_test_tables(test_engine: Any) -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.mark.anyio
@pytest.mark.parametrize(
    "name, description",
    [
        ("Test Item 1", "Description 1"),
        ("Test Item 2", None),
    ]
)
async def test_repo_create_and_get(db_session: Any, name: str, description: str | None) -> None:
    repo = RepoTestRepository(db_session)
    schema = RepoTestCreateSchema(name=name, description=description)
    
    created = await repo.create(schema)
    assert created.id is not None
    assert created.name == name
    assert created.description == description

    fetched_with_fields = await repo.get(id=created.id, fields=[RepoTestModel.name])
    assert fetched_with_fields is not None
    assert fetched_with_fields.name == name

@pytest.mark.anyio
@pytest.mark.parametrize(
    "schemas, expect_db_hit",
    [
        ([], False),
        (
            [
                RepoTestCreateSchema(name="Item A", description="Desc A"),
                RepoTestCreateSchema(name="Item B", description="Desc B")
            ],
            True
        ),
    ]
)
async def test_repo_create_multi_empty_and_filled(
    db_session: Any,
    schemas: list[RepoTestCreateSchema],
    expect_db_hit: bool
) -> None:
    repo = RepoTestRepository(db_session)
    
    spy_session = AsyncMock(wraps=db_session)
    spy_repo = RepoTestRepository(spy_session)

    if not expect_db_hit:
        results = await spy_repo.create_multi(schemas)
        assert results == []
        spy_session.add.assert_not_called()
        spy_session.add_all.assert_not_called()
    else:
        results = await repo.create_multi(schemas, refresh=True)
        assert len(results) == len(schemas)
        for i, item in enumerate(results):
            assert item.name == schemas[i].name

@pytest.mark.anyio
@pytest.mark.parametrize(
    "partial, expected_desc",
    [
        (True, "Original Desc"),
        (False, None),
    ]
)
async def test_repo_partial_update(db_session: Any, partial: bool, expected_desc: str | None) -> None:
    repo = RepoTestRepository(db_session)
    created = await repo.create(RepoTestCreateSchema(name="Original Name", description="Original Desc"))

    update_schema = RepoTestUpdateSchema(name="Updated Name")
    updated = await repo.update(created.id, update_schema, partial=partial)

    assert updated is not None
    assert updated.name == "Updated Name"
    assert updated.description == expected_desc

@pytest.mark.anyio
@pytest.mark.parametrize(
    "non_existent_id",
    [
        99999,
    ]
)
async def test_repo_delete_multi(db_session: Any, non_existent_id: int) -> None:
    repo = RepoTestRepository(db_session)
    
    item1 = await repo.create(RepoTestCreateSchema(name="Item 1"))
    item2 = await repo.create(RepoTestCreateSchema(name="Item 2"))

    targets = [item1.id, item2.id, non_existent_id]
    deleted = await repo.delete_multi(targets)

    assert len(deleted) == 2
    deleted_ids = {item.id for item in deleted}
    assert item1.id in deleted_ids
    assert item2.id in deleted_ids

    assert await repo.get(id=item1.id) is None
    assert await repo.get(id=item2.id) is None