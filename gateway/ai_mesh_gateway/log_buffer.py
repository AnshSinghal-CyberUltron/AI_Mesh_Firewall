"""
In-memory log buffer with async subscriber support for SSE streaming.

Records are fed into the buffer via RedisLogSubscriber (which consumes
from the centralized Redis Pub/Sub log channel). SSE clients subscribe
via an async generator that yields new records as they arrive.
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import AsyncGenerator, Optional


MAX_BUFFER_SIZE = 500
POLL_INTERVAL_SECONDS = 0.5


class LogRecord:
    """Lightweight serializable log record."""

    __slots__ = ("timestamp", "level", "service", "message")

    def __init__(self, timestamp: str, level: str, service: str, message: str) -> None:
        self.timestamp = timestamp
        self.level = level
        self.service = service
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "service": self.service,
            "message": self.message,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class LogBuffer:
    """
    Thread-safe circular buffer for recent log records.

    Records are pushed from a custom logging.Handler and consumed
    by async SSE subscribers.
    """

    def __init__(self, max_size: int = MAX_BUFFER_SIZE) -> None:
        self._buffer: deque[LogRecord] = deque(maxlen=max_size)
        self._counter: int = 0

    def push(self, record: LogRecord) -> None:
        self._buffer.append(record)
        self._counter += 1

    @property
    def counter(self) -> int:
        return self._counter

    def recent(self, limit: int = 50) -> list[LogRecord]:
        items = list(self._buffer)
        return items[-limit:]

    async def subscribe(
        self,
        service_filter: Optional[str] = None,
        level_filter: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Async generator that yields new log records as SSE-formatted strings.

        Sends an initial SSE comment immediately to trigger the browser
        EventSource ``onopen`` callback, then polls the buffer for new
        entries every POLL_INTERVAL_SECONDS. Periodic heartbeat comments
        are emitted when idle to keep the connection alive.

        Filters by service prefix and minimum log level if specified.
        """
        HEARTBEAT_INTERVAL_SECONDS: float = 15.0

        level_priority = {
            "DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4,
        }
        min_level = level_priority.get((level_filter or "").upper(), 0)

        yield ": connected\n\n"

        # Send recent buffered logs so the viewer is populated immediately
        for item in self.recent(50):
            if service_filter and service_filter != "all":
                if not item.service.lower().startswith(service_filter.lower()):
                    continue
            item_level = level_priority.get(item.level.upper(), 0)
            if item_level < min_level:
                continue
            yield f"data: {item.to_json()}\n\n"

        last_seen = self._counter
        time_since_heartbeat: float = 0.0

        while True:
            current = self._counter
            if current > last_seen:
                new_count = current - last_seen
                items = list(self._buffer)
                new_items = items[-new_count:] if new_count <= len(items) else items

                for item in new_items:
                    if service_filter and service_filter != "all":
                        if not item.service.lower().startswith(service_filter.lower()):
                            continue
                    item_level = level_priority.get(item.level.upper(), 0)
                    if item_level < min_level:
                        continue
                    yield f"data: {item.to_json()}\n\n"

                last_seen = current
                time_since_heartbeat = 0.0
            else:
                time_since_heartbeat += POLL_INTERVAL_SECONDS
                if time_since_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    yield ": heartbeat\n\n"
                    time_since_heartbeat = 0.0

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


class RedisLogSubscriber:
    """
    Async background task that subscribes to the Redis Pub/Sub
    log channel and feeds incoming records into the LogBuffer.

    All services (gateway, backend, celery) publish to the channel;
    this subscriber feeds everything into the local LogBuffer for
    SSE streaming.
    """

    def __init__(
        self,
        redis_url: str,
        buffer: LogBuffer,
        channel: str = "logs:stream",
    ) -> None:
        self._redis_url = redis_url
        self._buffer = buffer
        self._channel = channel
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._subscribe_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _subscribe_loop(self) -> None:
        import redis.asyncio as aioredis

        while self._running:
            client = None
            pubsub = None
            try:
                client = aioredis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_connect_timeout=3.0,
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(self._channel)

                async for message in pubsub.listen():
                    if not self._running:
                        break
                    if message["type"] != "message":
                        continue
                    try:
                        data = json.loads(message["data"])
                        record = LogRecord(
                            timestamp=data.get("timestamp", ""),
                            level=data.get("level", "INFO"),
                            service=data.get("service", "Unknown"),
                            message=data.get("message", ""),
                        )
                        self._buffer.push(record)
                    except (json.JSONDecodeError, KeyError):
                        pass

            except asyncio.CancelledError:
                break
            except Exception:
                if self._running:
                    await asyncio.sleep(5)
            finally:
                if pubsub:
                    try:
                        await pubsub.unsubscribe(self._channel)
                        await pubsub.aclose()
                    except Exception:
                        pass
                if client:
                    try:
                        await client.aclose()
                    except Exception:
                        pass


LOG_BUFFER = LogBuffer()
