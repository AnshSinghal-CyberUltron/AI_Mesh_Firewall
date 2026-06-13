"""
ConfigSync: subscribes to Redis Pub/Sub ``config_updates`` channel and
maintains a hot-reloadable copy of the firewall configuration in the
Gateway's global CONFIG dict.

Lifecycle:
    1. On startup, loads the current config from Redis (GET firewall:config)
    2. Merges Redis values into the global CONFIG dict (env vars as fallback)
    3. Subscribes to config_updates Pub/Sub channel
    4. On each notification, re-fetches and re-merges the config
    5. proxy_chat() reads from CONFIG which is always up to date
"""

import asyncio
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.config_sync")

REDIS_KEY = "firewall:config"
REDIS_KEY_PREFIX = "firewall:config:"
LLM_MODEL_CONFIGS_REDIS_KEY = "llm:model_configs"
LLM_MODEL_CONFIGS_PREFIX = "llm:model_configs:"
PUBSUB_CHANNEL = "config_updates"
THREAT_INTEL_KEY_PREFIX = "firewall:threat_intel:"
THREAT_INTEL_CHANNEL = "threat_intel_updates"
RECONNECT_DELAY_SECONDS = 5

LOG_LEVEL_MAP: dict[str, int] = {
    "minimal": logging.ERROR,
    "standard": logging.WARNING,
    "detailed": logging.INFO,
    "verbose": logging.DEBUG,
}


class ConfigSync:
    """
    Async Redis Pub/Sub subscriber that keeps the global CONFIG dict
    in sync with the FirewallConfig stored in Redis by the backend.

    Env-var defaults (from load_config()) are preserved as fallbacks.
    Redis values override them on each sync.
    """

    def __init__(self, redis_url: str, config: dict[str, Any]) -> None:
        self._redis_url: str = redis_url
        self._config: dict[str, Any] = config
        self._config_by_org: dict[str, dict[str, Any]] = {}
        self._model_routing_by_org: dict[str, list[dict]] = {}
        self._fallback_chains_by_org: dict[str, dict[str, Any]] = {}
        self._threat_intel_by_org: dict[str, list[dict[str, Any]]] = {}
        self._subscriber_task: Optional[asyncio.Task] = None
        self._threat_intel_task: Optional[asyncio.Task] = None
        self._running: bool = False

    def get_config(self, org_slug: str = "") -> dict[str, Any]:
        """Return org-specific config, falling back to default then global CONFIG."""
        if org_slug and org_slug in self._config_by_org:
            return self._config_by_org[org_slug]
        if "default" in self._config_by_org:
            return self._config_by_org["default"]
        return self._config

    def get_model_routing(self, org_slug: str = "") -> list[dict]:
        """Return org-specific model routing metadata."""
        if org_slug and org_slug in self._model_routing_by_org:
            return self._model_routing_by_org[org_slug]
        return self._model_routing_by_org.get("default", [])

    def get_fallback_chains(self, org_slug: str = "") -> dict[str, Any]:
        """Return precomputed compliant fallback chains for an org."""
        if org_slug and org_slug in self._fallback_chains_by_org:
            return self._fallback_chains_by_org[org_slug]
        return self._fallback_chains_by_org.get("default", {})

    def get_threat_intel(self, org_slug: str = "") -> list[dict[str, Any]]:
        """Return threat intelligence entries for an org (from Module 2 sync)."""
        if org_slug and org_slug in self._threat_intel_by_org:
            return self._threat_intel_by_org[org_slug]
        return self._threat_intel_by_org.get("default", [])

    @property
    def is_loaded(self) -> bool:
        return self._config.get("_config_sync_loaded", False)

    async def start(self) -> None:
        """
        Load the initial config from Redis and start the background
        subscriber for hot-reload notifications.
        """
        self._running = True
        await self._load_initial()
        await self._load_threat_intel_initial()
        self._subscriber_task = asyncio.create_task(self._subscriber_loop())
        self._threat_intel_task = asyncio.create_task(self._threat_intel_subscriber_loop())
        LOG.info(
            "ConfigSync started (loaded=%s, firewall_enabled=%s, enforcement_mode=%s)",
            self.is_loaded,
            self._config.get("firewall_enabled", "N/A"),
            self._config.get("enforcement_mode", "N/A"),
        )

    async def stop(self) -> None:
        """Stop the background subscriber gracefully."""
        self._running = False
        for task in (self._subscriber_task, self._threat_intel_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        LOG.info("ConfigSync stopped")

    async def reload_models_now(self, org_slug: str = "") -> None:
        """
        Force an immediate LLM model reload from Redis.

        Useful at gateway startup so model routes are available before
        the first Pub/Sub model_reload notification is received.
        """
        client = None
        try:
            client = aioredis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=3.0,
                socket_connect_timeout=2.0,
            )
            await self._reload_llm_models(client, org_slug=org_slug)
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    async def _load_initial(self) -> None:
        """
        Load the firewall config from Redis on startup.
        Loads both legacy flat key and per-org keys.
        """
        try:
            client = aioredis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=3.0,
                socket_connect_timeout=2.0,
            )

            raw = await client.get(REDIS_KEY)
            if raw is not None:
                data = json.loads(raw)
                self._apply(data)
                LOG.info("Loaded firewall config from Redis (%d keys)", len(data))

            keys = []
            async for key in client.scan_iter(match=f"{REDIS_KEY_PREFIX}*"):
                keys.append(key)
            for key in keys:
                raw_val = await client.get(key)
                if raw_val:
                    data = json.loads(raw_val)
                    slug = key.replace(REDIS_KEY_PREFIX, "")
                    self._apply(data)
                    self._config_by_org[slug] = {**self._config, **data}
                    LOG.info("Loaded org config for '%s' (%d keys)", slug, len(data))

            model_keys = []
            async for key in client.scan_iter(match=f"{LLM_MODEL_CONFIGS_PREFIX}*"):
                model_keys.append(key)
            for key in model_keys:
                raw_val = await client.get(key)
                if raw_val:
                    data = json.loads(raw_val)
                    slug = key.replace(LLM_MODEL_CONFIGS_PREFIX, "")
                    if isinstance(data, dict) and "routing" in data:
                        self._model_routing_by_org[slug] = data["routing"]
                        if "fallback_chains" in data:
                            self._fallback_chains_by_org[slug] = data["fallback_chains"]

            await client.aclose()
        except Exception:
            LOG.warning(
                "Failed to load firewall config from Redis. Will retry when Pub/Sub connects.",
                exc_info=True,
            )

    async def _load_threat_intel_initial(self) -> None:
        """Load threat intel entries from Redis on startup."""
        try:
            client = aioredis.Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=3.0,
                socket_connect_timeout=2.0,
            )
            async for key in client.scan_iter(match=f"{THREAT_INTEL_KEY_PREFIX}*"):
                raw = await client.get(key)
                if raw:
                    slug = key.replace(THREAT_INTEL_KEY_PREFIX, "")
                    self._threat_intel_by_org[slug] = json.loads(raw)
            await client.aclose()
        except Exception:
            LOG.warning("Failed to load threat intel from Redis", exc_info=True)

    async def _reload_threat_intel(self, client: aioredis.Redis, org_slug: str = "") -> None:
        """Fetch latest threat intel for an org from Redis."""
        try:
            if org_slug:
                key = f"{THREAT_INTEL_KEY_PREFIX}{org_slug}"
                raw = await client.get(key)
                if raw is not None:
                    self._threat_intel_by_org[org_slug] = json.loads(raw)
                    LOG.info("Threat intel hot-reloaded for '%s'", org_slug)
            else:
                async for key in client.scan_iter(match=f"{THREAT_INTEL_KEY_PREFIX}*"):
                    raw = await client.get(key)
                    if raw:
                        slug = key.replace(THREAT_INTEL_KEY_PREFIX, "")
                        self._threat_intel_by_org[slug] = json.loads(raw)
        except Exception:
            LOG.exception("Failed to reload threat intel from Redis")

    async def _threat_intel_subscriber_loop(self) -> None:
        """Subscribe to threat_intel_updates Pub/Sub channel."""
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
                await pubsub.subscribe(THREAT_INTEL_CHANNEL)
                LOG.info("ConfigSync subscribed to '%s'", THREAT_INTEL_CHANNEL)

                async for message in pubsub.listen():
                    if not self._running:
                        break
                    if message["type"] != "message":
                        continue
                    msg_data = message.get("data", "")
                    try:
                        parsed = json.loads(msg_data) if isinstance(msg_data, str) else {}
                    except (json.JSONDecodeError, TypeError):
                        parsed = {}
                    org_slug = parsed.get("org_slug", "")
                    await self._reload_threat_intel(client, org_slug=org_slug)
            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning(
                    "Threat intel subscriber disconnected, reconnecting in %ds",
                    RECONNECT_DELAY_SECONDS,
                    exc_info=True,
                )
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            finally:
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(THREAT_INTEL_CHANNEL)
                        await pubsub.aclose()
                    except Exception:
                        pass
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass

    def _apply(self, data: dict[str, Any]) -> None:
        """
        Merge Redis-sourced config values into the global CONFIG dict.
        Only keys present in the data payload are overridden; env-var
        defaults remain for any missing keys.
        """
        for key, value in data.items():
            self._config[key] = value

        self._config["_config_sync_loaded"] = True

        log_level_name = data.get("log_level")
        if log_level_name and log_level_name in LOG_LEVEL_MAP:
            target_level = LOG_LEVEL_MAP[log_level_name]
            # Only change root logger level (controls console/docker-logs verbosity).
            # Do NOT change gateway or middleware logger levels -- they are pinned
            # at DEBUG to keep the SSE log viewer stream always flowing.
            root_logger = logging.getLogger()
            if root_logger.level != target_level:
                root_logger.setLevel(target_level)
                LOG.log(
                    max(target_level, logging.INFO),
                    "Root log level changed to %s (%s)",
                    log_level_name, logging.getLevelName(target_level),
                )

        LOG.debug(
            "Applied firewall config: firewall_enabled=%s, enforcement_mode=%s, "
            "input_scan_enabled=%s, rate_limit_enabled=%s",
            self._config.get("firewall_enabled"),
            self._config.get("enforcement_mode"),
            self._config.get("input_scan_enabled"),
            self._config.get("rate_limit_enabled"),
        )

    async def _subscriber_loop(self) -> None:
        """
        Continuously subscribe to the config_updates Pub/Sub channel.
        On each message, fetch the latest config from Redis and merge.
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
                LOG.info("ConfigSync subscribed to Pub/Sub channel '%s'", PUBSUB_CHANNEL)

                async for message in pubsub.listen():
                    if not self._running:
                        break

                    if message["type"] != "message":
                        continue

                    LOG.info(
                        "Config update notification received on '%s': %s",
                        PUBSUB_CHANNEL,
                        message.get("data", ""),
                    )

                    msg_data = message.get("data", "")
                    try:
                        parsed = json.loads(msg_data) if isinstance(msg_data, str) else {}
                    except (json.JSONDecodeError, TypeError):
                        parsed = {}
                    action = parsed.get("action", msg_data)
                    org_slug = parsed.get("org_slug", "")

                    if action == "model_reload":
                        await self._reload_llm_models(client, org_slug=org_slug)
                    else:
                        await self._refresh(client, org_slug=org_slug)

            except asyncio.CancelledError:
                break
            except Exception:
                LOG.warning(
                    "ConfigSync subscriber disconnected, reconnecting in %ds",
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

    async def _refresh(self, client: aioredis.Redis, org_slug: str = "") -> None:
        """Fetch the latest firewall config from Redis and apply."""
        try:
            if org_slug:
                key = f"{REDIS_KEY_PREFIX}{org_slug}"
            else:
                key = REDIS_KEY
            raw = await client.get(key)
            if raw is None:
                LOG.warning("%s key missing during refresh; keeping current config", key)
                return

            data = json.loads(raw)
            # Always apply to global CONFIG so components like InputScanner
            # (which hold a reference to the global dict) see the latest values.
            self._apply(data)
            if org_slug:
                self._config_by_org[org_slug] = {**self._config, **data}

            LOG.info(
                "Firewall config hot-reloaded from Redis (%s, %d keys, "
                "firewall_enabled=%s, enforcement_mode=%s)",
                key, len(data),
                data.get("firewall_enabled"),
                data.get("enforcement_mode"),
            )
        except Exception:
            LOG.exception("Failed to refresh firewall config from Redis")

    async def _reload_llm_models(self, client: aioredis.Redis, org_slug: str = "") -> None:
        """
        Fetch updated LLM model configs from Redis and reload the
        LiteLLM Router with the new model list.
        """
        try:
            keys_to_check = []
            if org_slug:
                keys_to_check.append(f"{LLM_MODEL_CONFIGS_PREFIX}{org_slug}")
            else:
                async for key in client.scan_iter(match=f"{LLM_MODEL_CONFIGS_PREFIX}*"):
                    keys_to_check.append(key)
                if not keys_to_check:
                    keys_to_check.append(LLM_MODEL_CONFIGS_REDIS_KEY)

            all_models = []
            for key in keys_to_check:
                raw = await client.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                slug = key.replace(LLM_MODEL_CONFIGS_PREFIX, "").replace(LLM_MODEL_CONFIGS_REDIS_KEY, "default")
                if isinstance(data, dict):
                    model_list = data.get("models", [])
                    routing_list = data.get("routing", [])
                    self._model_routing_by_org[slug] = routing_list
                    if "fallback_chains" in data:
                        self._fallback_chains_by_org[slug] = data["fallback_chains"]
                    all_models.extend(model_list)
                elif isinstance(data, list):
                    all_models.extend(data)

            if not all_models:
                LOG.info("Empty model list from Redis; skipping reload")
                return

            from ai_mesh_gateway import main as gateway_main

            if gateway_main.LLM_ROUTER is not None:
                gateway_main.LLM_ROUTER.reload_models(all_models)
                LOG.info("LLM model configs reloaded from Redis (%d models)", len(all_models))
            else:
                LOG.warning("LLM_ROUTER not initialized; skipping model reload")
        except Exception:
            LOG.exception("Failed to reload LLM model configs from Redis")
