"""Shared redis-py connection pool kwargs (maintenance notification handshake)."""

from __future__ import annotations

from typing import Any


def channels_redis_host_config(redis_url: str, **overrides: Any) -> dict[str, Any]:
    """Kwargs for ``channels_redis`` host entries (async redis-py).

    Uses RESP2 (async pool does not accept ``maint_notifications_config``) and
    longer socket timeouts so the channel-layer BRPOP loop does not trip
    ``Timeout reading from redis``.
    """
    base: dict[str, Any] = {
        "socket_timeout": 30,
        "socket_connect_timeout": 10,
        "retry_on_timeout": True,
        "health_check_interval": 30,
        "protocol": 2,
    }
    base.update(overrides)
    return {"address": redis_url, **base}


def connection_pool_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return kwargs for ``redis.ConnectionPool.from_url`` / django-redis pools.

    redis-py 5.7+ negotiates RESP3 maintenance push notifications on connect.
    Local ``redis:7-alpine`` is often < 7.2 and does not support
    ``CLIENT MAINT_NOTIFICATIONS``, which spams DEBUG logs on every reconnect.
    """
    kwargs: dict[str, Any] = dict(overrides)
    try:
        from redis.maint_notifications import MaintNotificationsConfig
    except ImportError:
        return kwargs
    kwargs.setdefault(
        "maint_notifications_config",
        MaintNotificationsConfig(enabled=False),
    )
    return kwargs
