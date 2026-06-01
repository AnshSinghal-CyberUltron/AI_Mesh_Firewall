"""
Configuration for the AIGuardX Gateway Proxy (Phase 3).
"""
import os


def get_env(key, default=None):
    return os.environ.get(key, default)


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
    port = int(get_env("GATEWAY_PORT", "8300"))
    stats_interval = int(get_env("GATEWAY_STATS_INTERVAL_SEC", "120"))
    call_security_scan = get_env("GATEWAY_CALL_SECURITY_SCAN", "false").lower() in ("true", "1", "yes")
    redis_url = get_env("GATEWAY_REDIS_URL", "redis://localhost:6379/0")
    auth_enabled = get_env("GATEWAY_AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
    policy_cache_enabled = get_env("GATEWAY_POLICY_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
    policy_cache_require_loaded = get_env("GATEWAY_POLICY_CACHE_REQUIRE_LOADED", "true").lower() in ("true", "1", "yes")
    input_scan_enabled = get_env("GATEWAY_INPUT_SCAN_ENABLED", "true").lower() in ("true", "1", "yes")
    output_scan_enabled = get_env("GATEWAY_OUTPUT_SCAN_ENABLED", "true").lower() in ("true", "1", "yes")
    scan_block_on_injection = get_env("GATEWAY_SCAN_BLOCK_ON_INJECTION", "true").lower() in ("true", "1", "yes")
    scan_block_on_pii = get_env("GATEWAY_SCAN_BLOCK_ON_PII", "false").lower() in ("true", "1", "yes")
    tier2_fail_closed_enabled = get_env("GATEWAY_TIER2_FAIL_CLOSED_ENABLED", "true").lower() in ("true", "1", "yes")
    tier2_execution_mode = get_env("GATEWAY_TIER2_EXECUTION_MODE", "sync_pre_llm").strip().lower()
    tier2_stream_hold_enabled = get_env("GATEWAY_TIER2_STREAM_HOLD_ENABLED", "false").lower() in ("true", "1", "yes")
    tier2_stream_hold_timeout_ms = int(get_env("GATEWAY_TIER2_STREAM_HOLD_TIMEOUT_MS", "1200"))
    stream_preflight_fail_closed = get_env("GATEWAY_STREAM_PREFLIGHT_FAIL_CLOSED", "true").lower() in ("true", "1", "yes")
    stream_max_buffer_bytes = int(get_env("GATEWAY_STREAM_MAX_BUFFER_BYTES", "4096"))
    stream_max_buffer_chunks = int(get_env("GATEWAY_STREAM_MAX_BUFFER_CHUNKS", "64"))
    stream_emit_debug_headers = get_env("GATEWAY_STREAM_EMIT_DEBUG_HEADERS", "false").lower() in ("true", "1", "yes")
    stream_finalize_timeout_ms = int(get_env("GATEWAY_STREAM_FINALIZE_TIMEOUT_MS", "5000"))
    scan_buffer_max_bytes = int(get_env("GATEWAY_SCAN_BUFFER_MAX_BYTES", "4096"))
    scan_thread_pool_size = int(get_env("GATEWAY_SCAN_THREAD_POOL_SIZE", "4"))
    deep_scan_enabled = get_env("GATEWAY_DEEP_SCAN_ENABLED", "false").lower() in ("true", "1", "yes")
    org_only_inference = get_env("GATEWAY_ORG_ONLY_INFERENCE", "true").lower() in ("true", "1", "yes")
    litellm_default_model = (get_env("LITELLM_DEFAULT_MODEL", "") or "").strip()
    litellm_fallback_models = get_env("LITELLM_FALLBACK_MODELS", "gpt-4o-mini")
    litellm_request_timeout = int(get_env("LITELLM_REQUEST_TIMEOUT", "120"))
    litellm_num_retries = int(get_env("LITELLM_NUM_RETRIES", "2"))
    litellm_drop_params = get_env("LITELLM_DROP_PARAMS", "true").lower() in ("true", "1", "yes")
    rag_enabled = get_env("GATEWAY_RAG_ENABLED", "false").lower() in ("true", "1", "yes")
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
    rag_max_query_length = int(get_env("GATEWAY_RAG_MAX_QUERY_LENGTH", "2000"))
    rag_default_max_results = int(get_env("GATEWAY_RAG_DEFAULT_MAX_RESULTS", "10"))
    default_max_context_tokens = int(get_env("GATEWAY_DEFAULT_MAX_CONTEXT_TOKENS", "0"))
    kill_switch_enabled = get_env("GATEWAY_KILL_SWITCH_ENABLED", "true").lower() in ("true", "1", "yes")
    telemetry_enabled = get_env("GATEWAY_TELEMETRY_ENABLED", "true").lower() in ("true", "1", "yes")
    telemetry_flush_interval = float(get_env("GATEWAY_TELEMETRY_FLUSH_INTERVAL", "2.0"))
    telemetry_buffer_size = int(get_env("GATEWAY_TELEMETRY_BUFFER_SIZE", "100"))
    output_guard_enabled = get_env("GATEWAY_OUTPUT_GUARD_ENABLED", "true").lower() in ("true", "1", "yes")
    output_grounding_enabled = get_env("GATEWAY_OUTPUT_GROUNDING_ENABLED", "true").lower() in ("true", "1", "yes")
    output_block_on_credential = get_env("GATEWAY_OUTPUT_BLOCK_ON_CREDENTIAL", "true").lower() in ("true", "1", "yes")
    output_block_on_ip_leakage = get_env("GATEWAY_OUTPUT_BLOCK_ON_IP_LEAKAGE", "false").lower() in ("true", "1", "yes")
    hallucination_flag_enabled = get_env("GATEWAY_HALLUCINATION_FLAG_ENABLED", "true").lower() in ("true", "1", "yes")

    # Smart routing weights
    routing_risk_weight = float(get_env("GATEWAY_ROUTING_RISK_WEIGHT", "0.30"))
    routing_cost_weight = float(get_env("GATEWAY_ROUTING_COST_WEIGHT", "0.20"))
    routing_latency_weight = float(get_env("GATEWAY_ROUTING_LATENCY_WEIGHT", "0.20"))
    routing_priority_weight = float(get_env("GATEWAY_ROUTING_PRIORITY_WEIGHT", "0.30"))

    # Circuit breaker
    circuit_breaker_enabled = get_env("GATEWAY_CIRCUIT_BREAKER_ENABLED", "true").lower() in ("true", "1", "yes")
    circuit_breaker_error_threshold = float(get_env("GATEWAY_CIRCUIT_BREAKER_ERROR_THRESHOLD", "0.5"))
    circuit_breaker_min_requests = int(get_env("GATEWAY_CIRCUIT_BREAKER_MIN_REQUESTS", "10"))
    circuit_breaker_cooldown_seconds = int(get_env("GATEWAY_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "120"))
    semantic_leakage_enabled = get_env("GATEWAY_SEMANTIC_LEAKAGE_ENABLED", "false").lower() in ("true", "1", "yes")

    # RAG Pipeline (4-stage governed pipeline)
    rag_rate_limit_rpm = int(get_env("GATEWAY_RAG_RATE_LIMIT_RPM", "0"))  # 0=unlimited
    rag_circuit_breaker_enabled = get_env("GATEWAY_RAG_CIRCUIT_BREAKER_ENABLED", "true").lower() in ("true", "1", "yes")
    rag_context_binding_enabled = get_env("GATEWAY_RAG_CONTEXT_BINDING_ENABLED", "true").lower() in ("true", "1", "yes")
    rag_context_binding_ttl = int(get_env("GATEWAY_RAG_CONTEXT_BINDING_TTL", "300"))
    rag_relevance_threshold = float(get_env("GATEWAY_RAG_RELEVANCE_THRESHOLD", "0.75"))
    prompt_rewrite_threshold = float(get_env("GATEWAY_PROMPT_REWRITE_THRESHOLD", "0.50"))

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
        "input_scan_enabled": input_scan_enabled,
        "output_scan_enabled": output_scan_enabled,
        "scan_block_on_injection": scan_block_on_injection,
        "scan_block_on_pii": scan_block_on_pii,
        "tier2_fail_closed_enabled": tier2_fail_closed_enabled,
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