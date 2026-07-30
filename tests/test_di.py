import asyncio
import uuid
import pytest

from contextlib import contextmanager
from typing import Any, Generator, Type, get_origin, get_args, Annotated
from abc import ABC
from fastapi.params import Depends as DependsClass

from zcore.kernel.di import (
    container,
    _current_scope_id,
    _scoped_instances,
    CircularDependencyError,
    DIException,
    Injector,
    Inject,
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
def test_resolve_singleton(interface: Type[Any], implementation: Type[Any]) -> None:
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
    interface: Type[Any],
    implementation: Type[Any],
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
def test_resolve_transient(interface: Type[Any], implementation: Type[Any]) -> None:
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
def test_circular_dependency(registrations: dict[str, Type[Any]]) -> None:
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
def test_forward_reference_resolution(target: Type[Any], dep: Type[Any]) -> None:
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
    for task_id, instance in results:
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