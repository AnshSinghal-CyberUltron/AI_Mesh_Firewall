"""
PolicySync: subscribes to Redis Pub/Sub ``policy_updates`` channel and
maintains an in-memory cache of compiled policies for zero-latency
enforcement in the Gateway.

Lifecycle:
    1. On startup, scans org-scoped ``policies:compiled:*`` keys from Redis
    2. Subscribes to policy_updates Pub/Sub channel
    3. On each notification, refreshes the specific org's bundle
    4. proxy_chat() reads from the org-scoped cache for tenant isolation
"""

import asyncio
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

try:
    from .policy_signing import signing_enforced, verify_bundle
    from .telemetry_ops import (
        emit_operational_event,
        EVENT_CLASS_POLICY_HMAC_FAILURE,
    )
except ImportError:
    from policy_signing import signing_enforced, verify_bundle
    from telemetry_ops import (
        emit_operational_event,
        EVENT_CLASS_POLICY_HMAC_FAILURE,
    )

LOG = logging.getLogger("gateway.policy_sync")

REDIS_KEY_COMPILED = "policies:compiled"
REDIS_KEY_VERSION = "policies:version"
PUBSUB_CHANNEL = "policy_updates"

RECONNECT_DELAY_SECONDS = 5


def _normalize_policy_domain(domain: str | None) -> str:
    """Normalize policy_domain; legacy ``global`` rows map to ``pipeline``."""
    normalized = str(domain or "pipeline").strip().lower()
    if normalized == "global":
        return "pipeline"
    return normalized


def filter_policies_by_domain(
    policies: list[dict[str, Any]], domain: str
) -> list[dict[str, Any]]:
    """Return compiled policy entries whose policy_domain matches *domain*."""
    target = _normalize_policy_domain(domain)
    filtered: list[dict[str, Any]] = []
    for entry in policies:
        policy = entry.get("policy", {})
        p_domain = _normalize_policy_domain(policy.get("policy_domain"))
        if p_domain == target:
            filtered.append(entry)
    return filtered


class PolicySync:
    """
    Async Redis Pub/Sub subscriber that keeps per-org in-memory copies
    of compiled policy bundles for zero-latency, tenant-isolated enforcement.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url: str = redis_url
        self._org_caches: dict[str, dict[str, Any]] = {}
        self._org_versions: dict[str, int] = {}
        # Legacy single-cache kept for backward compatibility with callers
        # that use .policies / .is_loaded without specifying org
        self._cache: Optional[dict[str, Any]] = None
        self._version: int = 0
        self._subscriber_task: Optional[asyncio.Task] = None
        self._running: bool = False
        # Phase 1 Fx-1: distinguishes "sync has run (possibly empty)" from
        # "sync has never run". /health treats the former as healthy because
        # zero-policy bundles are a valid state for a fresh org — only an
        # unreachable control plane should mark the gateway as degraded.
        self._sync_completed: bool = False

    def get_policies(self, org_slug: str = "default") -> list[dict[str, Any]]:
        """Return compiled policy entries for a specific org."""
        bundle = self._org_caches.get(org_slug)
        if bundle is not None:
            return bundle.get("policies", [])
        return []

    def get_policies_for_server(
        self, org_slug: str, server_slug: str, domain: str = "mcp"
    ) -> list[dict[str, Any]]:
        """Return compiled policies applicable to a specific MCP server.

        Includes:
        - Policies explicitly bound to this server (mcp_server_slug matches)
        - Org-wide MCP policies (mcp_server_slug is None, policy_domain == domain)
        """
        all_policies = self.get_policies(org_slug)
        result = []
        normalized_domain = _normalize_policy_domain(domain)
        for entry in all_policies:
            policy = entry.get("policy", {})
            p_domain = _normalize_policy_domain(policy.get("policy_domain"))
            p_server = policy.get("mcp_server_slug")
            if p_domain == normalized_domain:
                if p_server is None or p_server == server_slug:
                    result.append(entry)
        return result

    @property
    def policies(self) -> list[dict[str, Any]]:
        """Return the default org's compiled policy entries (backward compat)."""
        return self.get_policies("default")

    @property
    def version(self) -> int:
        """Return the current cached bundle version."""
        return self._version

    @property
    def is_loaded(self) -> bool:
        """Return True if the cache has been populated at least once OR if a
        successful sync from the control plane completed with zero bundles
        (a valid state for a fresh org with no policies yet)."""
        return (
            bool(self._org_caches)
            or self._cache is not None
            or self._sync_completed
        )

    @property
    def policy_count(self) -> int:
        """Return the total number of policies across all orgs."""
        total = sum(b.get("policy_count", 0) for b in self._org_caches.values())
        if total:
            return total
        if self._cache is not None:
            return self._cache.get("policy_count", 0)
        return 0

    async def start(self) -> None:
        """
        Initialize the cache and start the background subscriber.

        1. Scan all org-scoped policy keys from Redis (cold start)
        2. Spawn the subscriber loop as an asyncio background task
        """
        self._running = True
        await self._load_initial_bundle()
        self._subscriber_task = asyncio.create_task(self._subscriber_loop())
        LOG.info(
            "PolicySync started (orgs=%d, total_policies=%d)",
            len(self._org_caches),
            self.policy_count,
        )

    async def stop(self) -> None:
        """Stop the background subscriber gracefully."""
        self._running = False
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
        LOG.info("PolicySync stopped")

    async def _load_initial_bundle(self) -> None:
        """
        Load all org-scoped compiled bundles from Redis on startup.
        Scans for ``policies:compiled:*`` keys to build per-org cache.
        Falls back to the legacy global key if no org-scoped keys exist.
        """
        try:
            client = aioredis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=3.0,
                socket_connect_timeout=2.0,
            )

            # Scan org-scoped keys: policies:compiled:{slug}
            org_keys = []
            async for key in client.scan_iter(match=f"{REDIS_KEY_COMPILED}:*", count=100):
                org_keys.append(key)

            for key in org_keys:
                raw = await client.get(key)
                if raw is None:
                    continue
                bundle = json.loads(raw)
                slug = key.split(":", 2)[-1]  # "policies:compiled:default" → "default"

                # HMAC verification (fail-closed). When signing is enforced
                # we DROP unsigned or tampered bundles entirely so a malicious
                # writer cannot replace policies with an empty allow-all set.
                if not verify_bundle(bundle):
                    if signing_enforced():
                        LOG.critical(
                            "REFUSING unsigned/invalid policy bundle for org '%s' at key '%s'. "
                            "Gateway will not load these policies.",
                            slug,
                            key,
                        )
                        # Phase 0 D-G1-v3: emit operational event with
                        # site=initial_load so dashboards can disambiguate
                        # cold-start failures from refresh failures.
                        asyncio.create_task(
                            emit_operational_event(
                                EVENT_CLASS_POLICY_HMAC_FAILURE,
                                org_slug=slug,
                                severity="critical",
                                metadata={
                                    "site": "initial_load",
                                    "redis_key": key,
                                },
                            )
                        )
                        continue
                    LOG.warning(
                        "Loading unsigned policy bundle for org '%s' "
                        "(GATEWAY_POLICY_SIGNING_REQUIRED=false; only safe during cutover)",
                        slug,
                    )

                self._org_caches[slug] = bundle
                ver = bundle.get("version", 0)
                self._org_versions[slug] = ver
                if ver > self._version:
                    self._version = ver
                LOG.info(
                    "Loaded policy bundle for org '%s' (version=%d, policies=%d)",
                    slug,
                    ver,
                    bundle.get("policy_count", 0),
                )

                if not self._org_caches:
                    LOG.warning(
                        "No org-scoped compiled policy bundles found in Redis. "
                        "Gateway will operate without policies until backend compiles per-organization bundles."
                    )

            await client.aclose()
            # Phase 1 Fx-1: sync round-trip with control plane succeeded.
            # An empty result is valid (fresh org) — mark loaded so /health
            # reports 200. Per user choice: WARN every sync that returns 0
            # bundles so operator misconfiguration cannot stay silent.
            self._sync_completed = True
            if not self._org_caches:
                LOG.warning(
                    "PolicySync initial load completed with ZERO policy bundles. "
                    "Gateway will allow all requests until backend compiles per-org bundles. "
                    "If this persists, verify backend policy_compiler ran and Redis is reachable."
                )
        except Exception:
            LOG.warning(
                "Failed to load initial policy bundles from Redis. "
                "Will retry when Pub/Sub connects.",
                exc_info=True,
            )

    async def _subscriber_loop(self) -> None:
        """
        Continuously subscribe to the policy_updates Pub/Sub channel.
        On each message, fetch the latest compiled bundle for the affected org.
        Reconnects automatically on connection loss.
        """
        while self._running:
            client = None
            pubsub = None
            try:
                client = aioredis.Redis.from_url(
                    self._redis_url,
                    decode_responses=True,
                    socket_timeout=None,
                    socket_connect_timeout=3.0,
                )
                pubsub = client.pubsub()
                await pubsub.subscribe(PUBSUB_CHANNEL)
                LOG.info("PolicySync subscribed to Pub/Sub channel '%s'", PUBSUB_CHANNEL)

                async for message in pubsub.listen():
                    if not self._running:
                        break

                    if message["type"] != "message":
                        continue

                    try:
                        notification = json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        LOG.warning("Received invalid JSON on policy_updates channel")
                        continue

                    org_slug = notification.get("org_slug", "default")
                    incoming_version = notification.get("version", 0)
                    current_org_version = self._org_versions.get(org_slug, 0)

                    if incoming_version <= current_org_version:
                        LOG.debug(
                            "Skipping stale notification for org '%s' (incoming=%d, current=%d)",
                            org_slug,
                            incoming_version,
                            current_org_version,
                        )
                        continue

                    LOG.info(
                        "Policy update notification (org=%s, version=%d, trigger=%s, changed=%s)",
                        org_slug,
                        incoming_version,
                        notification.get("trigger", "unknown"),
                        notification.get("changed_policy_ids", []),
                    )

                    await self._refresh_cache(client, org_slug)

            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning(
                    "PolicySync subscriber disconnected, reconnecting in %ds",
                    RECONNECT_DELAY_SECONDS,
                    exc_info=True,
                )
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(PUBSUB_CHANNEL)
                        await pubsub.aclose()
                    except Exception:
                        pass
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass

    async def _refresh_cache(self, client: aioredis.Redis, org_slug: str = "default") -> None:
        """Fetch the latest compiled bundle for a specific org from Redis."""
        try:
            redis_key = f"{REDIS_KEY_COMPILED}:{org_slug}"
            raw_bundle = await client.get(redis_key)
            if raw_bundle is None:
                LOG.warning("Policy key '%s' missing during refresh", redis_key)
                return

            bundle = json.loads(raw_bundle)

            # Same fail-closed verification as initial load. Keep the
            # previous cache untouched if the new bundle is tampered.
            if not verify_bundle(bundle):
                if signing_enforced():
                    LOG.critical(
                        "REFUSING tampered policy bundle for org '%s' on refresh; "
                        "retaining previous cache (version=%d)",
                        org_slug,
                        self._org_versions.get(org_slug, 0),
                    )
                    # Phase 0 D-G1-v3: site=refresh so we can alert on
                    # post-startup tampering (more suspicious than init).
                    asyncio.create_task(
                        emit_operational_event(
                            EVENT_CLASS_POLICY_HMAC_FAILURE,
                            org_slug=org_slug,
                            severity="critical",
                            metadata={
                                "site": "refresh",
                                "redis_key": redis_key,
                                "current_version": self._org_versions.get(org_slug, 0),
                            },
                        )
                    )
                    return
                LOG.warning(
                    "Refreshing unsigned policy bundle for org '%s' "
                    "(GATEWAY_POLICY_SIGNING_REQUIRED=false)",
                    org_slug,
                )

            new_version = bundle.get("version", 0)

            self._org_caches[org_slug] = bundle
            self._org_versions[org_slug] = new_version
            if new_version > self._version:
                self._version = new_version

            LOG.info(
                "Policy cache refreshed for org '%s' (version=%d, policies=%d)",
                org_slug,
                new_version,
                bundle.get("policy_count", 0),
            )
        except Exception:
            LOG.exception("Failed to refresh policy cache for org '%s'", org_slug)
