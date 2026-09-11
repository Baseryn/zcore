import asyncio
import uuid
from abc import ABC
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated, Any, get_args, get_origin
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.params import Depends as DependsClass
from sqlalchemy.ext.asyncio import AsyncSession

from zcore.context.context import _request_context_store
from zcore.db.setup import db_manager
from zcore.kernel.di import (
    CircularDependencyError,
    DIException,
    Inject,
    Injector,
    _current_scope_id,
    _scoped_instances,
    background_scope,
    background_task,
    container,
)


class IService:
    pass


class ServiceImpl(IService):
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class AnotherServiceImpl(IService):
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class ForwardA:
    def __init__(self, b: "ForwardB") -> None:
        self.b = b


class ForwardB:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class CircA:
    def __init__(self, b: "CircB") -> None:
        self.b = b


class CircB:
    def __init__(self, a: "CircA") -> None:
        self.a = a


class AbstractService(ABC):
    pass


class ConcreteService(AbstractService):
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class AutoZ:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class AutoY:
    def __init__(self, z: AutoZ) -> None:
        self.z = z


class AutoX:
    def __init__(self, y: AutoY) -> None:
        self.y = y


class SimpleNoInit:
    pass


class DepWithAnnotated:
    def __init__(self, dep: Annotated[ForwardB, "meta"]) -> None:
        self.dep = dep


class DeepA:
    def __init__(self, b: "DeepB") -> None:
        self.b = b


class DeepB:
    def __init__(self, c: "DeepC") -> None:
        self.c = c


class DeepC:
    def __init__(self, a: "DeepA") -> None:
        self.a = a


class BackgroundTaskWorker:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


class SecondaryWorker:
    def __init__(self) -> None:
        self.id = uuid.uuid4()


@contextmanager
def di_scope(scope_id: str) -> Generator[None, None, None]:
    token_id = _current_scope_id.set(scope_id)
    token_instances = _scoped_instances.set({})
    try:
        yield
    finally:
        _current_scope_id.reset(token_id)
        _scoped_instances.reset(token_instances)


@pytest.fixture(autouse=True)
def reset_container() -> None:
    container._singletons.clear()
    container._scoped_definitions.clear()
    container._factories.clear()
    container._constructor_cache.clear()
    container._dependency_signature_cache.clear()


@pytest.mark.parametrize(
    "interface, implementation",
    [
        (IService, ServiceImpl),
        (ForwardB, ForwardB),
    ]
)
def test_resolve_singleton(interface: type[Any], implementation: type[Any]) -> None:
    instance = implementation()
    container.register_singleton(interface, instance)

    res1 = container.resolve(interface)
    res2 = container.resolve(interface)

    assert res1 is instance
    assert res2 is instance
    assert res1.id == res2.id


@pytest.mark.parametrize(
    "interface, implementation, scope_1, scope_2",
    [
        (IService, ServiceImpl, "scope-a", "scope-b"),
        (ForwardB, ForwardB, "scope-x", "scope-y"),
    ]
)
def test_resolve_scoped(
    interface: type[Any],
    implementation: type[Any],
    scope_1: str,
    scope_2: str
) -> None:
    container.register_scoped(interface, implementation)

    with di_scope(scope_1):
        res1 = container.resolve(interface)
        res2 = container.resolve(interface)
        assert res1 is res2

    with di_scope(scope_2):
        res3 = container.resolve(interface)
        assert res3 is not res1


@pytest.mark.parametrize(
    "interface, implementation",
    [
        (IService, ServiceImpl),
        (ForwardB, ForwardB),
    ]
)
def test_resolve_transient(interface: type[Any], implementation: type[Any]) -> None:
    container.register_transient(interface, implementation)

    res1 = container.resolve(interface)
    res2 = container.resolve(interface)

    assert res1 is not res2
    assert isinstance(res1, implementation)
    assert isinstance(res2, implementation)


@pytest.mark.parametrize(
    "registrations",
    [
        {"A": CircA, "B": CircB},
    ]
)
def test_circular_dependency(registrations: dict[str, type[Any]]) -> None:
    container.register_transient(CircA, registrations["A"])
    container.register_transient(CircB, registrations["B"])

    with pytest.raises(CircularDependencyError) as exc_info:
        container.resolve(CircA)

    assert "Circular dependency detected" in str(exc_info.value)
    assert "CircA" in str(exc_info.value)
    assert "CircB" in str(exc_info.value)


@pytest.mark.parametrize(
    "target, dep",
    [
        (ForwardA, ForwardB),
    ]
)
def test_forward_reference_resolution(target: type[Any], dep: type[Any]) -> None:
    container.register_transient(dep, dep)
    container.register_transient(target, target)

    resolved = container.resolve(target)
    assert isinstance(resolved, target)
    assert isinstance(resolved.b, dep)


@pytest.mark.anyio
@pytest.mark.parametrize("num_tasks", [10, 50])
async def test_di_concurrency_isolation(num_tasks: int) -> None:
    container.register_scoped(IService, ServiceImpl)

    async def run_task(task_id: str) -> tuple[str, Any]:
        with di_scope(task_id):
            res1 = container.resolve(IService)
            await asyncio.sleep(0.001)
            res2 = container.resolve(IService)
            assert res1 is res2
            return task_id, res1

    tasks = [run_task(f"scope-{uuid.uuid4()}") for _ in range(num_tasks)]
    results = await asyncio.gather(*tasks)

    seen_ids = set()
    for _task_id, instance in results:
        assert instance.id not in seen_ids
        seen_ids.add(instance.id)
    assert len(seen_ids) == num_tasks


def test_register_scoped_instance_success() -> None:
    instance = ServiceImpl()
    with di_scope("active_scope"):
        container.register_scoped_instance(IService, instance)
        resolved = container.resolve(IService)
        assert resolved is instance


def test_register_scoped_instance_outside_scope_error() -> None:
    instance = ServiceImpl()
    with pytest.raises(DIException) as exc_info:
        container.register_scoped_instance(IService, instance)
    assert "Cannot register scoped instance outside of an active scope" in str(exc_info.value)


def test_auto_wire_implicit_resolution() -> None:
    resolved = container.resolve(ForwardB)
    assert isinstance(resolved, ForwardB)


def test_auto_wire_recursive() -> None:
    resolved = container.resolve(AutoX)
    assert isinstance(resolved, AutoX)
    assert isinstance(resolved.y, AutoY)
    assert isinstance(resolved.y.z, AutoZ)


def test_constructor_and_signature_cache() -> None:
    assert ForwardA not in container._constructor_cache
    assert ForwardA not in container._dependency_signature_cache
    container.register_transient(ForwardB, ForwardB)
    container.register_transient(ForwardA, ForwardA)
    container.resolve(ForwardA)
    assert ForwardA in container._constructor_cache
    assert ForwardA in container._dependency_signature_cache
    assert container._dependency_signature_cache[ForwardA] == [ForwardB]


@pytest.mark.anyio
async def test_injector_callable_helper() -> None:
    container.register_transient(IService, ServiceImpl)
    injector_instance = Injector(IService)
    resolved = await injector_instance()
    assert isinstance(resolved, ServiceImpl)


def test_inject_syntactic_sugar() -> None:
    injected = Inject[IService]
    assert get_origin(injected) is Annotated
    args = get_args(injected)
    assert args[0] is IService
    depends_dep = args[1]
    assert isinstance(depends_dep, DependsClass)
    assert isinstance(depends_dep.dependency, Injector)
    assert depends_dep.dependency.interface is IService


def test_interface_to_implementation_binding() -> None:
    container.register_transient(AbstractService, ConcreteService)
    resolved = container.resolve(AbstractService)
    assert isinstance(resolved, ConcreteService)


def test_class_no_init_or_object_init() -> None:
    resolved = container.resolve(SimpleNoInit)
    assert isinstance(resolved, SimpleNoInit)
    assert SimpleNoInit in container._dependency_signature_cache
    assert container._dependency_signature_cache[SimpleNoInit] == []


def test_constructor_annotated_parameters() -> None:
    container.register_transient(ForwardB, ForwardB)
    container.register_transient(DepWithAnnotated, DepWithAnnotated)
    resolved = container.resolve(DepWithAnnotated)
    assert isinstance(resolved, DepWithAnnotated)
    assert isinstance(resolved.dep, ForwardB)


def test_clear_scope_explicit() -> None:
    container.register_scoped(IService, ServiceImpl)
    scope_id = "test_clear"
    with di_scope(scope_id):
        res1 = container.resolve(IService)
        res2 = container.resolve(IService)
        assert res1 is res2
        container.clear_scope(scope_id)
        res3 = container.resolve(IService)
        assert res3 is not res1


def test_deep_circular_dependency() -> None:
    container.register_transient(DeepA, DeepA)
    container.register_transient(DeepB, DeepB)
    container.register_transient(DeepC, DeepC)
    with pytest.raises(CircularDependencyError) as exc_info:
        container.resolve(DeepA)
    assert "Circular dependency detected" in str(exc_info.value)
    assert "DeepA" in str(exc_info.value)
    assert "DeepB" in str(exc_info.value)
    assert "DeepC" in str(exc_info.value)


@pytest.mark.anyio
async def test_background_scope_lifecycle_and_isolation() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    parent_ctx_token = _request_context_store.set({"user_id": "parent-user", "role": "admin"})

    try:
        assert _current_scope_id.get() is None

        async with background_scope(inherit_context=True, custom_flag=True):
            assert _current_scope_id.get() is not None
            session = container.resolve(AsyncSession)
            assert isinstance(session, AsyncSession)

            current_store = _request_context_store.get()
            assert current_store.get("user_id") == "parent-user"
            assert current_store.get("custom_flag") is True

        assert _current_scope_id.get() is None
        assert _request_context_store.get().get("custom_flag") is None
        assert _request_context_store.get().get("user_id") == "parent-user"
    finally:
        _request_context_store.reset(parent_ctx_token)


@pytest.mark.anyio
async def test_background_scope_no_inherit_context() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    parent_ctx_token = _request_context_store.set({"user_id": "isolated-user", "tenant": "corp"})

    try:
        async with background_scope(inherit_context=False, task_only_key="task-value"):
            current_store = _request_context_store.get()
            assert current_store.get("user_id") is None
            assert current_store.get("tenant") is None
            assert current_store.get("task_only_key") == "task-value"

        assert _request_context_store.get().get("user_id") == "isolated-user"
        assert _request_context_store.get().get("tenant") == "corp"
    finally:
        _request_context_store.reset(parent_ctx_token)


@pytest.mark.anyio
async def test_background_scope_exception_cleanup_guarantee() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    token = _request_context_store.set({"initial": "state"})

    try:
        with pytest.raises(ValueError, match="Scope breakdown"):
            async with background_scope(inherit_context=True, err_state=True):
                assert _current_scope_id.get() is not None
                raise ValueError("Scope breakdown")

        assert _current_scope_id.get() is None
        assert _request_context_store.get() == {"initial": "state"}
    finally:
        _request_context_store.reset(token)


@pytest.mark.anyio
async def test_background_task_decorator_async_execution() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    container.register_scoped(BackgroundTaskWorker, BackgroundTaskWorker)

    @background_task
    async def sample_async_job(target_name: str, worker: BackgroundTaskWorker) -> dict[str, Any]:
        session = container.resolve(AsyncSession)
        assert isinstance(session, AsyncSession)
        return {"name": target_name, "worker_id": worker.id}

    result = await sample_async_job("task-alpha")
    assert result["name"] == "task-alpha"
    assert isinstance(result["worker_id"], uuid.UUID)


@pytest.mark.anyio
async def test_background_task_decorator_sync_execution() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    container.register_scoped(BackgroundTaskWorker, BackgroundTaskWorker)

    @background_task
    def sample_sync_job(value: int, worker: BackgroundTaskWorker) -> int:
        session = container.resolve(AsyncSession)
        assert isinstance(session, AsyncSession)
        assert isinstance(worker.id, uuid.UUID)
        return value * 2

    computed = await sample_sync_job(21)
    assert computed == 42


@pytest.mark.anyio
async def test_background_task_explicit_override_arguments() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    container.register_scoped(BackgroundTaskWorker, BackgroundTaskWorker)
    explicit_worker = BackgroundTaskWorker()

    @background_task
    async def override_job(worker: BackgroundTaskWorker) -> uuid.UUID:
        return worker.id

    result_id = await override_job(worker=explicit_worker)
    assert result_id == explicit_worker.id


@pytest.mark.anyio
async def test_background_task_multiple_injections() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    container.register_scoped(BackgroundTaskWorker, BackgroundTaskWorker)
    container.register_scoped(SecondaryWorker, SecondaryWorker)

    @background_task
    async def multi_dep_job(
        w1: BackgroundTaskWorker,
        w2: SecondaryWorker,
        tag: str = "default_tag"
    ) -> tuple[uuid.UUID, uuid.UUID, str]:
        return w1.id, w2.id, tag

    id1, id2, tag = await multi_dep_job()
    assert isinstance(id1, uuid.UUID)
    assert isinstance(id2, uuid.UUID)
    assert tag == "default_tag"


@pytest.mark.anyio
async def test_background_task_concurrent_isolation() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    container.register_scoped(BackgroundTaskWorker, BackgroundTaskWorker)

    @background_task
    async def concurrent_job(idx: int, worker: BackgroundTaskWorker) -> tuple[int, uuid.UUID, int]:
        session = container.resolve(AsyncSession)
        await asyncio.sleep(0.005)
        return idx, worker.id, id(session)

    tasks = [concurrent_job(i) for i in range(10)]
    results = await asyncio.gather(*tasks)

    worker_ids = {r[1] for r in results}
    session_memory_ids = {r[2] for r in results}

    assert len(worker_ids) == 10
    assert len(session_memory_ids) == 10


@pytest.mark.anyio
async def test_background_task_warns_when_async_session_passed() -> None:
    if not db_manager._engine:
        db_manager.init_app("sqlite+aiosqlite:///:memory:")

    mock_session = AsyncMock(spec=AsyncSession)

    @background_task
    async def async_job_with_session(session: AsyncSession, name: str) -> str:
        return name

    @background_task
    def sync_job_with_session(session: AsyncSession, count: int) -> int:
        return count

    with patch("zcore.kernel.di.logger.warning") as mock_warn:
        res_pos = await async_job_with_session(mock_session, "pos_call")
        assert res_pos == "pos_call"
        mock_warn.assert_called_once()
        assert "Passing an existing AsyncSession directly" in mock_warn.call_args[0][0]

    with patch("zcore.kernel.di.logger.warning") as mock_warn:
        res_kw = await async_job_with_session(session=mock_session, name="kw_call")
        assert res_kw == "kw_call"
        mock_warn.assert_called_once()
        assert "Passing an existing AsyncSession directly via argument 'session'" in mock_warn.call_args[0][0]

    with patch("zcore.kernel.di.logger.warning") as mock_warn:
        res_sync = await sync_job_with_session(mock_session, 100)
        assert res_sync == 100
        mock_warn.assert_called_once()
        assert "Passing an existing AsyncSession directly" in mock_warn.call_args[0][0]