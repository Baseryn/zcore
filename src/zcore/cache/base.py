"""Caching Abstraction and Management Layer.

This module provides a unified cache manager supporting distributed caching (via Redis)
with an asynchronous, thread-safe local fallback (via `TTLLRUCache`). It coordinates
global cache lifecycles, structured serialization and deserialization, and automatically
spawns background memory eviction routines safely within active event loops based on dynamic settings.
"""

import asyncio
import contextlib
from typing import Any, Generic, TypeVar

import structlog
from pydantic import BaseModel

from zcore.cache.ttllru_cache import TTLLRUCache
from zcore.config import settings
from zcore.utils.helpers import json_dumps, json_loads

T = TypeVar("T")
logger = structlog.get_logger()

try:
    import redis.asyncio as aioredis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

_shared_redis_client: Any | None = None
_eviction_task: asyncio.Task | None = None


def _ensure_eviction_task(interval: int | None = None) -> None:
    """Schedule the background memory eviction task if an active event loop is running.

    Args:
        interval: Rest period duration in seconds between garbage collection sweeps.
    """
    global _eviction_task
    raw_interval = interval or getattr(settings, "CACHE_EVICTION_INTERVAL", 60)
    effective_interval: int = int(raw_interval) if raw_interval is not None else 60
    try:
        loop = asyncio.get_running_loop()
        if _eviction_task is None or _eviction_task.done():
            _eviction_task = loop.create_task(_start_eviction_loop(interval=effective_interval))
    except RuntimeError:
        pass


def init_cache(redis_url: str | None = None, **kwargs: Any) -> None:
    """Initialize global distributed caching clients and local eviction workers.

    Args:
        redis_url: Connection URL pointing to a Redis server instance. Defaults to None.
        **kwargs: Connection pool parameters passed directly to the Redis client initialization.
    """
    global _shared_redis_client

    if REDIS_AVAILABLE and redis_url:
        try:
            _shared_redis_client = aioredis.from_url(
                redis_url, encoding="utf-8", decode_responses=True, **kwargs
            )
            logger.debug("Shared Redis cache client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize shared Redis client: {e}")

    _ensure_eviction_task()


async def _start_eviction_loop(interval: int = 60) -> None:
    """Background loop invoking garbage collection routines on expired in-memory cache keys.

    Args:
        interval: Rest period duration in seconds between garbage collection sweeps.
    """
    while True:
        try:
            await asyncio.sleep(interval)
            TTLLRUCache.evict_all_expired()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cache memory eviction loop: {e}")


async def close_cache() -> None:
    """Cancel background loops and cleanly close distributed connections."""
    global _shared_redis_client, _eviction_task

    if _eviction_task and not _eviction_task.done():
        _eviction_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _eviction_task
        _eviction_task = None
        logger.debug("Cache memory eviction loop stopped.")

    if _shared_redis_client:
        try:
            await _shared_redis_client.aclose()
            _shared_redis_client = None
            logger.debug("Shared Redis cache connection closed successfully.")
        except Exception as e:
            logger.error(f"Failed to cleanly close Redis cache: {e}")


class BaseCache(Generic[T]):
    """Generic base cache interface with transparent distributed and local fallbacks.

    Attributes:
        prefix: Namespace prefix applied to all keys managed by this instance.
        default_ttl: Fallback lifespan for stored cache records.
        _local_cache: Thread-safe local cache backup.
    """

    def __init__(
        self,
        prefix: str,
        local_maxsize: int | None = None,
        default_ttl: int | None = None,
    ) -> None:
        """Initialize a BaseCache instance.

        Args:
            prefix: Dot-path or identifier namespace prefix used to scope keys.
            local_maxsize: Optional maximum entries allowed in the local fallback store.
            default_ttl: Optional default expiration limit in seconds.
        """
        self.prefix = prefix
        self.default_ttl = default_ttl or getattr(settings, "CACHE_DEFAULT_TTL", 3600)
        effective_maxsize = local_maxsize or getattr(settings, "CACHE_LOCAL_MAXSIZE", 1000)
        self._local_cache = TTLLRUCache(maxsize=effective_maxsize)

    @property
    def redis_client(self) -> Any:
        """Retrieve the active shared Redis client.

        Returns:
            The active Redis connection client, or None if distributed caching is uninitialized.
        """
        return _shared_redis_client

    def _get_key(self, key: str) -> str:
        """Construct a prefixed key to enforce namespace isolation.

        Args:
            key: Raw cache key query identifier.

        Returns:
            Prefixed string key.
        """
        return f"{self.prefix}:{key}"

    async def get(
        self, key: str, target_type: type[BaseModel] | None = None
    ) -> T | BaseModel | Any | None:
        """Retrieve and deserialize a cache record by its key.

        Args:
            key: The unique identifier key query.
            target_type: Optional Pydantic model class type to validate the decoded
                payload against. Defaults to None.

        Returns:
            The parsed data structure or validated Pydantic model instance, or None if the
            key is not found or fails to deserialize.
        """
        _ensure_eviction_task()
        full_key = self._get_key(key)
        client = self.redis_client
        raw_val = None

        if client:
            try:
                raw_val = await client.get(full_key)
            except Exception as e:
                logger.error(f"Redis get failed in '{self.prefix}': {e}")

        if raw_val is None:
            raw_val = self._local_cache.get(full_key)

        if raw_val is None:
            return None

        try:
            parsed_data = json_loads(raw_val) if isinstance(raw_val, (str, bytes)) else raw_val

            if target_type and issubclass(target_type, BaseModel):
                return target_type.model_validate(parsed_data)

            return parsed_data
        except Exception as e:
            logger.error(f"Failed to deserialize cache key '{full_key}': {e}")
            return None

    async def set(self, key: str, value: T, ttl: int | None = None) -> None:
        """Serialize and persist a key-value record with a Time-To-Live (TTL).

        Args:
            key: The unique identifier key to populate.
            value: The data payload to serialize and cache.
            ttl: Maximum cache duration in seconds. Defaults to instance default_ttl.
        """
        _ensure_eviction_task()
        effective_ttl = ttl if ttl is not None else self.default_ttl
        full_key = self._get_key(key)
        client = self.redis_client

        serialized_val = json_dumps(value)

        if client:
            try:
                await client.set(full_key, serialized_val, ex=effective_ttl)
                return
            except Exception as e:
                logger.error(f"Redis set failed in '{self.prefix}': {e}")

        self._local_cache.set(full_key, serialized_val, ttl=effective_ttl)

    async def delete(self, key: str) -> None:
        """Evict a record from both distributed and fallback caches.

        Args:
            key: The unique key identifier to evict.
        """
        _ensure_eviction_task()
        full_key = self._get_key(key)
        client = self.redis_client
        if client:
            try:
                await client.delete(full_key)
                return
            except Exception as e:
                logger.error(f"Redis delete failed in '{self.prefix}': {e}")

        self._local_cache.delete(full_key)
