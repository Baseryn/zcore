import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import zcore.web.streams as streams_module
from zcore.web.streams import StreamManager, init_stream_redis


@pytest.fixture(autouse=True)
def bypass_redis_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(streams_module, "_stream_redis_client", None)

def test_init_stream_redis() -> None:
    mock_client = MagicMock()
    init_stream_redis(mock_client)
    assert streams_module._stream_redis_client is mock_client
    init_stream_redis(None)

@pytest.mark.anyio
@pytest.mark.parametrize("num_subscribers", [1, 3])
async def test_stream_pubsub_lifecycle(num_subscribers: int) -> None:
    manager = StreamManager()
    user_id = uuid.uuid4()
    
    assert len(manager.users_queues) == 0
    assert manager._pubsub_task is None

    queues: list[asyncio.Queue[Any]] = []
    for _ in range(num_subscribers):
        q = await manager.subscribe(user_id)
        queues.append(q)

    assert len(manager.users_queues) == 1
    assert len(manager.users_queues[user_id]) == num_subscribers
    assert manager._pubsub_task is None

    payload = {"message_id": str(uuid.uuid4()), "event": "test_signal"}
    await manager.publish(user_id, payload)

    for q in queues:
        received = await asyncio.wait_for(q.get(), timeout=1.0)
        assert received == payload

    for q in queues:
        await manager.unsubscribe(user_id, q)

    assert len(manager.users_queues) == 0
    assert manager._pubsub_task is None

@pytest.mark.anyio
@pytest.mark.parametrize(
    "total_queues, overflow_index",
    [
        (2, 0),
        (3, 1),
    ]
)
async def test_stream_queue_overflow(total_queues: int, overflow_index: int) -> None:
    manager = StreamManager()
    user_id = uuid.uuid4()
    
    queues: list[asyncio.Queue[Any]] = []
    for _ in range(total_queues):
        q = await manager.subscribe(user_id)
        queues.append(q)

    overflow_queue = queues[overflow_index]
    overflow_queue.put_nowait = MagicMock(side_effect=asyncio.QueueFull)

    payload = {"alert": "system_overload"}
    await manager.publish(user_id, payload)

    assert overflow_queue not in manager.users_queues[user_id]
    assert len(manager.users_queues[user_id]) == total_queues - 1

    for i, q in enumerate(queues):
        if i != overflow_index:
            received = await asyncio.wait_for(q.get(), timeout=1.0)
            assert received == payload

    for q in list(manager.users_queues.get(user_id, [])):
        await manager.unsubscribe(user_id, q)

@pytest.mark.anyio
async def test_redis_lazy_background_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_redis = MagicMock()
    mock_pubsub = AsyncMock()
    
    async def mock_listen() -> AsyncGenerator[dict[str, Any], None]:
        while True:
            await asyncio.sleep(1)
            yield {}
            
    mock_pubsub.listen = mock_listen
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    monkeypatch.setattr(streams_module, "_stream_redis_client", mock_redis)
    
    manager = StreamManager()
    assert manager._pubsub_task is None
    
    user_id = uuid.uuid4()
    q = await manager.subscribe(user_id)
    await asyncio.sleep(0.01)
    
    assert manager._pubsub_task is not None
    assert not manager._pubsub_task.done()
    mock_redis.pubsub.assert_called_once()
    mock_pubsub.psubscribe.assert_called_once_with("stream:user:*")
    
    await manager.unsubscribe(user_id, q)

@pytest.mark.anyio
async def test_redis_clean_teardown_on_empty_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_redis = MagicMock()
    mock_pubsub = AsyncMock()
    
    async def mock_listen() -> AsyncGenerator[dict[str, Any], None]:
        while True:
            await asyncio.sleep(1)
            yield {}
            
    mock_pubsub.listen = mock_listen
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    monkeypatch.setattr(streams_module, "_stream_redis_client", mock_redis)
    
    manager = StreamManager()
    user_id = uuid.uuid4()
    q = await manager.subscribe(user_id)
    await asyncio.sleep(0.01)
    
    assert manager._pubsub_task is not None
    
    await manager.unsubscribe(user_id, q)
    await asyncio.sleep(0.01)
    
    assert manager._pubsub_task is None
    mock_pubsub.punsubscribe.assert_called_once_with("stream:user:*")
    mock_pubsub.close.assert_called_once()

@pytest.mark.anyio
async def test_redis_message_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_redis = MagicMock()
    mock_pubsub = AsyncMock()
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    monkeypatch.setattr(streams_module, "_stream_redis_client", mock_redis)
    
    user_id = uuid.uuid4()
    payload = {"test": "data"}
    
    async def mock_listen() -> AsyncGenerator[dict[str, Any], None]:
        yield {
            "type": "pmessage",
            "channel": f"stream:user:{user_id}",
            "data": json.dumps(payload)
        }
        while True:
            await asyncio.sleep(1)
            
    mock_pubsub.listen = mock_listen
    
    manager = StreamManager()
    q = await manager.subscribe(user_id)
    
    received = await asyncio.wait_for(q.get(), timeout=1.0)
    assert received == payload
    
    await manager.unsubscribe(user_id, q)

@pytest.mark.anyio
async def test_redis_malformatted_message_robustness(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_redis = MagicMock()
    mock_pubsub = AsyncMock()
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    monkeypatch.setattr(streams_module, "_stream_redis_client", mock_redis)
    
    user_id = uuid.uuid4()
    payload = {"valid": "json"}
    
    async def mock_listen() -> AsyncGenerator[dict[str, Any], None]:
        yield {
            "type": "pmessage",
            "channel": "stream:user:invalid-uuid-format",
            "data": json.dumps(payload)
        }
        yield {
            "type": "pmessage",
            "channel": f"stream:user:{user_id}",
            "data": json.dumps(payload)
        }
        while True:
            await asyncio.sleep(1)
            
    mock_pubsub.listen = mock_listen
    
    manager = StreamManager()
    q = await manager.subscribe(user_id)
    
    received = await asyncio.wait_for(q.get(), timeout=1.0)
    assert received == payload
    
    await manager.unsubscribe(user_id, q)

@pytest.mark.anyio
async def test_stream_subscription_context_manager_normal_and_error() -> None:
    manager = StreamManager()
    user_id = uuid.uuid4()
    
    async with manager.subscription(user_id) as q:
        assert q in manager.users_queues[user_id]
        await manager.publish(user_id, {"msg": "hi"})
        res = await asyncio.wait_for(q.get(), timeout=1.0)
        assert res == {"msg": "hi"}
        
    assert user_id not in manager.users_queues
    
    try:
        async with manager.subscription(user_id) as q_err:
            assert q_err in manager.users_queues[user_id]
            raise ValueError("Simulated error")
    except ValueError:
        pass
        
    assert user_id not in manager.users_queues

@pytest.mark.anyio
async def test_stream_multi_user_isolation() -> None:
    manager = StreamManager()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    
    q_a = await manager.subscribe(user_a)
    q_b = await manager.subscribe(user_b)
    
    payload = {"data": "isolated"}
    await manager.publish(user_a, payload)
    
    received_a = await asyncio.wait_for(q_a.get(), timeout=1.0)
    assert received_a == payload
    
    assert q_b.empty()
    
    await manager.unsubscribe(user_a, q_a)
    await manager.unsubscribe(user_b, q_b)

@pytest.mark.anyio
async def test_stream_garbage_collection_on_all_queues_overflow() -> None:
    manager = StreamManager()
    user_id = uuid.uuid4()
    
    q1 = await manager.subscribe(user_id)
    q2 = await manager.subscribe(user_id)
    
    q1.put_nowait = MagicMock(side_effect=asyncio.QueueFull)
    q2.put_nowait = MagicMock(side_effect=asyncio.QueueFull)
    
    await manager.publish(user_id, {"event": "overflow"})
    
    assert user_id not in manager.users_queues

@pytest.mark.anyio
async def test_stream_robust_unsubscribe_nonexistent() -> None:
    manager = StreamManager()
    user_id = uuid.uuid4()
    fake_queue = asyncio.Queue()
    
    await manager.unsubscribe(user_id, fake_queue)
    
    q = await manager.subscribe(user_id)
    await manager.unsubscribe(user_id, q)
    await manager.unsubscribe(user_id, q)
    
    assert user_id not in manager.users_queues

@pytest.mark.anyio
async def test_redis_active_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_redis = MagicMock()
    mock_redis.publish = AsyncMock()
    monkeypatch.setattr(streams_module, "_stream_redis_client", mock_redis)
    
    manager = StreamManager()
    user_id = uuid.uuid4()
    payload = {"event": "test"}
    
    await manager.publish(user_id, payload)
    
    mock_redis.publish.assert_called_once_with(
        f"stream:user:{user_id}",
        json.dumps(payload)
    )

@pytest.mark.anyio
async def test_redis_publish_fallback_to_local(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_redis = MagicMock()
    mock_pubsub = AsyncMock()
    
    async def mock_listen() -> AsyncGenerator[dict[str, Any], None]:
        while True:
            await asyncio.sleep(1)
            yield {}
            
    mock_pubsub.listen = mock_listen
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
    mock_redis.publish = AsyncMock(side_effect=Exception("Redis publish error"))
    monkeypatch.setattr(streams_module, "_stream_redis_client", mock_redis)
    
    manager = StreamManager()
    user_id = uuid.uuid4()
    q = await manager.subscribe(user_id)
    
    payload = {"event": "fallback"}
    await manager.publish(user_id, payload)
    
    received = await asyncio.wait_for(q.get(), timeout=1.0)
    assert received == payload
    
    await manager.unsubscribe(user_id, q)