import uuid
import inspect
import asyncio
import pytest

from typing import Any
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi import FastAPI, APIRouter
from httpx import ASGITransport, AsyncClient
from unittest.mock import MagicMock, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession

from zcore.kernel.di import container, _current_scope_id
from zcore.web.base_router import BaseRouter, RouteKey
from zcore.web.middleware import RequestLogMiddleware, ScopedDependencyMiddleware
from zcore.web.projection import Zchema
from zcore.web.api_router import ZCoreRequest
from zcore.web.streams import StreamManager, init_stream_redis
from zcore.context.context import ZContext
from zcore.exceptions.base import EntityNotFound, AppException
from zcore.exceptions.handlers import app_exception_handler
from zcore.db.setup import db_manager
from zcore.db.pagination import PageNumberPagination, PaginatedResult, PageNumberParams

class DummyModel:
    __tablename__ = "dummy"

    @classmethod
    def actions(cls) -> Any:
        mock_actions = MagicMock()
        mock_actions.CREATE = "dummy:create"
        mock_actions.VIEW = "dummy:view"
        mock_actions.LISTVIEW = "dummy:listview"
        mock_actions.UPDATE = "dummy:update"
        mock_actions.DELETE = "dummy:delete"
        return mock_actions

class DummyCreate(BaseModel):
    name: str

class DummyUpdate(BaseModel):
    name: str

class DummyOut(Zchema):
    __model__ = "dummy"
    id: str
    name: str
    password: str = ""

class NestedProfile(Zchema):
    __model__ = "profile"
    phone: str
    city: str

class DummyOutWithNested(Zchema):
    __model__ = "dummy"
    id: str
    name: str
    profile: NestedProfile

class TargetService:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def get(self, id: uuid.UUID) -> DummyOut:
        return DummyOut(id=str(id), name=self.payload["name"], password=self.payload["password"])

    async def get_list(self, pagination: Any = None) -> list[Any]:
        return []

class MockFullService:
    def __init__(self) -> None:
        pass

    async def create(self, schema: DummyCreate) -> DummyOut:
        return DummyOut(id="123", name=schema.name, password="safe")

    async def get(self, id: uuid.UUID) -> DummyOut:
        return DummyOut(id=str(id), name="retrieved", password="safe")

    async def get_list(self, pagination: Any = None) -> Any:
        data = [DummyOut(id="1", name="n1", password="s1")]
        if pagination and isinstance(pagination, PageNumberParams):
            return PaginatedResult(data=data, meta={"total": 1, "page": pagination.page})
        return data

    async def search(self, search_in: Any, pagination: Any = None) -> Any:
        return [DummyOut(id="1", name="search_res", password="s1")]

    async def update(self, id: uuid.UUID, schema: DummyUpdate, partial: bool = False) -> DummyOut:
        name = schema.name if schema.name else "patched"
        return DummyOut(id=str(id), name=name, password="safe")

    async def delete(self, id: uuid.UUID) -> None:
        pass

def clean_endpoint_signature(endpoint):
    sig = inspect.signature(endpoint)
    parameters = list(sig.parameters.values())
    new_params = [p for p in parameters if p.kind != inspect.Parameter.VAR_KEYWORD]
    globals_dict = {"_orig_endpoint": endpoint}
    param_strs = []
    call_strs = []
    for i, p in enumerate(new_params):
        name = p.name
        if p.default is not inspect.Parameter.empty:
            globals_dict[f"_default_{i}"] = p.default
            param_strs.append(f"{name}=_default_{i}")
        else:
            param_strs.append(name)
        call_strs.append(f"{name}={name}")
    param_line = ", ".join(param_strs)
    call_line = ", ".join(call_strs)
    func_code = f"async def clean_endpoint({param_line}):\n    return await _orig_endpoint({call_line})"
    local_dict = {}
    exec(func_code, globals_dict, local_dict)
    clean_func = local_dict["clean_endpoint"]
    clean_func.__annotations__ = endpoint.__annotations__
    return clean_func

@pytest.mark.parametrize(
    "router_attrs, expected_error_msg",
    [
        (
            {"service": None},
            "Service class must be defined"
        ),
        (
            {"service": MagicMock(), "create_schema": None, "exclude": set()},
            "POST route is enabled"
        ),
        (
            {"service": MagicMock(), "create_schema": DummyCreate, "update_schema": None, "exclude": set()},
            "UPDATE/PATCH route is enabled"
        ),
        (
            {
                "service": MagicMock(),
                "create_schema": DummyCreate,
                "update_schema": DummyUpdate,
                "model": None,
            },
            "Model class must be defined"
        ),
    ]
)
def test_router_auto_scaffolding_validation_errors(router_attrs: dict[str, Any], expected_error_msg: str) -> None:
    attrs = {
        "model": DummyModel,
        "create_schema": DummyCreate,
        "update_schema": DummyUpdate,
        "schema_out": DummyOut,
    }
    attrs.update(router_attrs)
    router_cls = type("TestRouter", (BaseRouter,), attrs)
    with pytest.raises(ValueError) as exc_info:
        router_cls()
    assert expected_error_msg in str(exc_info.value)

@pytest.mark.anyio
@pytest.mark.parametrize(
    "restricted_fields, payload_in, expected_payload_out, expected_vary",
    [
        (
            {"dummy.password", "resource.dummy.password"},
            {"id": "12345678-1234-5678-1234-567812345678", "name": "UserA", "password": "hash"},
            {"id": "12345678-1234-5678-1234-567812345678", "name": "UserA"},
            ["Authorization", "Cookie"]
        ),
        (
            set(),
            {"id": "12345678-1234-5678-1234-567812345678", "name": "UserA", "password": "hash"},
            {"id": "12345678-1234-5678-1234-567812345678", "name": "UserA", "password": "hash"},
            []
        )
    ]
)
async def test_router_schema_projection_pruning(
    monkeypatch: pytest.MonkeyPatch,
    restricted_fields: set[str],
    payload_in: dict[str, Any],
    expected_payload_out: dict[str, Any],
    expected_vary: list[str]
) -> None:
    original_add_api_route = APIRouter.add_api_route
    def patched_add_api_route(self, path, endpoint, *args, **kwargs):
        clean_endpoint = clean_endpoint_signature(endpoint)
        return original_add_api_route(self, path, clean_endpoint, *args, **kwargs)
    monkeypatch.setattr(APIRouter, "add_api_route", patched_add_api_route)
    monkeypatch.setattr(ZContext, "restricted_fields", property(lambda self: frozenset(restricted_fields)))
    app = FastAPI()
    mock_service = TargetService(payload_in)
    container.register_singleton(TargetService, mock_service)
    class TargetRouter(BaseRouter[DummyCreate, DummyUpdate]):
        model = DummyModel
        create_schema = DummyCreate
        update_schema = DummyUpdate
        schema_out = DummyOut
        service = TargetService
        prefix = "/items"
        expose_schemas = True
        def get_route_dependencies(self, route_key: RouteKey, action: str) -> list[Any]:
            return []
    router_inst = TargetRouter()
    app.include_router(router_inst.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/items/12345678-1234-5678-1234-567812345678")
        assert response.status_code == 200
        json_data = response.json()
        assert json_data["data"] == expected_payload_out
        vary_header = response.headers.get("vary", "")
        for header in expected_vary:
            assert header.lower() in vary_header.lower()
        if expected_vary:
            schema_resp = await client.get("/items/?schema=true")
            assert schema_resp.status_code == 200
            schema_data = schema_resp.json()
            assert "password" not in schema_data["data"]["properties"]

@pytest.mark.anyio
@pytest.mark.parametrize(
    "custom_request_id, expect_valid_uuid",
    [
        ("my-custom-request-id-123", False),
        (None, True),
    ]
)
async def test_request_id_middleware(custom_request_id: str | None, expect_valid_uuid: bool) -> None:
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    @app.get("/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {}
        if custom_request_id:
            headers["x-request-id"] = custom_request_id
        response = await client.get("/health", headers=headers)
        assert response.status_code == 200
        response_id = response.headers.get("x-request-id")
        assert response_id is not None
        if expect_valid_uuid:
            assert uuid.UUID(response_id)
        else:
            assert response_id == custom_request_id

@pytest.mark.anyio
async def test_router_full_crud_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    original_add_api_route = APIRouter.add_api_route
    def patched_add_api_route(self, path, endpoint, *args, **kwargs):
        return original_add_api_route(self, path, clean_endpoint_signature(endpoint), *args, **kwargs)
    monkeypatch.setattr(APIRouter, "add_api_route", patched_add_api_route)
    app = FastAPI()
    service_inst = MockFullService()
    container.register_singleton(MockFullService, service_inst)
    class FullCrudRouter(BaseRouter[DummyCreate, DummyUpdate]):
        model = DummyModel
        create_schema = DummyCreate
        update_schema = DummyUpdate
        schema_out = DummyOut
        service = MockFullService
        prefix = "/crud"
        pagination_class = PageNumberPagination
        def get_route_dependencies(self, route_key: RouteKey, action: str) -> list[Any]:
            return []
    r = FullCrudRouter()
    app.include_router(r.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/crud/", json={"name": "new_item"})
        assert res.status_code == 201
        assert res.json()["data"]["name"] == "new_item"
        res = await client.get("/crud/12345678-1234-5678-1234-567812345678")
        assert res.status_code == 200
        assert res.json()["data"]["name"] == "retrieved"
        res = await client.get("/crud/?page=2&size=10")
        assert res.status_code == 200
        assert res.json()["data"][0]["name"] == "n1"
        assert res.json()["meta"]["page"] == 2
        res = await client.post("/crud/search", json={"filters": [], "size": 10})
        assert res.status_code == 200
        assert res.json()["data"][0]["name"] == "search_res"
        res = await client.patch("/crud/12345678-1234-5678-1234-567812345678", json={"name": "patched_item"})
        assert res.status_code == 200
        assert res.json()["data"]["name"] == "patched_item"
        res = await client.delete("/crud/12345678-1234-5678-1234-567812345678")
        assert res.status_code == 200
        assert res.json()["message"] == "Deleted successfully"

@pytest.mark.anyio
async def test_router_exception_translation_handler() -> None:
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    @app.get("/error")
    async def raise_error():
        raise EntityNotFound(message="Missing item", payload={"key": "val"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/error")
        assert res.status_code == 404
        body = res.json()
        assert body["success"] is False
        assert body["message"] == "Missing item"
        assert body["meta"]["error_type"] == "EntityNotFound"

@pytest.mark.anyio
async def test_zchema_recursive_nested_pruning(monkeypatch: pytest.MonkeyPatch) -> None:
    nested = NestedProfile(phone="12345", city="Tehran")
    model = DummyOutWithNested(id="1", name="A", profile=nested)
    monkeypatch.setattr(ZContext, "restricted_fields", property(lambda self: frozenset({"dummy.profile.phone"})))
    serialized = model.model_dump(mode="json")
    assert "phone" not in serialized["profile"]
    assert serialized["profile"]["city"] == "Tehran"

@pytest.mark.anyio
async def test_zchema_wildcard_pruning(monkeypatch: pytest.MonkeyPatch) -> None:
    nested = NestedProfile(phone="12345", city="Tehran")
    model = DummyOutWithNested(id="1", name="A", profile=nested)
    monkeypatch.setattr(ZContext, "restricted_fields", property(lambda self: frozenset({"dummy.*"})))
    serialized = model.model_dump(mode="json")
    assert serialized == {}

@pytest.mark.anyio
async def test_method_schema_exposure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ZContext, "restricted_fields", property(lambda self: frozenset({"dummy.password"})))
    app = FastAPI()
    class ExposureRouter(BaseRouter[DummyCreate, DummyUpdate]):
        model = DummyModel
        create_schema = DummyCreate
        update_schema = DummyUpdate
        schema_out = DummyOut
        service = MockFullService
        prefix = "/exposure"
        expose_schemas = True
        def get_route_dependencies(self, route_key: RouteKey, action: str) -> list[Any]:
            return []
    r = ExposureRouter()
    app.include_router(r.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res_post = await client.post("/exposure/?schema=true")
        assert res_post.status_code == 200
        assert "name" in res_post.json()["data"]["properties"]
        res_get = await client.get("/exposure/12345678-1234-5678-1234-567812345678?schema=true")
        assert res_get.status_code == 200
        assert "password" not in res_get.json()["data"]["properties"]

@pytest.mark.anyio
@pytest.mark.parametrize(
    "bad_id",
    [
        "short",
        "a" * 70,
        "inject<script>",
        "bad_char$",
    ]
)
async def test_request_id_validation_pattern(bad_id: str) -> None:
    app = FastAPI()
    app.add_middleware(RequestLogMiddleware)
    @app.get("/")
    def root():
        return {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/", headers={"x-request-id": bad_id})
        res_id = res.headers.get("x-request-id")
        assert res_id != bad_id
        assert uuid.UUID(res_id)

@pytest.mark.anyio
async def test_scoped_dependency_middleware_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.add_middleware(ScopedDependencyMiddleware)
    mock_session = AsyncMock(spec=AsyncSession)
    @asynccontextmanager
    async def mock_sess_manager():
        yield mock_session
    monkeypatch.setattr(db_manager, "session", mock_sess_manager)
    @app.get("/scoped")
    async def check_scope() -> dict[str, Any]:
        scope_id = _current_scope_id.get()
        registered_session = container.resolve(AsyncSession)
        return {"scope_id": scope_id, "session_is_mock": registered_session is mock_session}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/scoped")
        assert res.status_code == 200
        data = res.json()
        assert data["scope_id"] is not None
        assert data["session_is_mock"] is True
    assert _current_scope_id.get() is None
    with pytest.raises(Exception):
        container.resolve(AsyncSession)

@pytest.mark.anyio
async def test_zcore_request_body_caching() -> None:
    scope = {"type": "http", "method": "POST", "path": "/"}
    async def mock_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"test_payload", "more_body": False}
    req = ZCoreRequest(scope, mock_receive)
    body1 = await req.body()
    body2 = await req.body()
    assert body1 == b"test_payload"
    assert body2 == b"test_payload"

def test_route_sorting_shadowing() -> None:
    class DummyService:
        pass
    class SortRouter(BaseRouter[DummyCreate, DummyUpdate]):
        model = DummyModel
        create_schema = DummyCreate
        update_schema = DummyUpdate
        schema_out = DummyOut
        service = DummyService
        prefix = "/test"
        exclude = {RouteKey.POST, RouteKey.GET_ALL, RouteKey.UPDATE, RouteKey.PATCH, RouteKey.DELETE}
    router_inst = SortRouter()
    paths = [r.path for r in router_inst.router.routes]
    assert "/test/search" in paths
    assert "/test/{id:uuid}" in paths
    idx_search = paths.index("/test/search")
    idx_uuid = paths.index("/test/{id:uuid}")
    assert idx_search < idx_uuid

@pytest.mark.anyio
async def test_stream_manager_pubsub_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    redis_mock = AsyncMock()
    pubsub_mock = AsyncMock()
    redis_mock.pubsub = MagicMock(return_value=pubsub_mock)
    async def mock_listen():
        while True:
            await asyncio.sleep(3600)
            yield {"type": "pmessage", "channel": "stream:user:123", "data": "{}"}
    pubsub_mock.listen = mock_listen
    init_stream_redis(redis_mock)
    sm = StreamManager()
    u_id = uuid.uuid4()
    queue = await sm.subscribe(u_id)
    assert u_id in sm.users_queues
    assert sm._pubsub_task is not None
    await sm.publish(u_id, {"msg": "hello"})
    redis_mock.publish.assert_called_once()
    await sm._local_publish(u_id, {"msg": "local"})
    msg = queue.get_nowait()
    assert msg == {"msg": "local"}
    for i in range(105):
        try:
            queue.put_nowait({"msg": f"flood_{i}"})
        except asyncio.QueueFull:
            pass
    await sm._local_publish(u_id, {"msg": "overflow"})
    assert u_id not in sm.users_queues
    await sm.unsubscribe(u_id, queue)
    assert sm._pubsub_task is None
    init_stream_redis(None)