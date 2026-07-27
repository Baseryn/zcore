import asyncio
import time
from typing import Any, Type
from unittest.mock import AsyncMock
import pytest
from pydantic import BaseModel

from zcore.cache.base import BaseCache
from zcore.cache.ttllru_cache import TTLLRUCache

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
    target_type: Type[BaseModel] | None,
    expected_cls: Type[Any]
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