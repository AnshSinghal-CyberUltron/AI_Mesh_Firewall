"""
Configuration for the AIGuardX Gateway Proxy (Phase 3).
"""
import logging
import os

logger = logging.getLogger(__name__)


def get_env(key, default=None):
    return os.environ.get(key, default)


def _env_int(key, default, *, min_value=None, max_value=None):
    """
    M-09: Safely parse an int env var.

    A typo'd value (e.g. GATEWAY_PORT="8300x") previously crashed the whole
    gateway at import/startup via a bare int(). This falls back to ``default``
    and logs a warning instead, then clamps to [min_value, max_value] so a
    pathological-but-parseable value (e.g. a 2GB stream buffer) can't take the
    process down either. ``default`` is returned unchanged for the common
    valid-input case, preserving existing behavior.
    """
    raw = get_env(key, None)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            logger.warning(
                "Invalid int for env %s=%r; falling back to default %r",
                key, raw, default,
            )
            value = default
    if min_value is not None and value < min_value:
        logger.warning("Env %s=%r below min %r; clamping", key, value, min_value)
        value = min_value
    if max_value is not None and value > max_value:
        logger.warning("Env %s=%r above max %r; clamping", key, value, max_value)
        value = max_value
    return value


def _env_float(key, default, *, min_value=None, max_value=None):
    """
    M-09: Safely parse a float env var (see ``_env_int`` for rationale).
    Falls back to ``default`` + logs on a bad value, then clamps to bounds.
    """
    raw = get_env(key, None)
    if raw is None or str(raw).strip() == "":
        value = default
    else:
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            logger.warning(
                "Invalid float for env %s=%r; falling back to default %r",
                key, raw, default,
            )
            value = default
    if min_value is not None and value < min_value:
        logger.warning("Env %s=%r below min %r; clamping", key, value, min_value)
        value = min_value
    if max_value is not None and value > max_value:
        logger.warning("Env %s=%r above max %r; clamping", key, value, max_value)
        value = max_value
    return value


def is_http_allowed_for_url(url, allow_env_var="GATEWAY_ALLOW_HTTP"):
    """
    Return True if HTTP is allowed for this URL (localhost/dev only).
    Production must use HTTPS unless allow_env_var is set (e.g. GATEWAY_ALLOW_HTTP=1).
    """
    if not url:
        return True
    url_lower = url.lower().strip()
    if url_lower.startswith("https://"):
        return True
    if url_lower.startswith("http://"):
        if get_env(allow_env_var, "").strip().lower() in ("1", "true", "yes"):
            return True
        rest = url_lower[7:].split("/")[0].split(":")[0]
        if rest in ("127.0.0.1", "localhost", "::1"):
            return True
        return False
    return True


def load_config():
    backend_url = get_env("AIGUARDX_BACKEND_URL", "").rstrip("/")
    name = get_env("AIGUARDX_GATEWAY_NAME", "Gateway Proxy")
    location = get_env("AIGUARDX_GATEWAY_LOCATION", "")
    api_key = get_env("AIGUARDX_API_KEY", "")
    upstream_url = get_env("GATEWAY_UPSTREAM_LLM_URL", "").rstrip("/")
    llm_api_key = get_env("GATEWAY_LLM_API_KEY", "")
    port = _env_int("GATEWAY_PORT", 8300, min_value=1, max_value=65535)
    stats_interval = _env_int("GATEWAY_STATS_INTERVAL_SEC", 120)
    call_security_scan = get_env("GATEWAY_CALL_SECURITY_SCAN", "false").lower() in ("true", "1", "yes")
    redis_url = get_env("GATEWAY_REDIS_URL", "redis://localhost:6379/0")
    auth_enabled = get_env("GATEWAY_AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
    policy_cache_enabled = get_env("GATEWAY_POLICY_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
    policy_cache_require_loaded = get_env("GATEWAY_POLICY_CACHE_REQUIRE_LOADED", "true").lower() in ("true", "1", "yes")
    # policy-driven-detection task 6.1: the legacy default-on scan toggles
    # (``input_scan_enabled`` / ``output_scan_enabled`` / ``scan_block_on_injection``
    # / ``scan_block_on_pii``) are REMOVED as detection drivers. Tier-1 detection is
    # now driven SOLELY by the org's enabled policy set (Requirement 5.1/5.2), and
    # ``firewall_enabled`` remains a suppression-only master bypass (it may never
    # CAUSE detection). These keys are intentionally NOT emitted into the gateway
    # config; a stale value arriving from an old control plane is ignored for the
    # purpose of enabling detection (Requirement 5.4), not treated as an error.
    # E12: OPT-IN hard-block of a credential/secret detected in MCP tool ARGUMENTS
    # (outbound to the MCP server). STRICTLY-WHAT-THE-OPERATOR-SELECTED (2026-07-22,
    # commit 005a6ffa): DEFAULT OFF. This was a built-in floor that ESCALATED a
    # detected-credential redact -> BLOCK regardless of the operator's selected
    # posture, so a server on "redact" HARD-BLOCKED the call (HTTP 400) instead of
    # masking the credential and forwarding — which is not "redact". That commit
    # made ``_mcp_block_on_credential_enabled`` default OFF (docstring + env fallback),
    # but THIS CONFIG default (read FIRST by that helper) was left at "true" and won
    # at runtime, so the escalation stayed live in every real deployment. Under
    # "redact" the credential is now masked in place (AKIA****MPLE) and forwarded; an
    # operator who wants a credential to hard-BLOCK selects the ``block`` posture (or a
    # block policy rule). Still opt-in via GATEWAY_MCP_BLOCK_ON_CREDENTIAL / CONFIG for
    # anyone who wants the belt-and-suspenders. A "monitor" posture always wins.
    mcp_block_on_credential = get_env("GATEWAY_MCP_BLOCK_ON_CREDENTIAL", "false").lower() in ("true", "1", "yes")
    # E12: force-REDACT an MCP tool RESULT (outbound back to the LLM/client) when
    # the output scan DETECTS a secret/credential or PII but the resolved
    # scan_action defaults to "tag"/"monitor" (detect-but-allow).
    # policy-driven-detection R1/R5/R6 (task 7.4): this was a DEFAULT-ON detection
    # DRIVER — it forced a mask on a tool RESULT under the safe ``tag`` default with
    # NO enabled policy authored for it (built-in mandatory detection). Under the
    # policy-driven model detection is enabled-policy-only, so this floor is now
    # EFFECTIVE-DEFAULT OFF: an MCP result is redacted only when a matched ENABLED
    # policy Rule's action is ``redact`` (orchestrator POLICY lane) — this floor no
    # longer forces default detection. An operator can opt it back on per deploy/org.
    mcp_redact_result_on_detect = get_env("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "false").lower() in ("true", "1", "yes")
    tier2_fail_closed_enabled = get_env("GATEWAY_TIER2_FAIL_CLOSED_ENABLED", "true").lower() in ("true", "1", "yes")
    # When a Tier-2 INPUT scan returns a degraded/unparseable verdict (the case
    # reached by prompts that evade Tier-1 signatures), block instead of
    # fail-open 'flag'. Default OFF preserves availability; enable per-deployment
    # or per-org for a stricter input posture. Output scanning stays fail-open.
    tier2_input_fail_closed = get_env("GATEWAY_TIER2_INPUT_FAIL_CLOSED", "false").lower() in ("true", "1", "yes")
    tier2_execution_mode = get_env("GATEWAY_TIER2_EXECUTION_MODE", "sync_pre_llm").strip().lower()
    tier2_stream_hold_enabled = get_env("GATEWAY_TIER2_STREAM_HOLD_ENABLED", "false").lower() in ("true", "1", "yes")
    # M-09: clamp hold/finalize timeouts to a sane window so a bad value can't
    # hang a request indefinitely (or fire instantly with 0).
    tier2_stream_hold_timeout_ms = _env_int(
        "GATEWAY_TIER2_STREAM_HOLD_TIMEOUT_MS", 1200, min_value=0, max_value=60000)
    stream_preflight_fail_closed = get_env("GATEWAY_STREAM_PREFLIGHT_FAIL_CLOSED", "true").lower() in ("true", "1", "yes")
    # M-09: bound stream buffers so a typo can't request a multi-GB allocation.
    stream_max_buffer_bytes = _env_int(
        "GATEWAY_STREAM_MAX_BUFFER_BYTES", 4096, min_value=64, max_value=10_485_760)
    stream_max_buffer_chunks = _env_int(
        "GATEWAY_STREAM_MAX_BUFFER_CHUNKS", 64, min_value=1, max_value=10000)
    stream_emit_debug_headers = get_env("GATEWAY_STREAM_EMIT_DEBUG_HEADERS", "false").lower() in ("true", "1", "yes")
    stream_finalize_timeout_ms = _env_int(
        "GATEWAY_STREAM_FINALIZE_TIMEOUT_MS", 5000, min_value=0, max_value=120000)
    scan_buffer_max_bytes = _env_int(
        "GATEWAY_SCAN_BUFFER_MAX_BYTES", 4096, min_value=64, max_value=10_485_760)
    scan_thread_pool_size = _env_int(
        "GATEWAY_SCAN_THREAD_POOL_SIZE", 4, min_value=1, max_value=256)
    deep_scan_enabled = get_env("GATEWAY_DEEP_SCAN_ENABLED", "false").lower() in ("true", "1", "yes")
    org_only_inference = get_env("GATEWAY_ORG_ONLY_INFERENCE", "true").lower() in ("true", "1", "yes")
    litellm_default_model = (get_env("LITELLM_DEFAULT_MODEL", "") or "").strip()
    litellm_fallback_models = get_env("LITELLM_FALLBACK_MODELS", "gpt-4o-mini")
    # M-09: clamp request timeout/retries to keep upstream calls from hanging
    # forever or retrying an unbounded number of times.
    litellm_request_timeout = _env_int(
        "LITELLM_REQUEST_TIMEOUT", 120, min_value=1, max_value=3600)
    litellm_num_retries = _env_int(
        "LITELLM_NUM_RETRIES", 2, min_value=0, max_value=10)
    litellm_drop_params = get_env("LITELLM_DROP_PARAMS", "true").lower() in ("true", "1", "yes")
    rag_enabled = get_env("GATEWAY_RAG_ENABLED", "false").lower() in ("true", "1", "yes")
    # Guardrails-only RAG: the reranker + generator pipeline stages are the
    # client's own RAG-app responsibility (the gateway provides retrieval
    # guardrails only). Default OFF — the query pipeline returns retriever
    # documents directly; the client assembles context and calls the generator
    # model through the normal chat pipeline (Tier-1/Tier-2 + output guard).
    rag_ranker_enabled = get_env("GATEWAY_RAG_RANKER_ENABLED", "false").lower() in ("true", "1", "yes")
    rag_generator_enabled = get_env("GATEWAY_RAG_GENERATOR_ENABLED", "false").lower() in ("true", "1", "yes")
    pinecone_api_key = get_env("GATEWAY_PINECONE_API_KEY", "")
    pinecone_environment = get_env("GATEWAY_PINECONE_ENVIRONMENT", "")
    milvus_uri = get_env("GATEWAY_MILVUS_URI", "")
    milvus_token = get_env("GATEWAY_MILVUS_TOKEN", "")
    # Phase 1 F-3.1: local Chroma backend for the RAG Collection Manager.
    chroma_url = get_env("CHROMA_URL", "") or get_env("GATEWAY_CHROMA_URL", "")
    chroma_auth_token = get_env("GATEWAY_CHROMA_AUTH_TOKEN", "")
    vault_db_dsn = get_env("GATEWAY_VAULT_DB_DSN", "") or get_env("DATABASE_URL", "")
    rag_context_scan_enabled = get_env("GATEWAY_RAG_CONTEXT_SCAN_ENABLED", "true").lower() in ("true", "1", "yes")
    rag_anomaly_detection_enabled = get_env("GATEWAY_RAG_ANOMALY_DETECTION_ENABLED", "true").lower() in ("true", "1", "yes")
    rag_max_query_length = _env_int(
        "GATEWAY_RAG_MAX_QUERY_LENGTH", 2000, min_value=1, max_value=1_000_000)
    rag_default_max_results = _env_int(
        "GATEWAY_RAG_DEFAULT_MAX_RESULTS", 10, min_value=1, max_value=1000)
    default_max_context_tokens = _env_int(
        "GATEWAY_DEFAULT_MAX_CONTEXT_TOKENS", 0, min_value=0)  # 0=unlimited
    kill_switch_enabled = get_env("GATEWAY_KILL_SWITCH_ENABLED", "true").lower() in ("true", "1", "yes")
    telemetry_enabled = get_env("GATEWAY_TELEMETRY_ENABLED", "true").lower() in ("true", "1", "yes")
    telemetry_flush_interval = _env_float(
        "GATEWAY_TELEMETRY_FLUSH_INTERVAL", 2.0, min_value=0.1, max_value=3600.0)
    telemetry_buffer_size = _env_int(
        "GATEWAY_TELEMETRY_BUFFER_SIZE", 100, min_value=1, max_value=1_000_000)
    output_guard_enabled = get_env("GATEWAY_OUTPUT_GUARD_ENABLED", "true").lower() in ("true", "1", "yes")
    output_grounding_enabled = get_env("GATEWAY_OUTPUT_GROUNDING_ENABLED", "true").lower() in ("true", "1", "yes")
    output_block_on_credential = get_env("GATEWAY_OUTPUT_BLOCK_ON_CREDENTIAL", "true").lower() in ("true", "1", "yes")
    output_block_on_ip_leakage = get_env("GATEWAY_OUTPUT_BLOCK_ON_IP_LEAKAGE", "false").lower() in ("true", "1", "yes")
    hallucination_flag_enabled = get_env("GATEWAY_HALLUCINATION_FLAG_ENABLED", "true").lower() in ("true", "1", "yes")

    # Smart routing weights (each clamped to [0,1]; sum validated below)
    routing_risk_weight = _env_float(
        "GATEWAY_ROUTING_RISK_WEIGHT", 0.30, min_value=0.0, max_value=1.0)
    routing_cost_weight = _env_float(
        "GATEWAY_ROUTING_COST_WEIGHT", 0.20, min_value=0.0, max_value=1.0)
    routing_latency_weight = _env_float(
        "GATEWAY_ROUTING_LATENCY_WEIGHT", 0.20, min_value=0.0, max_value=1.0)
    routing_priority_weight = _env_float(
        "GATEWAY_ROUTING_PRIORITY_WEIGHT", 0.30, min_value=0.0, max_value=1.0)

    # M-09: routing weights must sum to ~1.0 for the scoring math to be balanced.
    # If a misconfigured set doesn't (and isn't all-zero), warn and normalize so
    # routing stays well-behaved instead of silently skewing toward one factor.
    _routing_sum = (
        routing_risk_weight + routing_cost_weight
        + routing_latency_weight + routing_priority_weight
    )
    if _routing_sum <= 0:
        logger.warning(
            "Routing weights sum to %r (<=0); restoring defaults", _routing_sum)
        routing_risk_weight, routing_cost_weight = 0.30, 0.20
        routing_latency_weight, routing_priority_weight = 0.20, 0.30
    elif abs(_routing_sum - 1.0) > 0.01:
        logger.warning(
            "Routing weights sum to %r (expected 1.0); normalizing", _routing_sum)
        routing_risk_weight /= _routing_sum
        routing_cost_weight /= _routing_sum
        routing_latency_weight /= _routing_sum
        routing_priority_weight /= _routing_sum

    # Circuit breaker
    circuit_breaker_enabled = get_env("GATEWAY_CIRCUIT_BREAKER_ENABLED", "true").lower() in ("true", "1", "yes")
    circuit_breaker_error_threshold = _env_float(
        "GATEWAY_CIRCUIT_BREAKER_ERROR_THRESHOLD", 0.5, min_value=0.0, max_value=1.0)
    circuit_breaker_min_requests = _env_int(
        "GATEWAY_CIRCUIT_BREAKER_MIN_REQUESTS", 10, min_value=1, max_value=1_000_000)
    circuit_breaker_cooldown_seconds = _env_int(
        "GATEWAY_CIRCUIT_BREAKER_COOLDOWN_SECONDS", 120, min_value=0, max_value=86400)
    semantic_leakage_enabled = get_env("GATEWAY_SEMANTIC_LEAKAGE_ENABLED", "false").lower() in ("true", "1", "yes")

    # RAG Pipeline (4-stage governed pipeline)
    rag_rate_limit_rpm = _env_int(
        "GATEWAY_RAG_RATE_LIMIT_RPM", 0, min_value=0)  # 0=unlimited
    rag_circuit_breaker_enabled = get_env("GATEWAY_RAG_CIRCUIT_BREAKER_ENABLED", "true").lower() in ("true", "1", "yes")
    rag_context_binding_enabled = get_env("GATEWAY_RAG_CONTEXT_BINDING_ENABLED", "true").lower() in ("true", "1", "yes")
    rag_context_binding_ttl = _env_int(
        "GATEWAY_RAG_CONTEXT_BINDING_TTL", 300, min_value=0, max_value=86400)
    # Default OFF (0.0). This filter was a dead no-op for a long time (it read a
    # `score` key the clients didn't emit and fell back to 1.0), so the old 0.75
    # default never actually applied. Now that the filter is functional, a fixed
    # non-zero default is unsafe ACROSS embedding models: e5 scores relevant docs
    # ~0.8+ but OpenAI/text-embedding-3-small scores them ~0.3-0.5, so 0.75 would
    # silently drop ALL results for OpenAI-embedding orgs. There is no single
    # threshold that fits every model — operators opt in to a model-appropriate
    # value per-org (rag_relevance_threshold). Degenerate matches from embedding
    # failure are already prevented upstream (fail-closed embed). (H6)
    rag_relevance_threshold = _env_float(
        "GATEWAY_RAG_RELEVANCE_THRESHOLD", 0.0, min_value=0.0, max_value=1.0)
    prompt_rewrite_threshold = _env_float(
        "GATEWAY_PROMPT_REWRITE_THRESHOLD", 0.50, min_value=0.0, max_value=1.0)

    return {
        "backend_url": backend_url,
        "gateway_name": name,
        "gateway_location": location,
        "api_key": api_key,
        "upstream_llm_url": upstream_url,
        "llm_api_key": llm_api_key,
        "port": port,
        "stats_interval_sec": max(60, min(600, stats_interval)),
        "call_security_scan": call_security_scan,
        "allow_http_backend": is_http_allowed_for_url(backend_url, "GATEWAY_ALLOW_HTTP"),
        "allow_http_llm": is_http_allowed_for_url(upstream_url, "GATEWAY_ALLOW_HTTP_LLM"),
        "redis_url": redis_url,
        "auth_enabled": auth_enabled,
        "policy_cache_enabled": policy_cache_enabled,
        "policy_cache_require_loaded": policy_cache_require_loaded,
        # policy-driven-detection task 6.1: legacy scan toggles removed as
        # detection drivers (input_scan_enabled / output_scan_enabled /
        # scan_block_on_injection / scan_block_on_pii). Not emitted here.
        "mcp_block_on_credential": mcp_block_on_credential,
        "mcp_redact_result_on_detect": mcp_redact_result_on_detect,
        "tier2_fail_closed_enabled": tier2_fail_closed_enabled,
        "tier2_input_fail_closed": tier2_input_fail_closed,
        "tier2_execution_mode": tier2_execution_mode,
        "tier2_stream_hold_enabled": tier2_stream_hold_enabled,
        "tier2_stream_hold_timeout_ms": tier2_stream_hold_timeout_ms,
        "stream_preflight_fail_closed": stream_preflight_fail_closed,
        "stream_max_buffer_bytes": stream_max_buffer_bytes,
        "stream_max_buffer_chunks": stream_max_buffer_chunks,
        "stream_emit_debug_headers": stream_emit_debug_headers,
        "stream_finalize_timeout_ms": stream_finalize_timeout_ms,
        "scan_buffer_max_bytes": scan_buffer_max_bytes,
        "scan_thread_pool_size": scan_thread_pool_size,
        "deep_scan_enabled": deep_scan_enabled,
        "org_only_inference": org_only_inference,
        "litellm_default_model": litellm_default_model,
        "litellm_fallback_models": [m.strip() for m in litellm_fallback_models.split(",") if m.strip()] if litellm_fallback_models else None,
        "litellm_request_timeout": litellm_request_timeout,
        "litellm_num_retries": litellm_num_retries,
        "litellm_drop_params": litellm_drop_params,
        "rag_enabled": rag_enabled,
        "pinecone_api_key": pinecone_api_key,
        "pinecone_environment": pinecone_environment,
        "milvus_uri": milvus_uri,
        "milvus_token": milvus_token,
        "chroma_url": chroma_url,
        "chroma_auth_token": chroma_auth_token,
        "vault_db_dsn": vault_db_dsn,
        "rag_context_scan_enabled": rag_context_scan_enabled,
        "rag_anomaly_detection_enabled": rag_anomaly_detection_enabled,
        "rag_max_query_length": rag_max_query_length,
        "rag_default_max_results": rag_default_max_results,
        "rag_ranker_enabled": rag_ranker_enabled,
        "rag_generator_enabled": rag_generator_enabled,
        "default_max_context_tokens": default_max_context_tokens,
        "kill_switch_enabled": kill_switch_enabled,
        "telemetry_enabled": telemetry_enabled,
        "telemetry_flush_interval": telemetry_flush_interval,
        "telemetry_buffer_size": telemetry_buffer_size,
        "output_guard_enabled": output_guard_enabled,
        "output_grounding_enabled": output_grounding_enabled,
        "output_block_on_credential": output_block_on_credential,
        "output_block_on_ip_leakage": output_block_on_ip_leakage,
        "hallucination_flag_enabled": hallucination_flag_enabled,
        "routing_risk_weight": routing_risk_weight,
        "routing_cost_weight": routing_cost_weight,
        "routing_latency_weight": routing_latency_weight,
        "routing_priority_weight": routing_priority_weight,
        "circuit_breaker_enabled": circuit_breaker_enabled,
        "circuit_breaker_error_threshold": circuit_breaker_error_threshold,
        "circuit_breaker_min_requests": circuit_breaker_min_requests,
        "circuit_breaker_cooldown_seconds": circuit_breaker_cooldown_seconds,
        "semantic_leakage_enabled": semantic_leakage_enabled,
        "rag_rate_limit_rpm": rag_rate_limit_rpm,
        "rag_circuit_breaker_enabled": rag_circuit_breaker_enabled,
        "rag_context_binding_enabled": rag_context_binding_enabled,
        "rag_context_binding_ttl": rag_context_binding_ttl,
        "rag_relevance_threshold": rag_relevance_threshold,
        "prompt_rewrite_threshold": prompt_rewrite_threshold,
    }


# policy-driven-detection R3.2/R3.7: re-export the single-source tri-state
# resolver so callers reaching for ``config.resolve_tier2_enabled`` and
# ``config_sync.resolve_tier2_enabled`` get the SAME function (Tier-2 is
# opt-in, default OFF; ``None``/absent -> False, ``True`` -> True, else False).
# Guarded so a config_sync import hiccup can never break ``load_config``.
try:  # pragma: no cover - trivial re-export wiring
    from .config_sync import resolve_tier2_enabled  # noqa: F401
except ImportError:  # pragma: no cover
    try:
        from config_sync import resolve_tier2_enabled  # type: ignore[no-redef]  # noqa: F401
    except ImportError:
        def resolve_tier2_enabled(value):  # type: ignore[misc]
            """Fallback resolver (identical rule) if config_sync is unavailable."""
            return value is True

# policy-driven-detection R6.1/R6.3 (task 7.2): same single-source re-export for
# the RAG-surface Tier-2 resolver, so ``config.resolve_rag_tier2_enabled`` and
# ``config_sync.resolve_rag_tier2_enabled`` are the SAME function (RAG Tier-2 is
# opt-in, effective default OFF; ``None``/absent -> False, ``True`` -> True).
try:  # pragma: no cover - trivial re-export wiring
    from .config_sync import resolve_rag_tier2_enabled  # noqa: F401
except ImportError:  # pragma: no cover
    try:
        from config_sync import resolve_rag_tier2_enabled  # type: ignore[no-redef]  # noqa: F401
    except ImportError:
        def resolve_rag_tier2_enabled(value):  # type: ignore[misc]
            """Fallback resolver (identical rule) if config_sync is unavailable."""
            return value is True

# policy-driven-detection R6.1/R6.3 (task 7.4): same single-source re-export for
# the MCP-surface Tier-2 resolver, so ``config.resolve_mcp_tier2_enabled`` and
# ``config_sync.resolve_mcp_tier2_enabled`` are the SAME function (MCP Tier-2 is
# opt-in, model-only, effective default OFF; ``None``/absent -> False, ``True`` -> True).
try:  # pragma: no cover - trivial re-export wiring
    from .config_sync import resolve_mcp_tier2_enabled  # noqa: F401
except ImportError:  # pragma: no cover
    try:
        from config_sync import resolve_mcp_tier2_enabled  # type: ignore[no-redef]  # noqa: F401
    except ImportError:
        def resolve_mcp_tier2_enabled(value):  # type: ignore[misc]
            """Fallback resolver (identical rule) if config_sync is unavailable."""
            return value is True