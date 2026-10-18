import uuid
from collections.abc import AsyncGenerator
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy import Column, Date, Integer, String
from sqlalchemy.orm import load_only

from zcore.context.context import ctx
from zcore.db.pagination import CursorParams, PageNumberParams
from zcore.db.repository import BaseRepository
from zcore.db.search import FilterItem, SearchRequest
from zcore.db.setup import Base
from zcore.exceptions.base import ForbiddenError, ValidationError


class RepoTestModel(Base):
    __tablename__ = f"repo_test_model_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)

    @classmethod
    def scope_query(cls, query: Any) -> Any:
        if getattr(cls, "_scoped_mode", False):
            return query.where(cls.name.startswith("Active"))
        return query


class RepoTestCreateSchema(BaseModel):
    name: str
    description: str | None = None


class RepoTestUpdateSchema(BaseModel):
    name: str | None = None
    description: str | None = None


class RepoTestRepository(BaseRepository[RepoTestModel]):
    def __init__(self, db: Any) -> None:
        super().__init__(RepoTestModel, db)


class DateCursorModel(Base):
    __tablename__ = f"date_cursor_model_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True, autoincrement=True)
    operation_date = Column(Date, nullable=False)


class DateCursorCreateSchema(BaseModel):
    operation_date: date


class DateCursorRepository(BaseRepository[DateCursorModel]):
    def __init__(self, db: Any) -> None:
        super().__init__(DateCursorModel, db)


class CompositePartitionModel(Base):
    __tablename__ = f"composite_part_model_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    operation_date = Column(Date, primary_key=True)
    message = Column(String, nullable=False)


class CompositeCreateSchema(BaseModel):
    id: int
    operation_date: date
    message: str


class CompositeUpdateSchema(BaseModel):
    message: str


class CompositeRepository(BaseRepository[CompositePartitionModel]):
    def __init__(self, db: Any) -> None:
        super().__init__(CompositePartitionModel, db)


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
    ],
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
                RepoTestCreateSchema(name="Item B", description="Desc B"),
            ],
            True,
        ),
    ],
)
async def test_repo_create_multi_empty_and_filled(
    db_session: Any,
    schemas: list[RepoTestCreateSchema],
    expect_db_hit: bool,
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
    ],
)
async def test_repo_partial_update(
    db_session: Any, partial: bool, expected_desc: str | None
) -> None:
    repo = RepoTestRepository(db_session)
    created = await repo.create(
        RepoTestCreateSchema(name="Original Name", description="Original Desc")
    )

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
    ],
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


@pytest.mark.anyio
async def test_repo_exist(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    await repo.create(RepoTestCreateSchema(name="UniqueItem", description="Desc"))
    assert await repo.exist(name="UniqueItem") is True
    assert await repo.exist(RepoTestModel.name == "UniqueItem") is True
    assert await repo.exist(name="NonExistent") is False


@pytest.mark.anyio
async def test_repo_count(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    await repo.create(RepoTestCreateSchema(name="ItemA"))
    await repo.create(RepoTestCreateSchema(name="ItemB"))
    assert await repo.count() == 2
    assert await repo.count(RepoTestModel.name == "ItemA") == 1


@pytest.mark.anyio
async def test_repo_get_by_ids_empty_and_options(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    res = await repo.get_by_ids([])
    assert res == []

    item = await repo.create(RepoTestCreateSchema(name="Test"))
    res2 = await repo.get_by_ids([item.id], fields=[RepoTestModel.name])
    assert len(res2) == 1
    assert res2[0].name == "Test"


@pytest.mark.anyio
async def test_repo_update_multi(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    item1 = await repo.create(RepoTestCreateSchema(name="A", description="D1"))
    item2 = await repo.create(RepoTestCreateSchema(name="B", description="D2"))

    data = {
        item1.id: RepoTestUpdateSchema(name="A_New", description="D1"),
        item2.id: RepoTestUpdateSchema(name="B_New", description="D2_New"),
    }

    updated = await repo.update_multi(data)
    assert len(updated) == 2

    fetched1 = await repo.get(id=item1.id)
    fetched2 = await repo.get(id=item2.id)
    assert fetched1.name == "A_New"
    assert fetched1.description == "D1"
    assert fetched2.name == "B_New"
    assert fetched2.description == "D2_New"


@pytest.mark.anyio
async def test_repo_delete_single(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    item = await repo.create(RepoTestCreateSchema(name="DelMe"))

    deleted = await repo.delete(item.id)
    assert deleted is not None
    assert deleted.name == "DelMe"

    assert await repo.get(id=item.id) is None
    assert await repo.delete(99999) is None


@pytest.mark.anyio
async def test_repo_create_multi_no_refresh(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    schemas = [
        RepoTestCreateSchema(name="NR1"),
        RepoTestCreateSchema(name="NR2"),
    ]
    results = await repo.create_multi(schemas, refresh=False)
    assert len(results) == 2
    assert results[0].name == "NR1"


@pytest.mark.anyio
async def test_repo_page_number_pagination(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    for i in range(15):
        await repo.create(RepoTestCreateSchema(name=f"Item_{i:02d}"))

    params = PageNumberParams(page=2, size=5, sort_by="name", sort_order="desc")
    res = await repo.get_list(pagination=params)

    assert res.meta["total"] == 15
    assert res.meta["page"] == 2
    assert res.meta["total_pages"] == 3
    assert res.meta["has_next"] is True
    assert res.meta["has_prev"] is True
    assert len(res.data) == 5
    assert res.data[0].name == "Item_09"


@pytest.mark.anyio
async def test_repo_page_number_sorting_validation(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    params = PageNumberParams(page=1, size=5, sort_by="invalid_col")
    with pytest.raises(ValidationError):
        await repo.get_list(pagination=params)


@pytest.mark.anyio
async def test_repo_cursor_pagination(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    for i in range(5):
        await repo.create(RepoTestCreateSchema(name=f"C_{i}"))

    params1 = CursorParams(size=2)
    res1 = await repo.get_list(pagination=params1)
    assert len(res1.data) == 2
    assert res1.meta["has_more"] is True
    assert res1.meta["next_cursor"] is not None

    params2 = CursorParams(size=2, cursor=res1.meta["next_cursor"])
    res2 = await repo.get_list(pagination=params2)
    assert len(res2.data) == 2
    assert res2.meta["has_more"] is True

    params3 = CursorParams(size=2, cursor=res2.meta["next_cursor"])
    res3 = await repo.get_list(pagination=params3)
    assert len(res3.data) == 1
    assert res3.meta["has_more"] is False
    assert res3.meta["next_cursor"] is None


@pytest.mark.anyio
async def test_repo_cursor_invalid(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    params = CursorParams(size=5, cursor="!!!invalid_b64!!!")
    with pytest.raises(ValidationError):
        await repo.get_list(pagination=params)


@pytest.mark.anyio
async def test_repo_search_engine_complex(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    await repo.create(RepoTestCreateSchema(name="Apple", description="Fruit"))
    await repo.create(RepoTestCreateSchema(name="Banana", description="Fruit"))
    await repo.create(RepoTestCreateSchema(name="Carrot", description="Vegetable"))

    req = SearchRequest(
        filters=[
            FilterItem(
                op="and",
                items=[
                    FilterItem(field="description", op="eq", value="Fruit"),
                    FilterItem(field="name", op="ilike", value="ba"),
                ],
            )
        ]
    )
    res = await repo.search(req)
    assert len(res) == 1
    assert res[0].name == "Banana"


@pytest.mark.anyio
async def test_repo_search_with_fields_and_options(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    await repo.create(RepoTestCreateSchema(name="LookupItem1", description="DeepDescription1"))
    await repo.create(RepoTestCreateSchema(name="LookupItem2", description="DeepDescription2"))

    req = SearchRequest(filters=[FilterItem(field="name", op="startswith", value="Lookup")])
    res = await repo.search(
        req, fields=[RepoTestModel.id, RepoTestModel.name], options=[load_only(RepoTestModel.name)]
    )
    assert len(res) == 2
    assert res[0].name == "LookupItem1"
    assert res[1].name == "LookupItem2"


@pytest.mark.anyio
async def test_repo_search_security_violation(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)

    token = ctx.initialize()
    table_suffix = RepoTestModel.__tablename__.split("_")[-1]
    ctx.restricted_fields = frozenset(
        [
            "description",
            f"repo_test_model_{table_suffix}.description",
        ]
    )

    req = SearchRequest(filters=[FilterItem(field="description", op="eq", value="Fruit")])

    try:
        with pytest.raises(ForbiddenError):
            await repo.search(req)
    finally:
        ctx.reset(token)


@pytest.mark.anyio
async def test_repo_scoped_row_level_isolation(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    await repo.create(RepoTestCreateSchema(name="Active_1"))
    await repo.create(RepoTestCreateSchema(name="Active_2"))
    await repo.create(RepoTestCreateSchema(name="Inactive_1"))

    assert await repo.count() == 3

    RepoTestModel._scoped_mode = True
    try:
        assert await repo.count() == 2
        res = await repo.get_list()
        for item in res:
            assert item.name.startswith("Active")
    finally:
        RepoTestModel._scoped_mode = False


@pytest.mark.anyio
async def test_create_multi_dialect_fallback_without_returning(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    schemas = [
        RepoTestCreateSchema(name="Fallback 1", description="FB 1"),
        RepoTestCreateSchema(name="Fallback 2", description="FB 2"),
    ]
    with patch.object(db_session.bind.dialect, "insert_returning", False):
        results = await repo.create_multi(schemas, refresh=True)
    assert len(results) == 2
    assert results[0].name == "Fallback 1"
    assert results[1].name == "Fallback 2"
    assert results[0].id is not None
    assert results[1].id is not None


@pytest.mark.anyio
async def test_delete_multi_dialect_fallback_without_returning(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    item1 = await repo.create(RepoTestCreateSchema(name="DelFB 1"))
    item2 = await repo.create(RepoTestCreateSchema(name="DelFB 2"))
    with patch.object(db_session.bind.dialect, "delete_returning", False):
        deleted = await repo.delete_multi([item1.id, item2.id, 999999])
    assert len(deleted) == 2
    deleted_ids = {d.id for d in deleted}
    assert item1.id in deleted_ids
    assert item2.id in deleted_ids
    assert await repo.get(id=item1.id) is None
    assert await repo.get(id=item2.id) is None


@pytest.mark.anyio
async def test_update_accepts_model_instance_directly(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    created = await repo.create(RepoTestCreateSchema(name="Original", description="Original Desc"))
    with patch.object(repo, "get", AsyncMock()) as mock_get:
        updated = await repo.update(
            target=created,
            schema=RepoTestUpdateSchema(name="Updated Direct", description="Updated Desc"),
        )
        mock_get.assert_not_called()
    assert updated is not None
    assert updated.id == created.id
    assert updated.name == "Updated Direct"
    assert updated.description == "Updated Desc"


@pytest.mark.anyio
async def test_update_multi_accepts_model_instances_as_keys(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    item1 = await repo.create(RepoTestCreateSchema(name="Item1", description="D1"))
    item2 = await repo.create(RepoTestCreateSchema(name="Item2", description="D2"))

    data = {
        item1: RepoTestUpdateSchema(name="Item1_Upd", description="D1_Upd"),
        item2: RepoTestUpdateSchema(name="Item2_Upd", description="D2_Upd"),
    }
    updated = await repo.update_multi(data)
    assert len(updated) == 2
    fetched1 = await repo.get(id=item1.id)
    fetched2 = await repo.get(id=item2.id)
    assert fetched1.name == "Item1_Upd"
    assert fetched1.description == "D1_Upd"
    assert fetched2.name == "Item2_Upd"
    assert fetched2.description == "D2_Upd"


@pytest.mark.anyio
async def test_delete_accepts_model_instance_directly(db_session: Any) -> None:
    repo = RepoTestRepository(db_session)
    item = await repo.create(RepoTestCreateSchema(name="Delete Direct"))
    with patch.object(repo, "get", AsyncMock()) as mock_get:
        deleted = await repo.delete(target=item)
        mock_get.assert_not_called()
    assert deleted is not None
    assert deleted.id == item.id
    assert await repo.get(id=item.id) is None


@pytest.mark.anyio
@pytest.mark.parametrize("order", ["desc", "asc"])
async def test_repo_search_with_cursor_pagination_and_sort(db_session: Any, order: str) -> None:
    repo = RepoTestRepository(db_session)
    for i in range(6):
        await repo.create(
            RepoTestCreateSchema(
                name=f"Alpha_{i:02d}",
                description=f"Desc_{(5 - i):02d}",
            )
        )

    sort_field = "description"
    search_req1 = SearchRequest(
        filters=[],
        sort=[{"field": sort_field, "order": order}],
    )
    cursor_params1 = CursorParams(size=3)

    result_page1 = await repo.search(search_req1, pagination=cursor_params1)
    assert len(result_page1.data) == 3
    assert result_page1.meta["has_more"] is True
    next_cursor = result_page1.meta["next_cursor"]
    assert next_cursor is not None

    search_req2 = SearchRequest(
        filters=[],
        sort=[{"field": sort_field, "order": order}],
    )
    cursor_params2 = CursorParams(size=3, cursor=next_cursor)

    result_page2 = await repo.search(search_req2, pagination=cursor_params2)
    assert len(result_page2.data) == 3
    assert result_page2.meta["has_more"] is False

    page1_ids = [item.id for item in result_page1.data]
    page2_ids = [item.id for item in result_page2.data]
    assert set(page1_ids).isdisjoint(set(page2_ids))

    all_descriptions = [item.description for item in result_page1.data + result_page2.data]
    expected_descriptions = sorted(all_descriptions, reverse=(order == "desc"))
    assert all_descriptions == expected_descriptions


@pytest.mark.anyio
async def test_repo_cursor_pagination_with_date_column_coercion(db_session: Any) -> None:
    repo = DateCursorRepository(db_session)
    for i in range(5):
        await repo.create(DateCursorCreateSchema(operation_date=date(2026, 10, 10 + i)))

    params1 = CursorParams(size=2, cursor_field="operation_date", order="desc")
    res1 = await repo.get_list(pagination=params1)
    assert len(res1.data) == 2
    assert res1.meta["has_more"] is True
    next_cursor = res1.meta["next_cursor"]
    assert next_cursor is not None

    params2 = CursorParams(size=2, cursor=next_cursor, cursor_field="operation_date", order="desc")
    res2 = await repo.get_list(pagination=params2)
    assert len(res2.data) == 2
    assert res2.meta["has_more"] is True

    first_page_dates = [item.operation_date for item in res1.data]
    second_page_dates = [item.operation_date for item in res2.data]
    assert all(isinstance(d, date) for d in first_page_dates + second_page_dates)
    assert set(first_page_dates).isdisjoint(set(second_page_dates))
    assert first_page_dates[0] > second_page_dates[0]


@pytest.mark.anyio
async def test_repo_composite_guarded_properties(db_session: Any) -> None:
    single_repo = RepoTestRepository(db_session)
    assert single_repo.is_composite_pk is False
    assert single_repo.pk is not None
    assert single_repo.pk_name == "id"

    comp_repo = CompositeRepository(db_session)
    assert comp_repo.is_composite_pk is True
    assert comp_repo.pk_names == ("id", "operation_date")

    with pytest.raises(AttributeError) as exc_pk:
        _ = comp_repo.pk
    assert "composite primary key" in str(exc_pk.value)

    with pytest.raises(AttributeError) as exc_pk_name:
        _ = comp_repo.pk_name
    assert "composite primary key" in str(exc_pk_name.value)


@pytest.mark.anyio
async def test_repo_composite_primary_key_crud(db_session: Any) -> None:
    repo = CompositeRepository(db_session)
    assert repo.is_composite_pk is True
    assert repo.pk_names == ("id", "operation_date")

    d1 = date(2026, 10, 18)
    d2 = date(2026, 10, 19)

    item1 = await repo.create(CompositeCreateSchema(id=1, operation_date=d1, message="First"))
    item2 = await repo.create(CompositeCreateSchema(id=2, operation_date=d2, message="Second"))

    assert await repo.exist(id=1, operation_date=d1) is True
    assert await repo.exist(id=99, operation_date=d1) is False

    fetched_by_tuple = await repo.get(repo._build_pk_condition((1, d1)))
    assert fetched_by_tuple is not None
    assert fetched_by_tuple.message == "First"

    fetched_by_dict = await repo.get(repo._build_pk_condition({"id": 2, "operation_date": d2}))
    assert fetched_by_dict is not None
    assert fetched_by_dict.message == "Second"

    by_ids_tuples = await repo.get_by_ids([(1, d1), (2, d2)])
    assert len(by_ids_tuples) == 2

    by_ids_dicts = await repo.get_by_ids(
        [{"id": 1, "operation_date": d1}, {"id": 2, "operation_date": d2}]
    )
    assert len(by_ids_dicts) == 2

    by_ids_models = await repo.get_by_ids([item1, item2])
    assert len(by_ids_models) == 2

    updated = await repo.update((1, d1), CompositeUpdateSchema(message="Updated First"))
    assert updated is not None
    assert updated.message == "Updated First"

    updated_items = await repo.update_multi(
        {
            (1, d1): CompositeUpdateSchema(message="Multi 1"),
            (2, d2): CompositeUpdateSchema(message="Multi 2"),
        }
    )
    assert len(updated_items) == 2
    refreshed1 = await repo.get(repo._build_pk_condition((1, d1)))
    assert refreshed1 is not None and refreshed1.message == "Multi 1"

    deleted = await repo.delete((1, d1))
    assert deleted is not None
    assert await repo.get(repo._build_pk_condition((1, d1))) is None

    deleted_multi = await repo.delete_multi([(2, d2)])
    assert len(deleted_multi) == 1
    assert await repo.get(repo._build_pk_condition((2, d2))) is None
