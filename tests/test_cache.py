import gc
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

import zcore.cache.base as base_module
from zcore.cache.base import BaseCache, _ensure_eviction_task, close_cache, init_cache
from zcore.cache.ttllru_cache import TTLLRUCache, _active_caches


class SampleCachedModel(BaseModel):
    id: int
    name: str


@pytest.mark.parametrize(
    "ttl_a, ttl_b, time_advancement, expected_a, expected_b",
    [
        (5, 15, 10, None, "value_b"),
        (5, 15, 20, None, None),
    ]
)
def test_ttllru_eviction_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    ttl_a: int,
    ttl_b: int,
    time_advancement: int,
    expected_a: str | None,
    expected_b: str | None
) -> None:
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)
    monkeypatch.setattr("zcore.cache.ttllru_cache.time.time", lambda: current_time)

    cache = TTLLRUCache(maxsize=10)
    cache.set("a", "value_a", ttl=ttl_a)
    cache.set("b", "value_b", ttl=ttl_b)

    assert cache.get("a") == "value_a"
    assert cache.get("b") == "value_b"

    current_time += time_advancement

    assert cache.get("a") == expected_a
    assert cache.get("b") == expected_b

    cache.set("c", "value_c", ttl=100)
    assert cache.get("c") == "value_c"

    current_time += 200
    TTLLRUCache.evict_all_expired()

    with cache._lock:
        assert "c" not in cache.cache


@pytest.mark.anyio
@pytest.mark.parametrize(
    "redis_healthy, simulate_exception",
    [
        (False, False),
        (True, True),
    ]
)
async def test_base_cache_redis_fallback(
    monkeypatch: pytest.MonkeyPatch,
    redis_healthy: bool,
    simulate_exception: bool
) -> None:
    cache = BaseCache[str](prefix="fallback_test")

    if not redis_healthy:
        monkeypatch.setattr("zcore.cache.base._shared_redis_client", None)
        assert cache.redis_client is None
    else:
        mock_client = AsyncMock()
        if simulate_exception:
            mock_client.set.side_effect = Exception("Redis connection lost")
            mock_client.get.side_effect = Exception("Redis connection lost")
        monkeypatch.setattr("zcore.cache.base._shared_redis_client", mock_client)

    await cache.set("safety_key", "secure_value", ttl=10)

    val = await cache.get("safety_key")
    assert val == "secure_value"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload, target_type, expected_cls",
    [
        ({"id": 101, "name": "ZCore"}, SampleCachedModel, SampleCachedModel),
        ({"user_id": 99}, None, dict),
        ("plain_string", None, str),
    ]
)
async def test_cache_deserialization_types(
    payload: Any,
    target_type: type[BaseModel] | None,
    expected_cls: type[Any]
) -> None:
    cache = BaseCache[Any](prefix="typing_test")
    await cache.set("payload_key", payload, ttl=5)

    retrieved = await cache.get("payload_key", target_type=target_type)
    assert retrieved is not None
    assert isinstance(retrieved, expected_cls)

    if target_type:
        assert retrieved.id == payload["id"]
        assert retrieved.name == payload["name"]
    else:
        assert retrieved == payload


def test_ttllru_lru_eviction() -> None:
    cache = TTLLRUCache(maxsize=3)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    cache.set("k3", "v3")
    cache.get("k1")
    cache.set("k4", "v4")
    assert cache.get("k1") == "v1"
    assert cache.get("k2") is None
    assert cache.get("k3") == "v3"
    assert cache.get("k4") == "v4"


def test_ttllru_inline_eviction_on_get(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)
    monkeypatch.setattr("zcore.cache.ttllru_cache.time.time", lambda: current_time)

    cache = TTLLRUCache(maxsize=10)
    cache.set("key", "val", ttl=5)

    current_time += 10
    assert cache.get("key") is None

    with cache._lock:
        assert "key" not in cache.cache


def test_ttllru_thread_safety() -> None:
    cache = TTLLRUCache(maxsize=100)

    def worker(worker_id: int) -> None:
        for i in range(50):
            key = f"key_{worker_id}_{i}"
            cache.set(key, f"val_{i}", ttl=1)
            cache.get(key)
            if i % 5 == 0:
                cache.delete(key)
                cache.evict_expired()
            time.sleep(0.001)

    threads = []
    for t_id in range(5):
        t = threading.Thread(target=worker, args=(t_id,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()


def test_ttllru_weakref_gc() -> None:
    gc.collect()
    initial_count = len(list(_active_caches))

    def create_temporary_cache() -> None:
        _ = TTLLRUCache(maxsize=5)
        assert len(list(_active_caches)) == initial_count + 1

    create_temporary_cache()
    gc.collect()
    assert len(list(_active_caches)) == initial_count


def test_ttllru_overwrite_resets_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = 1000.0
    monkeypatch.setattr("time.time", lambda: current_time)
    monkeypatch.setattr("zcore.cache.ttllru_cache.time.time", lambda: current_time)

    cache = TTLLRUCache()
    cache.set("k1", "v1", ttl=5)
    cache.set("k1", "v2", ttl=100)

    current_time += 10
    assert cache.get("k1") == "v2"


def test_ttllru_explicit_delete() -> None:
    cache = TTLLRUCache()
    cache.set("k1", "v1")
    assert cache.get("k1") == "v1"
    cache.delete("k1")
    assert cache.get("k1") is None

    with cache._lock:
        assert "k1" not in cache.cache


@pytest.mark.anyio
async def test_base_cache_prefix_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zcore.cache.base._shared_redis_client", None)

    cache_users = BaseCache[str](prefix="users")
    cache_products = BaseCache[str](prefix="products")

    await cache_users.set("1", "alice")
    await cache_products.set("1", "laptop")

    assert await cache_users.get("1") == "alice"
    assert await cache_products.get("1") == "laptop"


@pytest.mark.anyio
async def test_cache_global_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "_shared_redis_client", None)
    monkeypatch.setattr(base_module, "_eviction_task", None)

    init_cache(redis_url="redis://localhost:6379")

    assert base_module._eviction_task is not None
    assert not base_module._eviction_task.done()

    await close_cache()

    assert base_module._eviction_task is None
    assert base_module._shared_redis_client is None


@pytest.mark.anyio
async def test_cache_double_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "_shared_redis_client", None)
    monkeypatch.setattr(base_module, "_eviction_task", None)

    init_cache()
    task1 = base_module._eviction_task
    assert task1 is not None

    init_cache()
    task2 = base_module._eviction_task
    assert task2 is task1

    await close_cache()


@pytest.mark.anyio
@pytest.mark.parametrize("redis_healthy", [True, False])
async def test_base_cache_delete_fallback(monkeypatch: pytest.MonkeyPatch, redis_healthy: bool) -> None:
    cache = BaseCache[str](prefix="del_test")
    await cache.set("k1", "v1")

    if redis_healthy:
        mock_client = AsyncMock()
        monkeypatch.setattr("zcore.cache.base._shared_redis_client", mock_client)
        await cache.delete("k1")
        mock_client.delete.assert_called_once_with("del_test:k1")
    else:
        mock_client = AsyncMock()
        mock_client.delete.side_effect = Exception("Redis connection lost")
        mock_client.get.side_effect = Exception("Redis connection lost")
        monkeypatch.setattr("zcore.cache.base._shared_redis_client", mock_client)
        await cache.delete("k1")
        assert await cache.get("k1") is None


@pytest.mark.anyio
async def test_base_cache_corrupt_json_deserialization(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = BaseCache[Any](prefix="corrupt_test")
    monkeypatch.setattr("zcore.cache.base._shared_redis_client", None)

    cache._local_cache.set("corrupt_test:corrupt_key", "{invalid_json_string}")

    val = await cache.get("corrupt_key")
    assert val is None


def test_ensure_eviction_task_outside_event_loop_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "_eviction_task", None)
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("no running event loop")):
        _ensure_eviction_task()
        assert base_module._eviction_task is None


@pytest.mark.anyio
async def test_close_cache_cancellation_suppression(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_redis = AsyncMock()
    monkeypatch.setattr(base_module, "_shared_redis_client", mock_redis)
    monkeypatch.setattr(base_module, "_eviction_task", None)

    base_module._ensure_eviction_task()
    task = base_module._eviction_task
    assert task is not None
    assert not task.done()

    await close_cache()

    assert base_module._eviction_task is None
    assert base_module._shared_redis_client is None
    mock_redis.aclose.assert_called_once()