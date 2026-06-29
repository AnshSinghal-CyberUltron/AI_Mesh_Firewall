"""MCP connector -> Redis scan-version bump for gateway cache invalidation (M-15).

The gateway caches each server's enabled/disabled tool sets + scan controls
(``mcp_proxy._get_enabled_tools``, ~30s TTL per worker). Without invalidation,
an operator disabling a tool waits up to 30s per gateway worker. To make
changes take effect immediately, control bumps a monotonically increasing
version key in the SAME Redis the gateway reads (mirrors how core/signals.py
publishes kill-switch / firewall-config state):

- ``mcp:scan_ver:{org_slug}``                 -- org-scoped scan-control changes
- ``mcp:scan_ver:{org_slug}:{server_slug}``   -- server/tool-level changes

The gateway folds both into a composite version checked on every cache hit
(one cheap Redis MGET per MCP call); a mismatch forces an HTTP refetch.
``INCR`` is atomic; an absent key reads as version 0 on the gateway side, and
``INCR`` on an absent key yields 1, so first-bump is always a visible change.

Error handling mirrors core/signals.py: Redis failures are logged but never
block the Django save -- the gateway then degrades to its TTL-only behaviour.
"""

import logging
from typing import Any

import redis
from ai_mesh_shared.redis_pool import connection_pool_kwargs
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from mcp_connector.models import (
    MCPScanControl,
    MCPServerRegistration,
    MCPToolRegistration,
)

logger = logging.getLogger(__name__)

SCAN_VER_KEY_PREFIX = "mcp:scan_ver"

# MCPServerRegistration fields that change what the gateway's enabled-tools /
# server-config view of the world looks like. Bookkeeping saves (health pings,
# sync timestamps, OAuth state churn) use ``update_fields`` and are skipped so
# they don't invalidate every gateway cache entry on each health cycle.
_SERVER_RELEVANT_FIELDS = frozenset({
    "default_scan_action",
    "is_active",
    "is_exposed_to_agents",
    "name",
    "server_slug",
})

_redis_pool: redis.ConnectionPool | None = None


def _get_redis_pool() -> redis.ConnectionPool:
    """Return (or create) a module-level connection pool for Redis."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(
            getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            **connection_pool_kwargs(
                max_connections=20,
                socket_timeout=3,
                socket_connect_timeout=2,
                retry_on_timeout=True,
            ),
        )
    return _redis_pool


def _get_redis_client() -> redis.Redis:
    """Return a Redis client instance (tests monkeypatch this)."""
    return redis.Redis(connection_pool=_get_redis_pool())


def scan_version_key(org_slug: str, server_slug: str = "") -> str:
    """Redis key the gateway polls: org-level, or server-level when given."""
    if server_slug:
        return f"{SCAN_VER_KEY_PREFIX}:{org_slug}:{server_slug}"
    return f"{SCAN_VER_KEY_PREFIX}:{org_slug}"


def bump_scan_version(org_slug: str, server_slug: str = "") -> None:
    """INCR the gateway-visible scan-config version key after commit.

    Deferred via ``transaction.on_commit`` so a rolled-back change never
    invalidates gateway caches (and the gateway never refetches state that
    does not exist yet).
    """
    if not org_slug:
        return
    key = scan_version_key(org_slug, server_slug)

    def _do_bump() -> None:
        try:
            version = _get_redis_client().incr(key)
            logger.info("MCP scan version bumped: %s -> %s", key, version)
        except redis.RedisError:
            logger.exception(
                "Failed to bump MCP scan version %s. Gateways may serve a "
                "stale (<=TTL) tool/scan config until their cache expires.",
                key,
            )

    transaction.on_commit(_do_bump)


def _server_scope(server: MCPServerRegistration | None) -> tuple[str, str]:
    """Resolve (org_slug, server_slug) for a server, tolerating missing org."""
    if server is None:
        return "", ""
    org = getattr(server, "organization", None)
    return (getattr(org, "slug", "") or "", server.server_slug or "")


# ── MCPToolRegistration: enable/disable + scan_action overrides ─────


@receiver(post_save, sender=MCPToolRegistration)
def bump_scan_version_on_tool_save(
    sender: type,
    instance: MCPToolRegistration,
    created: bool,
    **kwargs: Any,
) -> None:
    """Tool toggled / scan_action changed / (re)discovered -> bump server key."""
    try:
        org_slug, server_slug = _server_scope(instance.server)
    except MCPServerRegistration.DoesNotExist:  # pragma: no cover — FK race
        return
    bump_scan_version(org_slug, server_slug)


@receiver(post_delete, sender=MCPToolRegistration)
def bump_scan_version_on_tool_delete(
    sender: type,
    instance: MCPToolRegistration,
    **kwargs: Any,
) -> None:
    """Tool pruned -> bump server key so gateways drop it from known/disabled."""
    try:
        org_slug, server_slug = _server_scope(instance.server)
    except MCPServerRegistration.DoesNotExist:  # pragma: no cover — cascade
        return
    bump_scan_version(org_slug, server_slug)


# ── MCPServerRegistration: default_scan_action & exposure changes ───


@receiver(post_save, sender=MCPServerRegistration)
def bump_scan_version_on_server_save(
    sender: type,
    instance: MCPServerRegistration,
    created: bool,
    **kwargs: Any,
) -> None:
    """Bump the server key when gateway-relevant server fields change.

    Saves scoped via ``update_fields`` to pure bookkeeping (health status,
    sync timestamps, OAuth state) are skipped; full saves and saves touching
    any relevant field bump conservatively.
    """
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not (
        _SERVER_RELEVANT_FIELDS & set(update_fields)
    ):
        return
    org_slug, server_slug = _server_scope(instance)
    bump_scan_version(org_slug, server_slug)


@receiver(post_delete, sender=MCPServerRegistration)
def bump_scan_version_on_server_delete(
    sender: type,
    instance: MCPServerRegistration,
    **kwargs: Any,
) -> None:
    """Server removed -> bump so gateways refetch (and 404 -> negative cache)."""
    org_slug, server_slug = _server_scope(instance)
    bump_scan_version(org_slug, server_slug)


# ── MCPScanControl: scan control matrix changes ──────────────────────


def _bump_for_scan_control(instance: MCPScanControl) -> None:
    """Server-scoped rows bump the server key; org/global rows bump org key."""
    org = getattr(instance, "organization", None)
    org_slug = getattr(org, "slug", "") or ""
    server_slug = ""
    if instance.server_id:
        try:
            _, server_slug = _server_scope(instance.server)
        except MCPServerRegistration.DoesNotExist:  # pragma: no cover — cascade
            server_slug = ""
    bump_scan_version(org_slug, server_slug)


@receiver(post_save, sender=MCPScanControl)
def bump_scan_version_on_scan_control_save(
    sender: type,
    instance: MCPScanControl,
    created: bool,
    **kwargs: Any,
) -> None:
    _bump_for_scan_control(instance)


@receiver(post_delete, sender=MCPScanControl)
def bump_scan_version_on_scan_control_delete(
    sender: type,
    instance: MCPScanControl,
    **kwargs: Any,
) -> None:
    _bump_for_scan_control(instance)
