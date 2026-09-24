"""
Redis Pub/Sub logging handler for centralized log aggregation.

Publishes structured JSON log records to a Redis Pub/Sub channel
so that any subscriber (e.g., the gateway SSE endpoint) can aggregate
logs from all services (gateway, backend, celery).
"""

import json
import logging
import threading
import time
from typing import Optional

import redis

REDIS_LOG_CHANNEL = "logs:stream"

# Logger name prefix -> human-readable service label.
# Longest (most specific) prefixes first so they match before shorter ones.
DEFAULT_SERVICE_MAP = [
    ("gateway.bedrock_scanner", "Bedrock"),
    ("gateway.bedrock_client", "Bedrock"),
    ("gateway.scanner", "Scanner"),
    ("gateway.middleware", "Middleware"),
    ("gateway", "Gateway"),
    ("bedrock", "Bedrock"),
    ("backend.bedrock_scanner", "Bedrock"),
    ("backend.bedrock_client", "Bedrock"),
    ("security_engines", "Backend"),
    ("policy", "Backend"),
    ("core", "Backend"),
    ("celery", "Celery"),
    ("django", "Backend"),
]


class RedisLogPublisher(logging.Handler):
    """
    Python logging handler that publishes log records to Redis Pub/Sub.

    Thread-safe. Uses a sync Redis client since logging.Handler.emit()
    is always called synchronously.

    On Redis connection failure, silently drops the record (fail-open
    for logging -- logging must never crash the service).
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        service_name: str = "Unknown",
        service_map: Optional[list] = None,
        channel: str = REDIS_LOG_CHANNEL,
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level=level)
        self._redis_url = redis_url
        self._default_service = service_name
        self._service_map = service_map or DEFAULT_SERVICE_MAP
        self._channel = channel
        self._client: Optional[redis.Redis] = None
        self._lock = threading.Lock()

    def _get_client(self) -> Optional[redis.Redis]:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                try:
                    self._client = redis.Redis.from_url(
                        self._redis_url,
                        decode_responses=True,
                        socket_timeout=1.0,
                        socket_connect_timeout=1.0,
                    )
                except Exception:
                    pass
            return self._client

    def _resolve_service(self, logger_name: str) -> str:
        for prefix, label in self._service_map:
            if logger_name.startswith(prefix):
                return label
        return self._default_service

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
                ),
                "level": record.levelname,
                "service": self._resolve_service(record.name),
                "logger": record.name,
                "message": self.format(record),
            }
            client = self._get_client()
            if client is not None:
                client.publish(self._channel, json.dumps(entry))
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            with self._lock:
                self._client = None
        except Exception:
            self.handleError(record)
