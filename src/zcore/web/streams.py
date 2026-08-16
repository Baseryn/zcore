"""Redis PubSub Streaming Engine.

This module coordinates multi-subscriber real-time event streaming. It maps active
user subscription queues locally, handles cluster-wide message propagation
via Redis PubSub, and safely unregisters listeners on connection termination.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import structlog

from zcore.utils.helpers import json_dumps, json_loads

logger = structlog.get_logger()

_stream_redis_client: Any = None


def init_stream_redis(client: Any) -> None:
    """Initialize the shared Redis connection client for the streaming subsystem.

    Args:
        client: The Redis connection client.
    """
    global _stream_redis_client
    _stream_redis_client = client


class StreamManager:
    """Coordinates active listener queues and Redis PubSub operations.

    Supports cluster-wide event routing via subscription channels formatted as
    `stream:user:<user_id>`, falling back to local memory queues if Redis is unavailable.

    Attributes:
        users_queues: In-memory mapping of active user IDs to list of active
            asyncio listener queues.
        _pubsub_task: The background asyncio Task processing incoming Redis PubSub frames.
        _pubsub: The active Redis PubSub subscription connection context.
        _lock: Reentrant async lock protecting user queue state modifications.
    """

    def __init__(self) -> None:
        """Initialize the StreamManager instance."""
        self.users_queues: dict[uuid.UUID, list[asyncio.Queue[Any]]] = {}
        self._pubsub_task: asyncio.Task[None] | None = None
        self._pubsub: Any = None
        self._lock = asyncio.Lock()

    @property
    def redis_client(self) -> Any:
        """Retrieve the active shared streaming Redis client.

        Returns:
            The active Redis connection client, or None if uninitialized.
        """
        return _stream_redis_client

    async def start_listening(self) -> None:
        """Initiate background Redis PubSub pattern-matching subscribers.

        Subscribes to cluster-wide channels of pattern `stream:user:*` and schedules
        the active listener task in the event loop.
        """
        client = self.redis_client
        if not client:
            return
        try:
            self._pubsub = client.pubsub()
            await self._pubsub.psubscribe("stream:user:*")
            self._pubsub_task = asyncio.create_task(self._listen_to_redis())
        except Exception as e:
            logger.error(f"Failed to subscribe to Redis PubSub: {e}")

    async def _listen_to_redis(self) -> None:
        """Background loop reading and routing incoming PubSub messages.

        Unpacks incoming messages, parses user scope identifiers, and forwards
        parsed payloads to matching active local queues.
        """
        try:
            async for message in self._pubsub.listen():
                if message["type"] == "pmessage":
                    channel = message["channel"]
                    user_id_str = channel.split(":")[-1]
                    try:
                        user_id = uuid.UUID(user_id_str)
                    except ValueError:
                        continue
                    data = json_loads(message["data"])
                    await self._local_publish(user_id, data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in Redis PubSub stream listener: {e}")
        finally:
            if self._pubsub:
                try:
                    await self._pubsub.punsubscribe("stream:user:*")
                    await self._pubsub.close()
                    logger.info("Redis PubSub connection safely released.")
                except Exception as e:
                    logger.error(f"Failed to release Redis PubSub connection: {e}")

    async def subscribe(self, user_id: uuid.UUID) -> asyncio.Queue[Any]:
        """Subscribe a user, returning a bounded async listener queue.

        Initializes background Redis listeners if this is the first active subscription
        for a user in this node.

        Args:
            user_id: The target user identifier key.

        Returns:
            A bounded asyncio Queue configured to receive events.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)
        async with self._lock:
            if user_id not in self.users_queues:
                self.users_queues[user_id] = []
                client = self.redis_client
                if client and (self._pubsub_task is None or self._pubsub_task.done()):
                    await self.start_listening()
            self.users_queues[user_id].append(queue)
        return queue

    async def unsubscribe(self, user_id: uuid.UUID, queue: asyncio.Queue[Any]) -> None:
        """Unsubscribe a user's listener queue.

        Pops the active queue, and shuts down the background task if no active
        listener queues remain in the system.

        Args:
            user_id: The target user identifier key.
            queue: The active asyncio Queue to unsubscribe.
        """
        async with self._lock:
            if user_id in self.users_queues:
                if queue in self.users_queues[user_id]:
                    self.users_queues[user_id].remove(queue)
                if not self.users_queues[user_id]:
                    del self.users_queues[user_id]
            if (
                not self.users_queues
                and self._pubsub_task
                and not self._pubsub_task.done()
            ):
                self._pubsub_task.cancel()
                self._pubsub_task = None

    @asynccontextmanager
    async def subscription(
        self, user_id: uuid.UUID
    ) -> AsyncGenerator[asyncio.Queue[Any], None]:
        """Context manager safely wrapping active user event streams.

        Guarantees cleanup and unregistration of the queue upon block exit.

        Args:
            user_id: The target user identifier key to stream.

        Yields:
            A bounded async Queue populated with incoming events.
        """
        queue = await self.subscribe(user_id)
        try:
            yield queue
        finally:
            await self.unsubscribe(user_id, queue)

    async def publish(self, user_id: uuid.UUID, data: dict[str, Any]) -> None:
        """Publish an event payload to a target user's stream.

        Routes the message through Redis PubSub if configured to broadcast to
        all active nodes, falling back to local memory delivery if unconfigured.

        Args:
            user_id: The target user identifier key.
            data: Key-value dictionary event payload to publish.
        """
        client = self.redis_client
        if client:
            try:
                await client.publish(f"stream:user:{user_id}", json_dumps(data))
                return
            except Exception as e:
                logger.error(f"Redis publish failed: {e}")
        await self._local_publish(user_id, data)

    async def _local_publish(self, user_id: uuid.UUID, data: dict[str, Any]) -> None:
        """Route event data locally to all registered active queues for a user.

        Unsubscribes and discards queues that overflow to prevent resource exhaustion.

        Args:
            user_id: The target user identifier key.
            data: Key-value dictionary event payload.
        """
        async with self._lock:
            queues = self.users_queues.get(user_id)
            if not queues:
                return
            for queue in list(queues):
                try:
                    queue.put_nowait(data)
                except asyncio.QueueFull:
                    if queue in queues:
                        queues.remove(queue)
            if not self.users_queues[user_id]:
                del self.users_queues[user_id]
