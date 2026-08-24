import uuid as uuid_pkg
import pytest
import pytest_asyncio
from datetime import datetime

from sqlalchemy.orm import relationship
from sqlalchemy import Column, ForeignKey, Integer, String, DateTime, Boolean, Uuid
from typing import Any, AsyncGenerator, Generator, Type

from zcore.db.setup import Base
from zcore.db.search import FilterItem, SearchEngine, SearchRequest, SortItem
from zcore.exceptions.base import ForbiddenError, ValidationError

class SearchUser(Base):
    __tablename__ = f"search_users_{uuid_pkg.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    username = Column(String)
    password = Column(String)
    created_at = Column(DateTime)
    is_active = Column(Boolean)
    uid = Column(Uuid)

class SearchProfile(Base):
    __tablename__ = f"search_profiles_{uuid_pkg.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey(f"{SearchUser.__tablename__}.id"))
    bio = Column(String)
    user = relationship("SearchUser")
    posts = relationship("SearchPost", back_populates="profile")

class SearchPost(Base):
    __tablename__ = f"search_posts_{uuid_pkg.uuid4().hex[:6]}"
    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey(f"{SearchProfile.__tablename__}.id"))
    title = Column(String)
    profile = relationship("SearchProfile", back_populates="posts")

class SearchComment(Base):
    __tablename__ = f"search_comments_{uuid_pkg.uuid4().hex[:6]}"
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
    user1 = SearchUser(
        id=1,
        username="admin%",
        password="foo",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
        is_active=True,
        uid=uuid_pkg.UUID("11111111-1111-1111-1111-111111111111")
    )
    user2 = SearchUser(
        id=2,
        username="user_special",
        password="bar",
        created_at=datetime(2026, 1, 2, 12, 0, 0),
        is_active=False,
        uid=uuid_pkg.UUID("22222222-2222-2222-2222-222222222222")
    )
    user3 = SearchUser(
        id=3,
        username="guest",
        password="baz",
        created_at=datetime(2026, 1, 3, 12, 0, 0),
        is_active=True,
        uid=uuid_pkg.UUID("33333333-3333-3333-3333-333333333333")
    )
    db_session.add_all([user1, user2, user3])

    profile1 = SearchProfile(id=1, user_id=1, bio="admin bio")
    profile2 = SearchProfile(id=2, user_id=2, bio=None)
    profile3 = SearchProfile(id=3, user_id=3, bio="guest bio")
    db_session.add_all([profile1, profile2, profile3])

    post1 = SearchPost(id=1, profile_id=1, title="Hello admin")
    post2 = SearchPost(id=2, profile_id=3, title="Hello guest")
    db_session.add_all([post1, post2])

    comment1 = SearchComment(id=1, post_id=1, body="nice comment")
    db_session.add_all([comment1])

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
        (["post.profile.non_existent"], ValidationError, "Invalid include relation path"),
        (["post.profile.user.extra"], ValidationError, "exceeds the maximum limit of 3"),
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

@pytest.mark.anyio
async def test_search_lt_le_ge(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    
    req_lt = SearchRequest(filters=[FilterItem(field="id", op="lt", value=2)])
    res_lt = await db_session.execute(engine.build_base_query(req_lt))
    assert {u.id for u in res_lt.scalars().all()} == {1}
    
    req_le = SearchRequest(filters=[FilterItem(field="id", op="le", value=2)])
    res_le = await db_session.execute(engine.build_base_query(req_le))
    assert {u.id for u in res_le.scalars().all()} == {1, 2}
    
    req_ge = SearchRequest(filters=[FilterItem(field="id", op="ge", value=2)])
    res_ge = await db_session.execute(engine.build_base_query(req_ge))
    assert {u.id for u in res_ge.scalars().all()} == {2, 3}

@pytest.mark.anyio
async def test_search_in_operator(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    req = SearchRequest(filters=[FilterItem(field="id", op="in", value=[1, 3])])
    res = await db_session.execute(engine.build_base_query(req))
    assert {u.id for u in res.scalars().all()} == {1, 3}

@pytest.mark.anyio
async def test_search_is_null_operator(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchProfile)
    
    req_null = SearchRequest(filters=[FilterItem(field="bio", op="is_null", value=True)])
    res_null = await db_session.execute(engine.build_base_query(req_null))
    assert {p.id for p in res_null.scalars().all()} == {2}
    
    req_not_null = SearchRequest(filters=[FilterItem(field="bio", op="is_null", value=False)])
    res_not_null = await db_session.execute(engine.build_base_query(req_not_null))
    assert {p.id for p in res_not_null.scalars().all()} == {1, 3}

@pytest.mark.anyio
async def test_search_wildcard_escaping(db_session: Any, seed_data: None) -> None:
    user4 = SearchUser(
        id=4,
        username="user1special",
        password="qux",
        created_at=datetime(2026, 1, 4, 12, 0, 0),
        is_active=True,
        uid=uuid_pkg.UUID("44444444-4444-4444-4444-444444444444")
    )
    db_session.add(user4)
    await db_session.flush()

    engine = SearchEngine(SearchUser)
    
    req_percent = SearchRequest(filters=[FilterItem(field="username", op="ilike", value="admin%")])
    res_percent = await db_session.execute(engine.build_base_query(req_percent))
    assert {u.id for u in res_percent.scalars().all()} == {1}
    
    req_underscore = SearchRequest(filters=[FilterItem(field="username", op="ilike", value="user_special")])
    res_underscore = await db_session.execute(engine.build_base_query(req_underscore))
    results = list(res_underscore.scalars().all())
    assert {u.id for u in results} == {2}

@pytest.mark.anyio
async def test_search_type_coercion_date_time(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    req = SearchRequest(filters=[FilterItem(field="created_at", op="eq", value="2026-01-02T12:00:00")])
    res = await db_session.execute(engine.build_base_query(req))
    assert {u.id for u in res.scalars().all()} == {2}

@pytest.mark.anyio
async def test_search_type_coercion_uuid(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    req = SearchRequest(filters=[FilterItem(field="uid", op="eq", value="33333333-3333-3333-3333-333333333333")])
    res = await db_session.execute(engine.build_base_query(req))
    assert {u.id for u in res.scalars().all()} == {3}

@pytest.mark.anyio
async def test_search_type_coercion_bool(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    req_true = SearchRequest(filters=[FilterItem(field="is_active", op="eq", value="true")])
    res_true = await db_session.execute(engine.build_base_query(req_true))
    assert {u.id for u in res_true.scalars().all()} == {1, 3}

@pytest.mark.anyio
async def test_search_relation_one_to_one(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchProfile)
    req = SearchRequest(filters=[FilterItem(field="user.username", op="eq", value="guest")])
    res = await db_session.execute(engine.build_base_query(req))
    assert {p.id for p in res.scalars().all()} == {3}

@pytest.mark.anyio
async def test_search_relation_one_to_many_any(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchProfile)
    req = SearchRequest(filters=[FilterItem(field="posts.title", op="eq", value="Hello guest")])
    res = await db_session.execute(engine.build_base_query(req))
    assert {p.id for p in res.scalars().all()} == {3}

@pytest.mark.anyio
async def test_search_custom_handler(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    engine.register_handler("username", lambda val: SearchUser.username == "guest")
    req = SearchRequest(filters=[FilterItem(field="username", op="eq", value="anything")])
    res = await db_session.execute(engine.build_base_query(req))
    assert {u.id for u in res.scalars().all()} == {3}

def test_search_nested_restricted_field() -> None:
    from zcore.context.context import ctx
    ctx.restricted_fields = {"post.profile.user.password"}
    engine = SearchEngine(SearchComment)
    req = SearchRequest(filters=[FilterItem(field="post.profile.user.password", op="eq", value="foo")])
    with pytest.raises(ForbiddenError) as exc_info:
        engine.build_base_query(req)
    assert "restricted" in str(exc_info.value).lower()

def test_search_restricted_relationship_path() -> None:
    from zcore.context.context import ctx
    ctx.restricted_fields = {"SearchComment.post", "post"}
    engine = SearchEngine(SearchComment)
    req = SearchRequest(filters=[FilterItem(field="post.title", op="eq", value="anything")])
    with pytest.raises(ForbiddenError) as exc_info:
        engine.build_base_query(req)
    assert "restricted" in str(exc_info.value).lower()

def test_search_invalid_sort_fields() -> None:
    engine = SearchEngine(SearchUser)
    req = SearchRequest(sort=[SortItem(field="non_existent_field", order="asc")])
    with pytest.raises(ValidationError) as exc_info:
        engine.build_base_query(req)
    assert "invalid sort field" in str(exc_info.value).lower()

@pytest.mark.anyio
async def test_search_pagination_build_query(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    req = SearchRequest(size=2, page=2)
    query = engine.build_query(req)
    res = await db_session.execute(query)
    items = list(res.scalars().all())
    assert len(items) == 1

@pytest.mark.anyio
async def test_search_empty_request(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    req = SearchRequest()
    query = engine.build_base_query(req)
    res = await db_session.execute(query)
    items = list(res.scalars().all())
    assert len(items) == 3

@pytest.mark.anyio
async def test_search_complex_combined_logical_filters(db_session: Any, seed_data: None) -> None:
    engine = SearchEngine(SearchUser)
    filter_item = FilterItem(
        op="or",
        items=[
            FilterItem(field="id", op="eq", value=1),
            FilterItem(
                op="and",
                items=[
                    FilterItem(field="id", op="eq", value=3),
                    FilterItem(field="username", op="eq", value="guest")
                ]
            )
        ]
    )
    req = SearchRequest(filters=[filter_item])
    res = await db_session.execute(engine.build_base_query(req))
    assert {u.id for u in res.scalars().all()} == {1, 3}