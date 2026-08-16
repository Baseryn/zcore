import uuid
import pytest
import datetime
import pytest_asyncio

from typing import Any, AsyncGenerator
from sqlalchemy import Column, DateTime, Integer, String, select, UUID

from zcore.db.pagination import (
    CursorPagination,
    CursorParams,
    PageNumberPagination,
    PageNumberParams,
)
from zcore.db.setup import Base
from zcore.exceptions.base import ValidationError

class PaginationTestModel(Base):
    __tablename__ = f"pagination_test_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    created_at = Column(DateTime)

class PaginationUUIDTestModel(Base):
    __tablename__ = f"pagination_uuid_test_{uuid.uuid4().hex[:6]}"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String)
    created_at = Column(DateTime)

@pytest_asyncio.fixture(autouse=True)
async def setup_pagination_tables(test_engine: Any) -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.mark.anyio
@pytest.mark.parametrize(
    "include_count, expected_total, expected_pages",
    [
        (True, 25, 2),
        (False, None, None),
    ]
)
async def test_page_number_pagination(
    db_session: Any,
    include_count: bool,
    expected_total: int | None,
    expected_pages: int | None
) -> None:
    records = [
        PaginationTestModel(id=i, name=f"Item {i}", created_at=datetime.datetime.now())
        for i in range(1, 26)
    ]
    db_session.add_all(records)
    await db_session.flush()

    paginator = PageNumberPagination()
    params = PageNumberParams(page=1, size=20, include_count=include_count)
    
    result = await paginator.paginate(db_session, select(PaginationTestModel), params, PaginationTestModel)
    
    assert len(result.data) == 20
    assert result.meta["total"] == expected_total
    assert result.meta["total_pages"] == expected_pages
    assert result.meta["has_next"] is True

@pytest.mark.anyio
async def test_page_number_pagination_sorting(db_session: Any) -> None:
    records = [
        PaginationTestModel(id=1, name="Item C", created_at=datetime.datetime.now()),
        PaginationTestModel(id=2, name="Item A", created_at=datetime.datetime.now()),
        PaginationTestModel(id=3, name="Item B", created_at=datetime.datetime.now()),
    ]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = PageNumberPagination()
    params_asc = PageNumberParams(page=1, size=10, sort_by="name", sort_order="asc")
    result_asc = await paginator.paginate(db_session, select(PaginationTestModel), params_asc, PaginationTestModel)
    assert result_asc.data[0].name == "Item A"
    assert result_asc.data[2].name == "Item C"
    
    params_desc = PageNumberParams(page=1, size=10, sort_by="name", sort_order="desc")
    result_desc = await paginator.paginate(db_session, select(PaginationTestModel), params_desc, PaginationTestModel)
    assert result_desc.data[0].name == "Item C"
    assert result_desc.data[2].name == "Item A"

@pytest.mark.anyio
async def test_page_number_pagination_invalid_sort(db_session: Any) -> None:
    paginator = PageNumberPagination()
    params = PageNumberParams(page=1, size=10, sort_by="unreal_field")
    with pytest.raises(ValidationError) as exc_info:
        await paginator.paginate(db_session, select(PaginationTestModel), params, PaginationTestModel)
    assert "Invalid sort field: 'unreal_field'" in str(exc_info.value)

@pytest.mark.anyio
async def test_page_number_pagination_empty_db(db_session: Any) -> None:
    paginator = PageNumberPagination()
    params = PageNumberParams(page=1, size=10, include_count=True)
    result = await paginator.paginate(db_session, select(PaginationTestModel), params, PaginationTestModel)
    assert result.data == []
    assert result.meta["total"] == 0
    assert result.meta["total_pages"] == 0
    assert result.meta["has_next"] is False
    assert result.meta["has_prev"] is False

@pytest.mark.anyio
async def test_page_number_pagination_out_of_bounds(db_session: Any) -> None:
    records = [PaginationTestModel(id=i, name=f"Item {i}", created_at=datetime.datetime.now()) for i in range(1, 11)]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = PageNumberPagination()
    params_with_count = PageNumberParams(page=5, size=5, include_count=True)
    result_wc = await paginator.paginate(db_session, select(PaginationTestModel), params_with_count, PaginationTestModel)
    assert result_wc.data == []
    assert result_wc.meta["total"] == 10
    assert result_wc.meta["total_pages"] == 2
    assert result_wc.meta["has_next"] is False
    assert result_wc.meta["has_prev"] is True
    
    params_no_count = PageNumberParams(page=5, size=5, include_count=False)
    result_nc = await paginator.paginate(db_session, select(PaginationTestModel), params_no_count, PaginationTestModel)
    assert result_nc.data == []
    assert result_nc.meta["has_next"] is False
    assert result_nc.meta["has_prev"] is True

@pytest.mark.anyio
async def test_page_number_pagination_exact_multiple(db_session: Any) -> None:
    records = [PaginationTestModel(id=i, name=f"Item {i}", created_at=datetime.datetime.now()) for i in range(1, 21)]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = PageNumberPagination()
    params = PageNumberParams(page=2, size=10, include_count=True)
    result = await paginator.paginate(db_session, select(PaginationTestModel), params, PaginationTestModel)
    assert len(result.data) == 10
    assert result.meta["total"] == 20
    assert result.meta["total_pages"] == 2
    assert result.meta["has_next"] is False
    assert result.meta["has_prev"] is True

@pytest.mark.anyio
@pytest.mark.parametrize("order", ["asc", "desc"])
async def test_cursor_pagination_datetime(db_session: Any, order: str) -> None:
    dt1 = datetime.datetime(2026, 1, 1, 10, 0, 0, 123456)
    dt2 = datetime.datetime(2026, 1, 1, 10, 0, 0, 123456)
    dt3 = datetime.datetime(2026, 1, 1, 10, 0, 0, 654321)

    records = [
        PaginationTestModel(id=1, name="Item 1", created_at=dt1),
        PaginationTestModel(id=2, name="Item 2", created_at=dt2),
        PaginationTestModel(id=3, name="Item 3", created_at=dt3),
    ]
    db_session.add_all(records)
    await db_session.flush()

    paginator = CursorPagination(cursor_field="created_at", order=order)
    
    params1 = CursorParams(size=1)
    page1 = await paginator.paginate(db_session, select(PaginationTestModel), params1, PaginationTestModel)
    assert len(page1.data) == 1
    assert page1.meta["has_more"] is True
    assert page1.meta["next_cursor"] is not None

    expected_first_id = 3 if order == "desc" else 1
    assert page1.data[0].id == expected_first_id

    params2 = CursorParams(size=2, cursor=page1.meta["next_cursor"])
    page2 = await paginator.paginate(db_session, select(PaginationTestModel), params2, PaginationTestModel)
    assert len(page2.data) == 2
    assert page2.meta["has_more"] is False
    
    expected_remaining_ids = [2, 1] if order == "desc" else [2, 3]
    assert [item.id for item in page2.data] == expected_remaining_ids

@pytest.mark.anyio
async def test_cursor_pagination_empty_db(db_session: Any) -> None:
    paginator = CursorPagination(cursor_field="id")
    params = CursorParams(size=5)
    result = await paginator.paginate(db_session, select(PaginationTestModel), params, PaginationTestModel)
    assert result.data == []
    assert result.meta["has_more"] is False
    assert result.meta["next_cursor"] is None

@pytest.mark.anyio
async def test_cursor_pagination_invalid_field(db_session: Any) -> None:
    paginator = CursorPagination(cursor_field="unreal_field")
    params = CursorParams(size=5)
    with pytest.raises(ValidationError) as exc_info:
        await paginator.paginate(db_session, select(PaginationTestModel), params, PaginationTestModel)
    assert "Invalid cursor field: 'unreal_field'" in str(exc_info.value)

@pytest.mark.anyio
async def test_cursor_pagination_exact_transitions(db_session: Any) -> None:
    records = [PaginationTestModel(id=i, name=f"Item {i}", created_at=datetime.datetime.now()) for i in range(1, 11)]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = CursorPagination(cursor_field="id", order="asc")
    params1 = CursorParams(size=5)
    page1 = await paginator.paginate(db_session, select(PaginationTestModel), params1, PaginationTestModel)
    assert len(page1.data) == 5
    assert page1.meta["has_more"] is True
    assert page1.meta["next_cursor"] is not None
    
    params2 = CursorParams(size=5, cursor=page1.meta["next_cursor"])
    page2 = await paginator.paginate(db_session, select(PaginationTestModel), params2, PaginationTestModel)
    assert len(page2.data) == 5
    assert page2.meta["has_more"] is False
    assert page2.meta["next_cursor"] is None

@pytest.mark.anyio
async def test_cursor_pagination_uuid_primary_key(db_session: Any) -> None:
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    id3 = uuid.uuid4()
    
    dt1 = datetime.datetime(2026, 1, 1, 10, 0, 0)
    dt2 = datetime.datetime(2026, 1, 1, 11, 0, 0)
    dt3 = datetime.datetime(2026, 1, 1, 12, 0, 0)
    
    records = [
        PaginationUUIDTestModel(id=id1, name="Item 1", created_at=dt1),
        PaginationUUIDTestModel(id=id2, name="Item 2", created_at=dt2),
        PaginationUUIDTestModel(id=id3, name="Item 3", created_at=dt3),
    ]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = CursorPagination(cursor_field="created_at", order="desc")
    params1 = CursorParams(size=1)
    page1 = await paginator.paginate(db_session, select(PaginationUUIDTestModel), params1, PaginationUUIDTestModel)
    assert len(page1.data) == 1
    assert page1.data[0].id == id3
    assert page1.meta["has_more"] is True
    
    params2 = CursorParams(size=2, cursor=page1.meta["next_cursor"])
    page2 = await paginator.paginate(db_session, select(PaginationUUIDTestModel), params2, PaginationUUIDTestModel)
    assert len(page2.data) == 2
    assert page2.data[0].id == id2
    assert page2.data[1].id == id1
    assert page2.meta["has_more"] is False

@pytest.mark.anyio
async def test_cursor_pagination_timezone_normalization(db_session: Any) -> None:
    tz_naive = datetime.datetime(2026, 6, 1, 12, 0, 0)
    tz_aware = datetime.datetime(2026, 6, 1, 15, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=3)))
    
    records = [
        PaginationTestModel(id=1, name="Naive", created_at=tz_naive),
        PaginationTestModel(id=2, name="Aware", created_at=tz_aware),
    ]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = CursorPagination(cursor_field="created_at", order="asc")
    params1 = CursorParams(size=1)
    page1 = await paginator.paginate(db_session, select(PaginationTestModel), params1, PaginationTestModel)
    assert page1.data[0].id == 1
    
    params2 = CursorParams(size=1, cursor=page1.meta["next_cursor"])
    page2 = await paginator.paginate(db_session, select(PaginationTestModel), params2, PaginationTestModel)
    assert page2.data[0].id == 2

@pytest.mark.anyio
async def test_cursor_pagination_string_field(db_session: Any) -> None:
    records = [
        PaginationTestModel(id=1, name="Banana", created_at=datetime.datetime.now()),
        PaginationTestModel(id=2, name="Apple", created_at=datetime.datetime.now()),
        PaginationTestModel(id=3, name="Cherry", created_at=datetime.datetime.now()),
    ]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = CursorPagination(cursor_field="name", order="asc")
    params1 = CursorParams(size=1)
    page1 = await paginator.paginate(db_session, select(PaginationTestModel), params1, PaginationTestModel)
    assert page1.data[0].name == "Apple"
    
    params2 = CursorParams(size=2, cursor=page1.meta["next_cursor"])
    page2 = await paginator.paginate(db_session, select(PaginationTestModel), params2, PaginationTestModel)
    assert len(page2.data) == 2
    assert page2.data[0].name == "Banana"
    assert page2.data[1].name == "Cherry"

@pytest.mark.parametrize(
    "corrupted_cursor",
    [
        "invalid_base64_string",
        "eyJhIjogMX0",
        "!!!",
    ]
)
@pytest.mark.anyio
async def test_malformed_cursor_error(db_session: Any, corrupted_cursor: str) -> None:
    paginator = CursorPagination(cursor_field="id")
    params = CursorParams(size=10, cursor=corrupted_cursor)
    
    with pytest.raises(ValidationError) as exc_info:
        await paginator.paginate(db_session, select(PaginationTestModel), params, PaginationTestModel)
        
    assert "Malformed cursor parameter provided." in str(exc_info.value)

@pytest.mark.anyio
async def test_cursor_pagination_drift_prevention(db_session: Any) -> None:
    records = [
        PaginationTestModel(id=10, name="Item 10", created_at=datetime.datetime(2026, 1, 1, 10, 0, 0)),
        PaginationTestModel(id=20, name="Item 20", created_at=datetime.datetime(2026, 1, 1, 11, 0, 0)),
    ]
    db_session.add_all(records)
    await db_session.flush()
    
    paginator = CursorPagination(cursor_field="created_at", order="desc")
    params1 = CursorParams(size=1)
    page1 = await paginator.paginate(db_session, select(PaginationTestModel), params1, PaginationTestModel)
    assert page1.data[0].id == 20
    
    new_record = PaginationTestModel(id=30, name="Item 30", created_at=datetime.datetime(2026, 1, 1, 12, 0, 0))
    db_session.add(new_record)
    await db_session.flush()
    
    params2 = CursorParams(size=1, cursor=page1.meta["next_cursor"])
    page2 = await paginator.paginate(db_session, select(PaginationTestModel), params2, PaginationTestModel)
    assert len(page2.data) == 1
    assert page2.data[0].id == 10