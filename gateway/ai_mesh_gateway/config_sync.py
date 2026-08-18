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
import sys
from typing import Any, Optional

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.config_sync")

REDIS_KEY = "firewall:config"
REDIS_KEY_PREFIX = "firewall:config:"
LLM_MODEL_CONFIGS_REDIS_KEY = "llm:model_configs"
LLM_MODEL_CONFIGS_PREFIX = "llm:model_configs:"
PUBSUB_CHANNEL = "config_updates"
RECONNECT_DELAY_SECONDS = 5

# Sentinel distinguishing "org_slug not supplied" (startup global model load)
# from an explicit empty org_slug ("" — per-request path with no resolved org).
# See ConfigSync.reload_models_now (#5: unknown-model fallback fan-out).
_UNSET = object()

LOG_LEVEL_MAP: dict[str, int] = {
    "minimal": logging.ERROR,
    "standard": logging.WARNING,
    "detailed": logging.INFO,
    "verbose": logging.DEBUG,
}

# ── M-19: defensive schema validation for Redis-sourced config payloads ──
#
# Org-config payloads come from the control plane via Redis. A buggy or
# malicious writer must not be able to poison the gateway's CONFIG dict
# (e.g. ``"firewall_enabled": "false"`` — a truthy string — silently
# flipping enforcement semantics). Validation is fail-open: a malformed
# payload is warned about and SKIPPED, keeping the last-good config.
#
# Known keys mirror control-plane ``FirewallConfig.build_gateway_payload()``
# plus the gateway-local keys from ``config.load_config()``. Unknown keys
# pass through unchanged (forward compatibility with newer control planes).
_BOOL_KEYS = (
    "firewall_enabled", "rate_limit_enabled", "input_scan_enabled",
    "scan_block_on_pii", "scan_block_on_injection", "deep_scan_enabled",
    "tier2_fail_closed_enabled", "tier2_input_fail_closed", "tier2_stream_hold_enabled",
    "tier2_enabled", "mcp_tier2_enabled", "tier2_strict",
    "model_isolation_enabled", "routing_enabled", "output_scan_enabled",
    "hallucination_flag_enabled", "output_pii_enabled",
    "output_credential_enabled", "output_ip_leakage_enabled",
    "output_policy_enabled", "output_incident_logging_enabled",
    "rag_enabled", "vector_db_isolation",
    "rag_redaction_enabled", "rag_tier2_enabled", "threat_intel_enabled",
    "auto_block_threats", "telemetry_enabled",
)
_NUM_KEYS = (  # bools are explicitly excluded in _value_type_ok
    "requests_per_minute", "burst_limit", "toxicity_threshold",
    # RAG-12c: ``prompt_rewrite_threshold``/``prompt_downgrade_threshold`` are
    # already propagated per-request by main.py (:12260) and vector_routes
    # (_RAG_GUARDRAIL_KEYS) and read by the RAG QueryStage, but they were MISSING
    # here — so an org payload carrying them was not recognised as numeric and the
    # pair was not per-org syncable. That left the operator unable to move the
    # rewrite band after raising ``prompt_injection_threshold``, which is what made
    # the non-monotonic block→rewrite flip unfixable from the control plane.
    "prompt_injection_threshold", "prompt_rewrite_threshold",
    "prompt_downgrade_threshold", "tier2_stream_hold_timeout_ms",
    "routing_risk_weight", "routing_cost_weight", "routing_latency_weight",
    "routing_priority_weight", "hallucination_grounding_threshold",
    "max_response_tokens", "rag_default_max_results",
    "rag_relevance_threshold", "threat_score_threshold", "retention_days",
)
_STR_KEYS = (
    "enforcement_mode", "log_level", "tier2_execution_mode",
    "litellm_default_model", "default_data_sensitivity",
    "hallucination_grounding_mode", "hallucination_grounding_model",
    "output_pii_action", "output_credential_action",
    "output_ip_leakage_action", "output_policy_action",
    "output_hallucination_action",
)
_LIST_KEYS = ("blocked_keywords", "allowed_models", "compliance_frameworks")

CONFIG_KEY_TYPES: dict[str, tuple[type, ...]] = {
    **{k: (bool,) for k in _BOOL_KEYS},
    **{k: (int, float) for k in _NUM_KEYS},
    **{k: (str,) for k in _STR_KEYS},
    **{k: (list,) for k in _LIST_KEYS},
}

# Keys where ``null`` is a meaningful tri-state value ("no per-org opinion").
_NULLABLE_KEYS: frozenset[str] = frozenset({"tier2_enabled", "mcp_tier2_enabled"})


def _value_type_ok(key: str, value: Any) -> bool:
    """Return True if *value* has an acceptable type for *key*."""
    expected = CONFIG_KEY_TYPES.get(key)
    if expected is None:
        return True  # unknown key — pass through (forward compatible)
    if value is None:
        return key in _NULLABLE_KEYS
    if isinstance(value, bool):
        # bool is a subclass of int — only accept it where bool is expected.
        return bool in expected
    return isinstance(value, expected)


def validate_config_payload(data: Any, source: str = "") -> Optional[dict[str, Any]]:
    """
    Validate a Redis-sourced org-config payload.

    Returns a sanitized copy, or ``None`` when the payload is not a dict
    (the caller must then keep its last-good config). Individual keys with
    unexpected types are dropped with a warning while the rest of the
    payload still applies.
    """
    if not isinstance(data, dict):
        LOG.warning(
            "Ignoring malformed config payload from %s: expected JSON object, got %s",
            source or "redis",
            type(data).__name__,
        )
        return None

    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            LOG.warning(
                "Dropping non-string config key %r from %s", key, source or "redis"
            )
            continue
        if not _value_type_ok(key, value):
            LOG.warning(
                "Dropping config key '%s' from %s: expected %s, got %s (%r)",
                key,
                source or "redis",
                "/".join(t.__name__ for t in CONFIG_KEY_TYPES.get(key, ())),
                type(value).__name__,
                value,
            )
            continue
        sanitized[key] = value
    return sanitized


def validate_model_config_payload(data: Any, source: str = "") -> Optional[dict[str, Any]]:
    """
    Validate an ``llm:model_configs:*`` payload from Redis.

    Accepts either a bare list of model dicts (legacy) or a dict with
    optional ``models`` / ``routing`` (lists) and ``fallback_chains``
    (dict). Returns a normalized dict containing only the keys that were
    present AND well-typed, or ``None`` when the payload shape is
    unusable. Malformed sub-sections are dropped with a warning so a
    partially-bad payload cannot wipe last-good routing state.
    """
    def _only_dicts(items: list, list_key: str) -> list:
        kept = [item for item in items if isinstance(item, dict)]
        if len(kept) != len(items):
            LOG.warning(
                "Dropped %d non-object entries from '%s' in model config %s",
                len(items) - len(kept), list_key, source or "redis",
            )
        return kept

    if isinstance(data, list):
        return {"models": _only_dicts(data, "models")}
    if not isinstance(data, dict):
        LOG.warning(
            "Ignoring malformed model config payload from %s: expected object or list, got %s",
            source or "redis",
            type(data).__name__,
        )
        return None

    normalized: dict[str, Any] = {}
    for list_key in ("models", "routing"):
        if list_key in data:
            value = data[list_key]
            if isinstance(value, list):
                normalized[list_key] = _only_dicts(value, list_key)
            else:
                LOG.warning(
                    "Dropping model config key '%s' from %s: expected list, got %s",
                    list_key, source or "redis", type(value).__name__,
                )
    if "fallback_chains" in data:
        value = data["fallback_chains"]
        if isinstance(value, dict):
            normalized["fallback_chains"] = value
        else:
            LOG.warning(
                "Dropping model config key 'fallback_chains' from %s: expected dict, got %s",
                source or "redis", type(value).__name__,
            )
    return normalized


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
        self._subscriber_task: Optional[asyncio.Task] = None
        self._running: bool = False
        # Long-lived Redis for hot-path reload_models_now (no from_url per chat).
        self._redis: Optional[aioredis.Redis] = None
        self._reload_locks: dict[str, asyncio.Lock] = {}

    def get_config(self, org_slug: str = "") -> dict[str, Any]:
        """Return org-specific config, falling back to default then global CONFIG."""
        if org_slug and org_slug in self._config_by_org:
            return self._config_by_org[org_slug]
        if "default" in self._config_by_org:
            return self._config_by_org["default"]
        return self._config

    def get_own_config(self, org_slug: str) -> Optional[dict[str, Any]]:
        """Return ONLY this org's own synced config, or ``None`` if it has not synced.

        Unlike :meth:`get_config`, this NEVER falls back to the ``default``/global
        config. A security-sensitive per-org SELECTION (e.g. the transparent MCP
        ext-proxy posture) must not be inherited from another org's config: an
        org that has not chosen a posture must resolve to its own safe default
        (observe-only), never silently adopt the platform/default org's enforcing
        action. Callers that need the lenient inheriting lookup keep using
        :meth:`get_config`.
        """
        if org_slug and org_slug in self._config_by_org:
            return self._config_by_org[org_slug]
        return None

    @staticmethod
    def _copy_routing_entries(entries: list) -> list[dict]:
        """Shallow-copy the routing list (and dict items) so callers cannot mutate store."""
        return [dict(x) if isinstance(x, dict) else x for x in entries]

    def get_model_routing(self, org_slug: str = "") -> list[dict]:
        """Return org-specific model routing metadata (a copy; store is last-good)."""
        if org_slug and org_slug in self._model_routing_by_org:
            src = self._model_routing_by_org[org_slug]
        else:
            src = self._model_routing_by_org.get("default", [])
        return self._copy_routing_entries(src)

    def get_fallback_chains(self, org_slug: str = "") -> dict[str, Any]:
        """Return precomputed compliant fallback chains for an org."""
        if org_slug and org_slug in self._fallback_chains_by_org:
            return self._fallback_chains_by_org[org_slug]
        return self._fallback_chains_by_org.get("default", {})

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
        self._subscriber_task = asyncio.create_task(self._subscriber_loop())
        LOG.info(
            "ConfigSync started (loaded=%s, firewall_enabled=%s, enforcement_mode=%s)",
            self.is_loaded,
            self._config.get("firewall_enabled", "N/A"),
            self._config.get("enforcement_mode", "N/A"),
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
        LOG.info("ConfigSync stopped")

    async def reload_models_now(self, org_slug: Any = _UNSET) -> None:
        """
        Force an immediate LLM model reload from Redis.

        Useful at gateway startup so model routes are available before
        the first Pub/Sub model_reload notification is received.

        Org-scoping (#5 — unknown-model fallback fan-out):
            * Called with NO argument (startup, ``reload_models_now()``) the
              ``org_slug`` sentinel triggers the legitimate global load that
              scans ``llm:model_configs:*`` and primes the router with every
              org's models — this only happens once, at boot.
            * Called from the per-request path with an explicit ``org_slug``
              that is EMPTY (``""`` — auth produced no resolved org) we must
              NOT fall through to that cross-org scan-and-merge. Merging every
              org's configs into the live router rebuilds an all-orgs fallback
              graph; an unknown/garbage model would then fan out across it.
              An empty per-request org is treated as "nothing to reload" so
              the request continues against the already-loaded routes and the
              unknown-model rejection happens downstream (see note below).

        NOTE: the actual unknown/unregistered-model rejection does NOT live
        here. ``reload_models_now`` only populates routing/fallback state from
        Redis; it never resolves the request's requested model. The early
        4xx "model not available" rejection belongs in the request path
        (``ai_mesh_gateway.main.proxy_chat`` after the
        ``_filter_inference_eligible_models`` lookup) and/or
        ``llm_router.LLMRouter._resolve_runtime_model`` /
        ``acompletion`` — those are the only places that see the requested
        model and the org's eligible routes. This method's contribution to
        the fix is the org-scoping above, which removes the cross-org
        fallback-graph amplifier for an unknown model on the request path.
        """
        # Per-request path passed an explicit but empty org → do not scan and
        # merge every org's model configs (the fan-out amplifier). The global
        # scan is reserved for the no-argument startup load.
        if org_slug is not _UNSET and not org_slug:
            return
        effective_org = "" if org_slug is _UNSET else org_slug
        lock = self._reload_lock_for(str(effective_org or ""))
        async with lock:
            try:
                client = self._get_redis_client()
                await self._reload_llm_models(client, org_slug=effective_org)
            except Exception:
                # GET / connect timeout must keep last-good catalog (do not write []).
                LOG.warning(
                    "reload_models_now failed for org=%s; keeping last-good catalog",
                    effective_org or "*",
                    exc_info=True,
                )

    def _reload_lock_for(self, org_key: str) -> asyncio.Lock:
        lock = self._reload_locks.get(org_key)
        if lock is None:
            lock = asyncio.Lock()
            self._reload_locks[org_key] = lock
        return lock

    def _get_redis_client(self) -> aioredis.Redis:
        """Reuse a long-lived client. Never from_url per chat."""
        if self._redis is not None:
            return self._redis
        shared = self._shared_gateway_redis()
        if shared is not None:
            self._redis = shared
            return self._redis
        self._redis = aioredis.Redis.from_url(
            self._redis_url,
            decode_responses=True,
            socket_timeout=3.0,
            socket_connect_timeout=2.0,
        )
        return self._redis

    @staticmethod
    def _shared_gateway_redis() -> Optional[aioredis.Redis]:
        # Reuse the process client only when main is already imported (no cycle).
        gateway_main = sys.modules.get("ai_mesh_gateway.main")
        if gateway_main is None:
            return None
        return getattr(gateway_main, "REDIS_CLIENT", None) or None

    @staticmethod
    def _merge_org_into_router(llm_router: Any, slug: str, org_models: list) -> list:
        """Keep other orgs' LiteLLM deployments; replace this org's.

        Per-org ``reload_models_now(org_slug=…)`` must not rebuild the router
        from only that tenant's models (that dropped every other org → 422
        ``no_provider_configured`` under concurrent chat).
        """
        existing: list = []
        inner = getattr(llm_router, "_router", None)
        model_list = getattr(inner, "model_list", None) if inner is not None else None
        if isinstance(model_list, list):
            for entry in model_list:
                if isinstance(entry, dict) and entry.get("_zs_org") != slug:
                    existing.append(dict(entry))
        return existing + list(org_models)

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
                # M-19: per-entry parse + schema validation. A malformed
                # payload is warned about and skipped (fail-open on the
                # env-var defaults / last-good config).
                data = self._parse_and_validate(raw, REDIS_KEY)
                if data is not None:
                    self._apply(data)
                    LOG.info("Loaded firewall config from Redis (%d keys)", len(data))

            keys = []
            async for key in client.scan_iter(match=f"{REDIS_KEY_PREFIX}*"):
                keys.append(key)
            for key in keys:
                raw_val = await client.get(key)
                if raw_val:
                    data = self._parse_and_validate(raw_val, key)
                    if data is None:
                        continue  # warn+skip; other org keys still load
                    slug = key.replace(REDIS_KEY_PREFIX, "")
                    self._config_by_org[slug] = {**self._config, **data}
                    LOG.info("Loaded org config for '%s' (%d keys)", slug, len(data))

            model_keys = []
            async for key in client.scan_iter(match=f"{LLM_MODEL_CONFIGS_PREFIX}*"):
                model_keys.append(key)
            for key in model_keys:
                raw_val = await client.get(key)
                if raw_val:
                    try:
                        data = json.loads(raw_val)
                    except (json.JSONDecodeError, TypeError):
                        LOG.warning("Invalid JSON in model config key '%s'; skipping", key)
                        continue
                    normalized = validate_model_config_payload(data, source=key)
                    if normalized is None:
                        continue
                    slug = key.replace(LLM_MODEL_CONFIGS_PREFIX, "")
                    if "routing" in normalized:
                        self._model_routing_by_org[slug] = normalized["routing"]
                        if "fallback_chains" in normalized:
                            self._fallback_chains_by_org[slug] = normalized["fallback_chains"]

            await client.aclose()
        except Exception:
            LOG.warning(
                "Failed to load firewall config from Redis. Will retry when Pub/Sub connects.",
                exc_info=True,
            )

    def _parse_and_validate(self, raw: str, source: str) -> Optional[dict[str, Any]]:
        """
        Parse a raw Redis value and run schema validation (M-19).

        Returns the sanitized payload dict, or ``None`` when the entry is
        malformed (invalid JSON or non-object payload). Callers treat
        ``None`` as warn+skip and keep the last-good config (fail-open).
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            LOG.warning(
                "Invalid JSON in config key '%s'; keeping last-good config", source
            )
            return None
        return validate_config_payload(data, source=source)

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
                # The GLOBAL `firewall:config` key was retired in favour of per-org
                # `firewall:config:{slug}` keys (the control plane publishes only
                # per-org configs now), so its absence is the expected steady state,
                # not an error — logging it at WARNING floods the logs on every
                # refresh. A missing PER-ORG key is still noteworthy.
                if key == REDIS_KEY:
                    LOG.debug(
                        "%s absent (retired global key); per-org configs carry the config",
                        key,
                    )
                else:
                    LOG.warning("%s key missing during refresh; keeping current config", key)
                return

            # M-19: validate before applying — a malformed payload must not
            # clobber the last-good config (fail-open).
            data = self._parse_and_validate(raw, key)
            if data is None:
                LOG.warning(
                    "Malformed config payload at '%s' during refresh; "
                    "keeping current config",
                    key,
                )
                return
            # Per-org refreshes must NOT mutate the global CONFIG dict — that would
            # bleed one tenant's enforcement settings into every other tenant's
            # fallback path (components that read CONFIG directly, or requests
            # whose org_slug has no dedicated cache entry yet).
            if org_slug:
                self._config_by_org[org_slug] = {**self._config, **data}
            else:
                self._apply(data)

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
                # M-19: per-key parse + validation — one malformed payload
                # must not abort the reload of the remaining org keys, and
                # must not clobber last-good routing/fallback state.
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    LOG.warning("Invalid JSON in model config key '%s'; skipping", key)
                    continue
                normalized = validate_model_config_payload(data, source=key)
                if normalized is None:
                    continue
                slug = key.replace(LLM_MODEL_CONFIGS_PREFIX, "").replace(LLM_MODEL_CONFIGS_REDIS_KEY, "default")
                if isinstance(data, dict):
                    if "routing" in data and "routing" not in normalized:
                        # Malformed routing section — keep last-good routing.
                        pass
                    else:
                        incoming_routing = normalized.get("routing") or []
                        last_good = self._model_routing_by_org.get(slug)
                        if not incoming_routing and last_good:
                            # Models-only / empty routing must not clobber last-good.
                            LOG.warning(
                                "Empty routing in '%s'; keeping last-good catalog (%d entries)",
                                key,
                                len(last_good),
                            )
                        else:
                            self._model_routing_by_org[slug] = incoming_routing
                    if "fallback_chains" in normalized:
                        self._fallback_chains_by_org[slug] = normalized["fallback_chains"]
                # H7: tag each deployment with its OWNING org so the router can
                # org-qualify the routing key — without this tag the global merge
                # keys deployments by bare model_name only, letting two tenants'
                # same-named models (and their distinct BYOK keys) load-balance.
                _slug_models = normalized.get("models", [])
                for _m in _slug_models:
                    if isinstance(_m, dict):
                        _m["_zs_org"] = slug
                all_models.extend(_slug_models)

            if not all_models:
                LOG.info("Empty model list from Redis; skipping reload")
                return

            from ai_mesh_gateway import main as gateway_main

            if gateway_main.LLM_ROUTER is not None:
                to_load = all_models
                if org_slug:
                    to_load = self._merge_org_into_router(
                        gateway_main.LLM_ROUTER, str(org_slug), all_models
                    )
                gateway_main.LLM_ROUTER.reload_models(to_load)
                LOG.info("LLM model configs reloaded from Redis (%d models)", len(to_load))
            else:
                LOG.warning("LLM_ROUTER not initialized; skipping model reload")
        except Exception:
            LOG.exception("Failed to reload LLM model configs from Redis")
