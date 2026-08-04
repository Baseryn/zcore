import asyncio
import time
import uuid
from typing import Any
import pytest

from zcore.kernel.events import EventDispatcher, on_event

class DummyContainer:
    def __init__(self) -> None:
        self.registry = {}
        self.resolutions = 0

    def register(self, cls: Any, instance_factory: Any) -> None:
        self.registry[cls] = instance_factory

    def resolve(self, cls: Any) -> Any:
        self.resolutions += 1
        if cls in self.registry:
            return self.registry[cls]()
        return cls()

class SampleService:
    def __init__(self) -> None:
        self.called = False

    @on_event("order.completed")
    async def handle_order(self, order_id: int) -> str:
        self.called = True
        return f"handled_{order_id}"

    def normal_method(self) -> str:
        return "normal"

    @on_event("sync.event")
    def sync_method(self) -> str:
        return "sync_val"

@pytest.mark.anyio
@pytest.mark.parametrize("event_name", [f"evt_{uuid.uuid4().hex[:6]}" for _ in range(2)])
async def test_subscribe_and_unsubscribe(event_name: str) -> None:
    dispatcher = EventDispatcher()
    calls: list[str] = []

    def sync_handler() -> str:
        calls.append("sync")
        return "sync_val"

    async def async_handler() -> str:
        calls.append("async")
        return "async_val"

    dispatcher.subscribe(event_name, sync_handler)
    dispatcher.subscribe(event_name, async_handler)

    res = await dispatcher.dispatch(event_name)
    assert "sync" in calls
    assert "async" in calls
    assert set(res) == {"sync_val", "async_val"}

    calls.clear()
    dispatcher.unsubscribe(event_name, sync_handler)
    dispatcher.unsubscribe(event_name, async_handler)

    res2 = await dispatcher.dispatch(event_name)
    assert not calls
    assert res2 == []

@pytest.mark.anyio
@pytest.mark.parametrize("event_name", [f"perf_{uuid.uuid4().hex[:6]}"])
async def test_dispatch_sync_and_async(event_name: str) -> None:
    dispatcher = EventDispatcher()

    async def async_handler_1() -> float:
        await asyncio.sleep(0.05)
        return 1.0

    async def async_handler_2() -> float:
        await asyncio.sleep(0.05)
        return 2.0

    def sync_handler() -> float:
        return 3.0

    dispatcher.subscribe(event_name, async_handler_1)
    dispatcher.subscribe(event_name, async_handler_2)
    dispatcher.subscribe(event_name, sync_handler)

    start_time = time.perf_counter()
    results = await dispatcher.dispatch(event_name)
    elapsed = time.perf_counter() - start_time

    assert elapsed < 0.09
    assert set(results) == {1.0, 2.0, 3.0}

@pytest.mark.anyio
@pytest.mark.parametrize("event_name", [f"err_{uuid.uuid4().hex[:6]}"])
async def test_event_error_isolation(event_name: str) -> None:
    dispatcher = EventDispatcher()
    executed: list[str] = []

    def sync_error_handler() -> None:
        executed.append("sync_err")
        raise ValueError("Sync error")

    async def async_error_handler() -> None:
        executed.append("async_err")
        raise ValueError("Async error")

    def sync_ok_handler() -> str:
        executed.append("sync_ok")
        return "ok"

    async def async_ok_handler() -> str:
        executed.append("async_ok")
        return "async_ok_val"

    dispatcher.subscribe(event_name, sync_error_handler)
    dispatcher.subscribe(event_name, async_error_handler)
    dispatcher.subscribe(event_name, sync_ok_handler)
    dispatcher.subscribe(event_name, async_ok_handler)

    results = await dispatcher.dispatch(event_name)

    assert "sync_err" in executed
    assert "async_err" in executed
    assert "sync_ok" in executed
    assert "async_ok" in executed

    assert "ok" in results
    assert "async_ok_val" in results
    assert None in results

@pytest.mark.anyio
async def test_register_listeners_success() -> None:
    dispatcher = EventDispatcher()
    container = DummyContainer()
    instance = SampleService()
    container.register(SampleService, lambda: instance)

    dispatcher.register_listeners(SampleService, container)
    results = await dispatcher.dispatch("order.completed", 42)

    assert instance.called is True
    assert results == ["handled_42"]

@pytest.mark.anyio
async def test_register_listeners_lazy_resolution() -> None:
    dispatcher = EventDispatcher()
    container = DummyContainer()
    
    instances = []
    def factory() -> SampleService:
        inst = SampleService()
        instances.append(inst)
        return inst

    container.register(SampleService, factory)
    dispatcher.register_listeners(SampleService, container)

    await dispatcher.dispatch("order.completed", 1)
    await dispatcher.dispatch("order.completed", 2)

    assert container.resolutions == 2
    assert len(instances) == 2
    assert instances[0] is not instances[1]

@pytest.mark.anyio
async def test_register_listeners_ignores_non_decorated() -> None:
    dispatcher = EventDispatcher()
    container = DummyContainer()
    dispatcher.register_listeners(SampleService, container)
    
    results = await dispatcher.dispatch("normal_method")
    assert results == []

@pytest.mark.anyio
async def test_register_listeners_ignores_sync_decorated() -> None:
    dispatcher = EventDispatcher()
    container = DummyContainer()
    dispatcher.register_listeners(SampleService, container)

    results = await dispatcher.dispatch("sync.event")
    assert results == []

@pytest.mark.anyio
async def test_dispatch_arguments_propagation() -> None:
    dispatcher = EventDispatcher()
    received_args = []
    received_kwargs = []

    def sync_h(*args: Any, **kwargs: Any) -> str:
        received_args.append(args)
        received_kwargs.append(kwargs)
        return "sync"

    async def async_h(*args: Any, **kwargs: Any) -> str:
        received_args.append(args)
        received_kwargs.append(kwargs)
        return "async"

    dispatcher.subscribe("test.args", sync_h)
    dispatcher.subscribe("test.args", async_h)

    results = await dispatcher.dispatch("test.args", 1, "two", foo="bar")
    assert set(results) == {"sync", "async"}
    assert len(received_args) == 2
    assert len(received_kwargs) == 2
    for args in received_args:
        assert args == (1, "two")
    for kwargs in received_kwargs:
        assert kwargs == {"foo": "bar"}

@pytest.mark.anyio
async def test_dispatch_unregistered_event() -> None:
    dispatcher = EventDispatcher()
    results = await dispatcher.dispatch("missing.event")
    assert results == []

@pytest.mark.anyio
async def test_unsubscribe_robustness() -> None:
    dispatcher = EventDispatcher()
    def dummy() -> None:
        pass
    dispatcher.unsubscribe("missing.event", dummy)
    
    dispatcher.subscribe("exists", dummy)
    dispatcher.unsubscribe("exists", lambda: None)
    
    results = await dispatcher.dispatch("exists")
    assert results == [None]

@pytest.mark.anyio
async def test_double_subscription() -> None:
    dispatcher = EventDispatcher()
    calls = 0
    def dummy() -> int:
        nonlocal calls
        calls += 1
        return calls
    
    dispatcher.subscribe("event", dummy)
    dispatcher.subscribe("event", dummy)
    
    results = await dispatcher.dispatch("event")
    assert calls == 2
    assert set(results) == {1, 2}

@pytest.mark.anyio
async def test_nested_event_dispatching() -> None:
    dispatcher = EventDispatcher()
    
    async def sub_handler(val: int) -> int:
        return val * 2
        
    async def main_handler(val: int) -> int:
        sub_results = await dispatcher.dispatch("sub", val)
        return sub_results[0] + 10
        
    dispatcher.subscribe("sub", sub_handler)
    dispatcher.subscribe("main", main_handler)
    
    results = await dispatcher.dispatch("main", 5)
    assert results == [20]

@pytest.mark.anyio
async def test_error_during_sync_preparation() -> None:
    dispatcher = EventDispatcher()
    
    def bad_sync() -> None:
        raise RuntimeError("Fail during preparation")
        
    async def good_async() -> str:
        return "success"
        
    dispatcher.subscribe("event", bad_sync)
    dispatcher.subscribe("event", good_async)
    
    results = await dispatcher.dispatch("event")
    assert "success" in results
    assert len(results) == 1