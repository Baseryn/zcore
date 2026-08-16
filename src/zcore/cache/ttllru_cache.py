"""In-Memory Least-Recently-Used (LRU) Cache with Time-To-Live (TTL) Support.

This module provides a thread-safe, in-memory cache implementation. It combines
LRU eviction boundaries with explicit expiration controls (TTL), registration metrics,
and global eviction utility tools.
"""

import contextlib
import threading
import time
import weakref
from collections import OrderedDict
from typing import Any

# Track active cache instances via weak references to coordinate global sweeps without memory leaks
_active_caches: weakref.WeakSet["TTLLRUCache"] = weakref.WeakSet()


class TTLLRUCache:
    """Thread-safe LRU cache featuring granular record-level expiration boundaries.

    Ensures that active read/write operations utilize reentrant thread locks to mitigate
    concurrency race conditions. Binds instances within a weak-reference registry
    to facilitate background eviction sweeps.

    Attributes:
        maxsize: The maximum quantity of keys allowed before LRU eviction is triggered.
        cache: Map ordering items by lookup recency to prioritize evictions.
        _lock: Thread synchronizer protecting key state transitions.
    """

    def __init__(self, maxsize: int = 1000) -> None:
        """Initialize a TTLLRUCache instance.

        Args:
            maxsize: Cap on the volume of elements cached simultaneously. Defaults to 1000.
        """
        self.maxsize = maxsize
        self.cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        _active_caches.add(self)

    def get(self, key: str) -> Any | None:
        """Fetch an item from the cache and slide its position to the end.

        Bypasses and discards the element inline if it has exceeded its TTL.

        Args:
            key: The unique lookup key.

        Returns:
            The cached value payload, or None if missing or expired.
        """
        with self._lock:
            if key not in self.cache:
                return None
            expiry, val = self.cache[key]
            if time.time() > expiry:
                self.cache.pop(key, None)
                return None
            self.cache.move_to_end(key)
            return val

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Write a value into the cache, applying eviction bounds if full.

        If the key already exists, its previous state is replaced. If the cache size
        reaches `maxsize`, the oldest (least recently used) record is evicted.

        Args:
            key: The unique lookup key.
            value: The data payload to store.
            ttl: Lifespan limit in seconds before the key is marked expired. Defaults to 3600.
        """
        expiry = time.time() + ttl
        with self._lock:
            if key in self.cache:
                self.cache.pop(key, None)
            elif len(self.cache) >= self.maxsize:
                self.cache.popitem(last=False)
            self.cache[key] = (expiry, value)

    def delete(self, key: str) -> None:
        """Evict a specific key-value record from the cache.

        Args:
            key: The unique target key to delete.
        """
        with self._lock:
            self.cache.pop(key, None)

    def evict_expired(self) -> None:
        """Sweep the local cache structure and purge any record that has exceeded its TTL."""
        now = time.time()
        expired_keys: list[str] = []
        with self._lock:
            for k, (expiry, _) in self.cache.items():
                if now > expiry:
                    expired_keys.append(k)
            for k in expired_keys:
                self.cache.pop(k, None)

    @classmethod
    def evict_all_expired(cls) -> None:
        """Sweeps all active caching instances registered in the system.

        Safely handles dereferenced or collection-modified objects using weak-reference pools.
        """
        for cache_instance in list(_active_caches):
            with contextlib.suppress(ReferenceError):
                cache_instance.evict_expired()
