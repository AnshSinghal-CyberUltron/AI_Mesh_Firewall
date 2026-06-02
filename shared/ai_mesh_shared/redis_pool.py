"""Shared redis-py connection pool kwargs (maintenance notification handshake)."""

from __future__ import annotations

from typing import Any


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
