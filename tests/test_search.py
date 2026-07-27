import uuid
from typing import Any, AsyncGenerator, Generator, Type
import pytest
import pytest_asyncio
from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from zcore.db.search import FilterItem, SearchEngine, SearchRequest, SortItem
from zcore.db.setup import Base
from zcore.exceptions.base import ForbiddenError, ValidationError

class SearchUser(Base):
    __tablename__ = f"search_users_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    username = Column(String)
    password = Column(String)

class SearchProfile(Base):
    __tablename__ = f"search_profiles_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey(f"{SearchUser.__tablename__}.id"))
    bio = Column(String)
    user = relationship("SearchUser")

class SearchPost(Base):
    __tablename__ = f"search_posts_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey(f"{SearchProfile.__tablename__}.id"))
    title = Column(String)
    profile = relationship("SearchProfile")

class SearchComment(Base):
    __tablename__ = f"search_comments_{uuid.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey(f"{SearchPost.__tablename__}.id"))
    body = Column(String)
    post = relationship("SearchPost")

@pytest.fixture(autouse=True)
def mock_restricted_fields() -> Generator[None, None, None]:
    from zcore.context.context import ctx
    ctx.restricted_fields = {"password", "SearchUser.password"}
    yield
    ctx.restricted_fields = None

@pytest_asyncio.fixture(autouse=True)
async def setup_search_tables(test_engine: Any) -> AsyncGenerator[None, None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest_asyncio.fixture
async def seed_data(db_session: Any) -> None:
    user1 = SearchUser(id=1, username="admin%", password="foo")
    user2 = SearchUser(id=2, username="user_special", password="bar")
    user3 = SearchUser(id=3, username="guest", password="baz")
    db_session.add_all([user1, user2, user3])
    await db_session.flush()

@pytest.mark.parametrize(
    "op, val, expected_ids",
    [
        ("eq", "guest", [3]),
        ("ne", "guest", [1, 2]),
        ("gt", 1, [2, 3]),
        ("ilike", "%", [1]),
        ("ilike", "special", [2]),
    ]
)
@pytest.mark.anyio
async def test_search_all_operators(
    db_session: Any,
    seed_data: None,
    op: str,
    val: Any,
    expected_ids: list[int]
) -> None:
    engine = SearchEngine(SearchUser)
    filter_item = FilterItem(field="id" if isinstance(val, int) else "username", op=op, value=val)
    request = SearchRequest(filters=[filter_item], size=10)
    query = engine.build_base_query(request)
    result = await db_session.execute(query)
    items = list(result.scalars().all())
    assert len(items) == len(expected_ids)
    assert {item.id for item in items} == set(expected_ids)

def make_nested_filter(depth: int) -> FilterItem:
    if depth <= 1:
        return FilterItem(field="username", op="eq", value="guest")
    return FilterItem(op="and", items=[make_nested_filter(depth - 1)])

@pytest.mark.parametrize(
    "depth, should_raise",
    [
        (2, False),
        (4, True),
    ]
)
def test_search_max_filter_depth(depth: int, should_raise: bool) -> None:
    engine = SearchEngine(SearchUser)
    nested_filter = make_nested_filter(depth)
    request = SearchRequest(filters=[nested_filter], size=10)
    if should_raise:
        with pytest.raises(ValidationError) as exc_info:
            engine.build_base_query(request)
        assert "Search query filter structure is too complex" in str(exc_info.value)
    else:
        query = engine.build_base_query(request)
        assert query is not None

@pytest.mark.parametrize(
    "field_variant",
    [
        "password",
        "PASSWORD",
        "PaSsWoRd",
    ]
)
def test_search_restricted_field_bypass(field_variant: str) -> None:
    engine = SearchEngine(SearchUser)
    filter_req = SearchRequest(filters=[FilterItem(field=field_variant, op="eq", value="secret")])
    with pytest.raises(ForbiddenError) as exc_info:
        engine.build_base_query(filter_req)
    assert "restricted" in str(exc_info.value).lower()
    sort_req = SearchRequest(sort=[SortItem(field=field_variant, order="asc")])
    with pytest.raises(ForbiddenError) as exc_info:
        engine.build_base_query(sort_req)
    assert "restricted" in str(exc_info.value).lower()

@pytest.mark.parametrize(
    "paths, expected_error, error_message",
    [
        (["post"], None, ""),
        (["post.profile"], None, ""),
        (["post.profile.user"], None, ""),
        (["post.profile.user.non_existent"], ValidationError, "Invalid include relation path"),
        (["post.profile.user.bio.extra"], ValidationError, "exceeds the maximum limit of 3"),
    ]
)
def test_search_include_depth_and_relation(
    paths: list[str],
    expected_error: Type[Exception] | None,
    error_message: str
) -> None:
    engine = SearchEngine(SearchComment)
    request = SearchRequest(include=paths, size=10)
    if expected_error:
        with pytest.raises(expected_error) as exc_info:
            engine.build_base_query(request)
        assert error_message in str(exc_info.value)
    else:
        query = engine.build_base_query(request)
        assert query is not None