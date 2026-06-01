# Graph Report - ai_mesh_gateway  (2026-05-28)

## Corpus Check
- 81 files · ~84,410 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1488 nodes · 2455 edges · 97 communities (76 shown, 21 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 335 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `85c90a76`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 96|Community 96]]

## God Nodes (most connected - your core abstractions)
1. `proxy_chat()` - 42 edges
2. `_HealthEndpointAccessFilter` - 34 edges
3. `LLMRouter` - 33 edges
4. `startup()` - 30 edges
5. `RAGFirewallPipeline` - 30 edges
6. `CircuitBreaker` - 26 edges
7. `InputScanner` - 25 edges
8. `GroundingGuard` - 25 edges
9. `SecureStreamingResponse` - 23 edges
10. `OutputGuard` - 22 edges

## Surprising Connections (you probably didn't know these)
- `test_circuit_open_blocks_before_sse()` --calls--> `CircuitBreaker`  [INFERRED]
  tests/test_stream_governance_integration.py → circuit_breaker.py
- `BedrockScanner` --uses--> `BedrockClient`  [INFERRED]
  bedrock_scanner.py → bedrock_client.py
- `_HealthEndpointAccessFilter` --uses--> `BedrockClient`  [INFERRED]
  main.py → bedrock_client.py
- `admin_bedrock_test()` --calls--> `BedrockClient`  [INFERRED]
  main.py → bedrock_client.py
- `startup()` --calls--> `default_bedrock_client()`  [INFERRED]
  main.py → bedrock_client.py

## Communities (97 total, 21 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (54): CircuitStatus, Current circuit breaker status for a model., BedrockCircuitOpenError, BedrockEmbedder, _bump(), _cache_key(), PIIRedactionRequiredError, Bedrock Titan v2 embedder for D_G10 Semantic Hallucination Grounding.  Provides (+46 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (26): ChromaDBClient, get_vector_client(), MilvusClient, PineconeClient, Vector DB client abstraction for the Gateway Data Plane.  Provides a unified asy, Synchronous query execution, run inside ThreadPoolExecutor., Async query execution via ThreadPoolExecutor., Synchronous add documents to a ChromaDB collection. (+18 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (39): _compute_grounding_score(), _detect_contradictions(), HallucinationScore, OutputGuard, OutputVerdict, Output Guard -- generator-level output guardrails for the gateway.  Orchestrates, Attach an optional SemanticLeakageDetector., Attach an optional :class:`rag_pipeline.grounding_guard.GroundingGuard`. (+31 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (26): _extract_response_text(), LLMRouter, ModelSelection, _normalize_model_alias(), _normalize_weights(), _parse_json_object(), _pop_compliant_fallback_chain(), LLM routing via LiteLLM. Provides async completion (streaming and non-streaming) (+18 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (45): _adapter_forward(), _backend_proxy_headers(), _effective_presidio_action(), ext_mcp_proxy(), _filter_tools_by_enabled(), _get_auth_context(), _get_enabled_tools(), _get_server_config() (+37 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (24): _build_error_sse(), FlushReason, Secure Streaming Response (Phase 3 - Step 3.2, extended with Output Guard).  Buf, Yield redacted content: first chunk gets all text, rest get empty., Reset all internal buffers., Wraps an SSE async generator with output scanning.      The inner generator yiel, SecureStreamingResponse, build_telemetry_event() (+16 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (31): _base_url(), _cleanup_expired(), oauth_authorize_page(), oauth_authorize_submit(), oauth_metadata(), oauth_protected_resource(), oauth_protected_resource_path(), oauth_register() (+23 more)

### Community 7 - "Community 7"
Cohesion: 0.06
Nodes (29): fake_redis(), fake_redis_server(), fake_sync_redis(), input_scanner(), mock_litellm_response(), MockAsyncIterator, Root conftest for all gateway (FastAPI Data Plane) tests.  Provides async fakere, Compiled policy bundle as stored in policies:compiled Redis key. (+21 more)

### Community 8 - "Community 8"
Cohesion: 0.07
Nodes (31): _apply_compliant_isolation_reroute(), _audit_fire_and_forget(), _blocked_keyword_matches(), _build_routing_metadata(), _category_to_threat_type(), _coerce_string_list(), _detect_rag_request(), _extract_agent_data() (+23 more)

### Community 9 - "Community 9"
Cohesion: 0.1
Nodes (29): enqueue_job(), admin_rag_create_collection(), admin_rag_delete_collection(), _emit_telemetry(), _enforce_org_tpm_rate_limit(), _estimate_request_tokens(), _is_valid_collection_name(), _maybe_emit_critical_alert() (+21 more)

### Community 10 - "Community 10"
Cohesion: 0.08
Nodes (27): compile_pattern(), detect_credential_exposure(), detect_hallucination_markers(), detect_ip_leakage(), _mask_api_key(), _mask_aws_key(), _mask_credit_card(), _mask_email() (+19 more)

### Community 11 - "Community 11"
Cohesion: 0.12
Nodes (28): _callback_url(), _discover_oauth_metadata(), _flow_key(), _flow_pop(), _flow_save(), _generate_pkce(), _get_redis(), get_stored_token() (+20 more)

### Community 12 - "Community 12"
Cohesion: 0.11
Nodes (26): _ensure_initialized(), _ensure_process(), _kill_process(), list_processes(), _log_stderr(), _process_key(), MCP Stdio Transport Adapter for the ZeroShield Gateway.  Manages stdio-based MCP, Get or start a stdio process for the given server. (+18 more)

### Community 13 - "Community 13"
Cohesion: 0.09
Nodes (14): ConfigSync, ConfigSync: subscribes to Redis Pub/Sub ``config_updates`` channel and maintains, Force an immediate LLM model reload from Redis.          Useful at gateway start, Load the firewall config from Redis on startup.         Loads both legacy flat k, Merge Redis-sourced config values into the global CONFIG dict.         Only keys, Continuously subscribe to the config_updates Pub/Sub channel.         On each me, Fetch the latest firewall config from Redis and apply., Fetch updated LLM model configs from Redis and reload the         LiteLLM Router (+6 more)

### Community 14 - "Community 14"
Cohesion: 0.15
Nodes (25): _admin_user_ids_from_env(), is_admin(), Admin RBAC helper for the AI Mesh Gateway.  Extracted into its own module so it, Return True iff ``auth_ctx`` represents an admin caller.      Pure predicate — n, Gate helper for admin endpoints.      Returns ``None`` when the caller is admin, require_admin(), _body(), _clean_env() (+17 more)

### Community 15 - "Community 15"
Cohesion: 0.12
Nodes (11): BedrockTier2Breaker, _KeyState, Per-(org_slug, scanning_model_id) circuit breaker for the Tier-2 Bedrock scanner, Add one observation; return (total, failures) across the window., Process-local circuit breaker for Bedrock Tier-2 calls.      Use ``allow(org_slu, Return True if the call should proceed to Bedrock.          - CLOSED          ->, Record one observation, advancing breaker state as needed., Seconds remaining in OPEN cooldown; 0 if not OPEN. (+3 more)

### Community 16 - "Community 16"
Cohesion: 0.15
Nodes (22): check_auto_isolate(), check_auto_recover(), check_model_state(), ModelStateVerdict, ModelState enforcement and rolling risk engine for the gateway.  Checks Redis fo, Record a risk event in the rolling window and return updated composite score., Check if model should be auto-isolated based on risk score exceeding threshold., Check if an isolated model should auto-recover based on cooldown expiry.      Re (+14 more)

### Community 17 - "Community 17"
Cohesion: 0.14
Nodes (10): get_engine(), PresidioEngine, PresidioFinding, Presidio integration for the ZeroShield gateway (DECISION-D Phase 1).  This modu, Thin façade over Presidio with sidecar + library + disabled modes.      Thread-s, Replace each detected span with ``<ENTITY_TYPE>``.          We implement this in, Scan a single string. Returns a ScanResult.          ``direction`` is one of ``", Recursively scan all string leaves of a JSON-shaped payload.          Returns `` (+2 more)

### Community 18 - "Community 18"
Cohesion: 0.11
Nodes (19): classify_intent(), classify_intent_v2(), IntentClassification, _is_tier2_degraded(), _normalize_bedrock_action(), _normalize_leet(), _normalize_score(), Gateway-local Input/Output Scanner.  Lightweight regex-based threat detection fo (+11 more)

### Community 19 - "Community 19"
Cohesion: 0.17
Nodes (21): check_kill_switch(), KillSwitchVerdict, _parse_payload(), Gateway-local kill-switch enforcement., Parse a JSON payload from Redis, returning None on failure., Result of a kill-switch check., Check kill switches via Redis pipeline (per-request canonical read).      Preced, Phase 1 §1.5 — unit tests for the gateway-local kill-switch.  Targets ``kill_swi (+13 more)

### Community 20 - "Community 20"
Cohesion: 0.1
Nodes (10): VectorPolicySync: subscribes to Redis Pub/Sub ``vector_policy_updates`` channel, Load the compiled vector policy bundle from Redis on startup., Continuously subscribe to the vector_policy_updates Pub/Sub channel.         Rec, Fetch the latest compiled vector policy bundle from Redis and swap the cache., Async Redis Pub/Sub subscriber that keeps an in-memory copy     of the compiled, O(1) lookup of a vector collection policy by project and collection name., Return all collection names accessible to a project., Initialize the cache and start the background subscriber.          1. Load the c (+2 more)

### Community 21 - "Community 21"
Cohesion: 0.13
Nodes (18): admin_rag_list_collections(), _check_version(), _configure_logging(), _http_request(), _http_request_with_retry(), main(), _parse_version(), _policy_check() (+10 more)

### Community 22 - "Community 22"
Cohesion: 0.14
Nodes (19): log_bedrock_request(), log_bedrock_response(), log_metrics(), log_scan_parse_failed(), log_scan_refusal(), log_scan_result(), log_scan_start(), _log_with_data() (+11 more)

### Community 23 - "Community 23"
Cohesion: 0.15
Nodes (18): _connection_key(), _ensure_connection(), _ensure_initialized(), list_connections(), MCP WebSocket Transport Adapter for the ZeroShield Gateway.  Manages WebSocket c, Get or open a WebSocket connection for the given server., Send a JSON-RPC message and wait for the response., Send MCP initialize + initialized if not already done. (+10 more)

### Community 24 - "Community 24"
Cohesion: 0.13
Nodes (8): LogBuffer, LogRecord, In-memory log buffer with async subscriber support for SSE streaming.  Records a, Async background task that subscribes to the Redis Pub/Sub     log channel and f, Lightweight serializable log record., Thread-safe circular buffer for recent log records.      Records are pushed from, Async generator that yields new log records as SSE-formatted strings.          S, RedisLogSubscriber

### Community 25 - "Community 25"
Cohesion: 0.11
Nodes (12): BedrockLogEntry, BedrockLogRing, _JSONFormatter, Push records into the in-memory ring buffer., Create and configure the dedicated Bedrock logger., Emit each log record as a single JSON line., Human-readable formatter for non-JSON mode., Lightweight structured log entry kept in memory. (+4 more)

### Community 26 - "Community 26"
Cohesion: 0.13
Nodes (15): build_isolation_audit_metadata(), fallback_profile_key(), model_passes_hard_filters(), Model isolation primitives: compliant fallback resolution and audit envelopes., Structured audit envelope for kill-switch / isolation / reroute events., Same hard filters as LLMRouter._score_routing_models (without soft scoring)., Pick a compliant fallback model.      Returns (model_name_or_none, reason_code)., resolve_compliant_fallback() (+7 more)

### Community 27 - "Community 27"
Cohesion: 0.16
Nodes (19): delete_vector_documents(), _ensure_redis_client(), get_vector_config(), _inject_vector_globals(), query_vector_db(), Vector Operations API (/v1/vector/*) — Portable Multi-Tenant RAG Vector DB Inter, Lazy-init fallback for ``REDIS_CLIENT``.      The intended wiring path is ``main, Extract organization ID from a Gateway API Key bearer token.      Returns ``{org (+11 more)

### Community 28 - "Community 28"
Cohesion: 0.14
Nodes (16): apply_redaction(), _compile_regex(), evaluate(), evaluate_for_stage(), _evaluate_rule(), EvaluationResult, _get_text_to_check(), Local policy evaluation engine for the Gateway Data Plane.  This is a Django-fre (+8 more)

### Community 29 - "Community 29"
Cohesion: 0.2
Nodes (14): enforce_org_tpm_rate_limit(), Phase 1 §1.1 — per-org TPM rate-limit enforcement helper.  Extracted from main.p, Enforce the per-org TPM ceiling. Returns a 429 JSONResponse to short-circuit, Phase 1 §1.1 — unit tests for the per-org TPM rate-limit helper.  Tests target `, _run(), test_allows_under_ceiling(), test_blocks_over_ceiling_with_429(), test_fails_closed_on_limiter_exception() (+6 more)

### Community 30 - "Community 30"
Cohesion: 0.16
Nodes (17): estimate_tokens(), _message_tokens(), minimize_context(), prune_messages(), Context Assembler: token-budget-based message pruning and field-level sensitivit, Orchestrator: apply field-level redaction then token-budget pruning.      If ``m, Recursively redact dictionary fields exceeding clearance level., Replace a JSON match with redacted version. (+9 more)

### Community 31 - "Community 31"
Cohesion: 0.2
Nodes (10): CircuitBreaker, CircuitState, Automatic Circuit Breaker for LLM Models.  Tracks per-model error rates in Redis, Record a failed LLM response and evaluate threshold., Check current circuit state for a model., Redis-backed circuit breaker for LLM model error tracking., Record a successful LLM response., StreamScanMode (+2 more)

### Community 32 - "Community 32"
Cohesion: 0.15
Nodes (15): _launch_chat_stream_response(), stream_phase + finalization_phase for /v1/chat/completions (SSE).      Assumes e, _stream_finalize_hooks(), build_base_stream_headers(), build_inner_llm_stream(), enrich_stream_headers(), policy_summary_id(), End-to-end streaming orchestration for /v1/chat/completions.  Lifecycle (all har (+7 more)

### Community 33 - "Community 33"
Cohesion: 0.15
Nodes (10): BedrockScanner, _fuzzy_subsequence_match(), InputScanner, Gateway-local scanner for prompt/response content., Asynchronously scan the LLM output for PII/Secrets and return a structured verdi, Synchronous prompt scanning logic, run in a thread to avoid blocking., Detect excessive repetition as a simple heuristic for DoS attempts., Heuristic toxicity scoring based on indicator pattern matches.         Compares (+2 more)

### Community 34 - "Community 34"
Cohesion: 0.14
Nodes (10): ContextGuard, ContextScanVerdict, Context Guard: document-level threat scanner for the Gateway Data Plane.  Scans, Asynchronously scan a single document text for threats., Detect anomalous results using distance threshold and statistical         outlie, Synchronous batch document scanning., Synchronous scan of a single document., Structured result of document-level scanning. (+2 more)

### Community 35 - "Community 35"
Cohesion: 0.21
Nodes (8): EmbeddingVault, _format_vector(), Embedding Vault for the Gateway Data Plane.  Stores embeddings of known prompt i, Synchronous vault check for use from non-async contexts (e.g. inside         the, Stores known attack embeddings and detects similar inputs., vault_size(), VaultMatch, VaultVerdict

### Community 36 - "Community 36"
Cohesion: 0.17
Nodes (9): _bucket_key(), RateLimiter, Token-Per-Minute (TPM) rate limiter using Redis fixed-window counters.  Redis ke, Check per-model RPM limit using a fixed-window counter.         Returns (allowed, Organization-wide TPM ceiling using a fixed-window Lua counter.          This is, Adjust the TPM counter after an LLM response.          ``check_rate_limit`` pre-, Async Redis-backed TPM rate limiter for the Gateway Data Plane., Lazy-init the async connection pool. (+1 more)

### Community 37 - "Community 37"
Cohesion: 0.21
Nodes (13): Single stage execution record for the audit trail., StageRecord, GeneratorStageInput, PipelineResult, QueryStageInput, RankerStageInput, Typed contracts for every stage boundary in the RAG firewall pipeline., Final result of the complete RAG firewall pipeline. (+5 more)

### Community 38 - "Community 38"
Cohesion: 0.12
Nodes (17): admin_bedrock_logs(), admin_bedrock_logs_file(), admin_bedrock_logs_stream(), admin_bedrock_test(), admin_circuit_breaker_reset(), admin_circuit_breaker_state(), admin_circuit_breaker_trigger(), admin_db_test() (+9 more)

### Community 39 - "Community 39"
Cohesion: 0.18
Nodes (12): prometheus_metrics(), Lightweight metrics emitter (logging-first).  This module provides simple functi, Return (body, content_type) for the /metrics handler., record_bedrock_embed(), record_kill_switch(), record_policy_block(), record_rate_limit(), record_request() (+4 more)

### Community 40 - "Community 40"
Cohesion: 0.16
Nodes (6): VectorProviderSync: subscribes to Redis Pub/Sub ``vector_provider_updates`` chan, Wait for DEBOUNCE_DELAY_SECONDS, then reload if pending.         Batches multipl, Async Redis subscriber that keeps an in-memory copy     of organisation vector p, O(1) lookup of a provider config by org ID and provider type., Return all active provider configs for an organisation., VectorProviderSync

### Community 41 - "Community 41"
Cohesion: 0.17
Nodes (9): JudgeVerdict, LLMJudge, LLM-as-Judge Detection Layer for the Gateway Data Plane.  Uses AWS Bedrock via b, Regex-only fallback when Bedrock is unavailable., Synchronous judge call via boto3 Bedrock invoke_model., Async LLM judge call. Returns JudgeVerdict., Result of LLM-as-Judge analysis., Uses AWS Bedrock to classify prompt injection attempts.      NO OpenAI, NO LiteL (+1 more)

### Community 42 - "Community 42"
Cohesion: 0.17
Nodes (9): PolicySync, Initialize the cache and start the background subscriber.          1. Scan all o, Stop the background subscriber gracefully., Load all org-scoped compiled bundles from Redis on startup.         Scans for ``, Continuously subscribe to the policy_updates Pub/Sub channel.         On each me, Fetch the latest compiled bundle for a specific org from Redis., Async Redis Pub/Sub subscriber that keeps per-org in-memory copies     of compil, emit_operational_event() (+1 more)

### Community 43 - "Community 43"
Cohesion: 0.14
Nodes (15): _build_no_inference_provider_response(), _filter_inference_eligible_models(), _guard_model_names(), _is_guard_only_model(), _is_routing_sentinel_model(), list_models(), Client hint meaning 'pick via router' — not an allowlist entry., Map auto/empty to org default_model or first connected inference model. (+7 more)

### Community 44 - "Community 44"
Cohesion: 0.17
Nodes (10): _extract_sensitive_fragments(), LeakageVerdict, Semantic Leakage Detector for the Gateway Data Plane.  Detects information leaka, Track information extraction across requests. Returns risk 0.0-1.0., Result of semantic leakage analysis., Detects confidential information leakage in LLM outputs., Register content that should not appear in outputs., Check output text for semantic similarity to confidential content. (+2 more)

### Community 45 - "Community 45"
Cohesion: 0.16
Nodes (8): CanaryResult, CanaryTokenManager, Canary Token System for the Gateway Data Plane.  Injects invisible marker tokens, Result of canary token verification., Manages canary token injection and verification., Generate a unique canary token., Inject a canary token into context text.          Returns (modified_text, canary, Check if the canary token appears in the LLM response.          If the canary le

### Community 46 - "Community 46"
Cohesion: 0.2
Nodes (10): emit_query_audit_event(), Emit a D_G5 query-audit event (rewrite / block / downgrade).      ``decision ==, RED-then-GREEN tests for ``telemetry_ops.emit_query_audit_event``.  These tests, _RecordingSink, sink(), test_allow_decision_is_noop(), test_block_emits_warning_severity(), test_downgrade_emits_info_severity() (+2 more)

### Community 47 - "Community 47"
Cohesion: 0.22
Nodes (10): DocumentManifest, GeneratorStageOutput, Independent policy decision from a single pipeline stage., Tracks document identity across pipeline stages for chain-of-custody., RetrieverStageOutput, StageVerdict, GeneratorStage, Verifies approved context, sanitizes documents, and prepares context binding. (+2 more)

### Community 48 - "Community 48"
Cohesion: 0.18
Nodes (10): _extract_usage_from_sse_line(), instrumented_stream_generator(), Fail-closed gate before opening an SSE response.      When ``stream_preflight_fa, Track TTFT, chunk count, and usage from SSE payloads., streaming_preflight_block_body(), Unit tests for stream_orchestration lifecycle helpers., test_extract_usage_from_sse(), test_instrumented_stream_tracks_ttft() (+2 more)

### Community 49 - "Community 49"
Cohesion: 0.22
Nodes (12): _HealthEndpointAccessFilter, Suppress uvicorn access-log lines for health-check endpoints.      Prevents read, finalize_stream(), finalization_phase: circuit, TPM, metrics, optional telemetry., Inputs for stream_phase after preflight/selection in main., Callbacks/resources for post-stream accounting., stream_with_finalize(), StreamFinalizeHooks (+4 more)

### Community 50 - "Community 50"
Cohesion: 0.36
Nodes (12): _new_breaker(), Phase 1 §1.5 — unit tests for CircuitBreaker state transitions.  Uses fakeredis, Per INVARIANT 6, check() must fail-closed (should_block=True)., _run(), test_below_min_requests_stays_closed(), test_does_not_open_when_under_threshold(), test_half_open_closes_after_probe_successes(), test_half_open_reopens_on_probe_failure() (+4 more)

### Community 51 - "Community 51"
Cohesion: 0.21
Nodes (8): BedrockClient, default_bedrock_client(), _extract_output_preview(), _extract_prompt_preview(), AWS Bedrock client for Tier-2 scanning using boto3 invoke_model.  Authentication, AWS Bedrock runtime client using boto3 invoke_model.      Expects:       - AWS_A, Send prompt to Bedrock via invoke_model and return normalized result.          R, Convenience factory using environment configuration.

### Community 52 - "Community 52"
Cohesion: 0.17
Nodes (10): log_bedrock_error(), log_rule_hit(), new_request_id(), Generate a short unique ID for correlating request/response pairs., Log a Bedrock invocation error., Log a security rule hit from Bedrock analysis., _is_refusal(), _normalize_score() (+2 more)

### Community 53 - "Community 53"
Cohesion: 0.23
Nodes (11): health(), startup(), _telemetry_loop(), _canonical_payload(), _get_signing_key(), Gateway-side verifier for compiled policy bundles.  MUST stay byte-compatible wi, Return the HMAC signing key bytes, or None when not configured.      Phase 0 D-G, Return True iff `bundle` carries a valid HMAC-SHA256 signature. (+3 more)

### Community 54 - "Community 54"
Cohesion: 0.22
Nodes (7): RankerStageOutput, compute_trust_score(), RankerStage, Ranker stage: trust scoring, anomaly detection, content scanning, and policy-bas, Compute a trust score [0.0-1.0] for a retrieved document.      Canonical impleme, Wraps ContextGuard for trust scoring, anomaly detection, and content scanning., Hot-update compiled policies without recreating the stage.

### Community 55 - "Community 55"
Cohesion: 0.25
Nodes (9): bedrock_model_id_candidates(), build_simulator_bedrock_route_metadata(), Bedrock model selection helpers (used by gateway routing and unit tests)., select_simulator_default_model(), test_bedrock_model_id_candidates_include_prefixed_and_unprefixed_variants(), test_build_simulator_bedrock_route_metadata_is_global_and_boto3_only(), test_select_simulator_default_model_prefers_bedrock_model_id_when_name_differs(), test_select_simulator_default_model_prefers_bedrock_model_name() (+1 more)

### Community 56 - "Community 56"
Cohesion: 0.29
Nodes (9): _coerce_content_to_text(), _extract_content(), _extract_first_json_object(), _get_content_str(), _parse_partial_json(), Bedrock-based Tier-2 scanner that calls the external Bedrock/OpenAI-compatible r, Remove ``<reasoning>...</reasoning>`` blocks that some Bedrock models     prepen, Attempt to salvage truncated JSON from Bedrock responses. (+1 more)

### Community 57 - "Community 57"
Cohesion: 0.22
Nodes (6): IntentClassifier, IntentVerdict, Intent Classifier for the Gateway Data Plane.  Classifies the intent of incoming, Result of intent classification., Classifies the intent of incoming queries., Classify the intent of a query.

### Community 58 - "Community 58"
Cohesion: 0.22
Nodes (4): PipelineContext accumulator -- audit trail for every RAG request., Pipeline-Aware RAG Firewall: 4-stage governed pipeline.  Stages: QueryStage -> R, RAGFirewallPipeline: the single source of truth for RAG request processing.  Orc, Retriever stage: execute vector DB query with circuit breaker and rate limiting.

### Community 59 - "Community 59"
Cohesion: 0.2
Nodes (3): metrics_module(), Tests for the Prometheus /metrics endpoint and recorder helpers., Reload the metrics module so each test gets a fresh registry.

### Community 60 - "Community 60"
Cohesion: 0.22
Nodes (4): PolicySync: subscribes to Redis Pub/Sub ``policy_updates`` channel and maintains, _log_task_exception(), Operational telemetry chokepoint for gateway-emitted events.  Phase 0 D-CC-2-v3:, ``add_done_callback`` handler that surfaces background-task errors.      Use as:

### Community 61 - "Community 61"
Cohesion: 0.28
Nodes (5): QueryStageOutput, _attempt_rewrite(), QueryStage, Query stage: validate and scan the incoming query before retrieval., Wraps InputScanner, LLM Judge, Embedding Vault, and Intent Classifier.      Resp

### Community 62 - "Community 62"
Cohesion: 0.25
Nodes (6): RAGOrchestrator, RAGVerdict, RAG Security Orchestrator -- backward-compatibility wrapper.  The real pipeline, Result of the full RAG security pipeline (backward-compat)., Backward-compat wrapper. Delegates to RAGFirewallPipeline., Full RAG pipeline with anomaly detection and context scanning.

### Community 63 - "Community 63"
Cohesion: 0.25
Nodes (8): _close_connection(), Close and clean up a WebSocket connection., Close all WebSocket connections. Called during gateway shutdown., Periodically close idle WebSocket connections., Start the background reaper task., _reaper_loop(), shutdown_all(), start_reaper()

### Community 64 - "Community 64"
Cohesion: 0.25
Nodes (6): EscalationConfig, get_escalation_config(), Inter-stage enforcement escalation for the RAG firewall pipeline., Return the escalation config for the given level, clamped to [0,2]., Threshold modifiers applied at a given escalation level., Generator stage: verify approved context, sanitize documents, and prepare contex

### Community 65 - "Community 65"
Cohesion: 0.29
Nodes (4): _build_zeroshield_metadata(), _get_compliance_tags(), Build the unified zeroshield metadata dict for any response., OutputGuardrailMetadataTests

### Community 66 - "Community 66"
Cohesion: 0.48
Nodes (6): _enabled(), _ensure_collection(), is_mongo_telemetry_enabled(), Additive Mongo telemetry sink for enforcement events (Phase 1 pilot).  Hot-path, Best-effort write. Never raises., record_enforcement_event()

### Community 67 - "Community 67"
Cohesion: 0.33
Nodes (5): detect_pii(), detect_secrets(), Detect PII, PHI, and PCI patterns in text. Returns dict of type -> matched value, Detect secret patterns in text. Returns dict of type -> matched value., Sync output scanning logic for PII/Secrets, run in a thread.

### Community 68 - "Community 68"
Cohesion: 0.33
Nodes (4): HTTP-adjacent integration tests for streaming governance gates., Mirror proxy_chat policy-unavailable behavior without mounting full app., test_circuit_open_blocks_before_sse(), test_policy_check_cached_fail_closed_for_stream_path()

### Community 69 - "Community 69"
Cohesion: 0.53
Nodes (5): get_env(), is_http_allowed_for_url(), load_config(), Configuration for the AIGuardX Gateway Proxy (Phase 3)., Return True if HTTP is allowed for this URL (localhost/dev only).     Production

### Community 70 - "Community 70"
Cohesion: 0.33
Nodes (4): PipelineContext, Accumulates state across all pipeline stages for a single request., Add a stage record and auto-escalate based on verdict., Serialize for telemetry metadata and API response.

### Community 71 - "Community 71"
Cohesion: 0.33
Nodes (6): _build_block_response(), _build_safe_block_response(), Build a unified blocked JSONResponse with zeroshield metadata.     CRITICAL: Use, Map a block to the pipeline stage that actually enforced it (not always policy)., Build a client-safe error response for policy blocks.     NEVER exposes:       -, _resolve_pipeline_blocked_by()

### Community 72 - "Community 72"
Cohesion: 0.4
Nodes (3): policies(), Return compiled policy entries for a specific org., Return compiled policies applicable to a specific MCP server.          Includes:

### Community 73 - "Community 73"
Cohesion: 0.5
Nodes (3): Health check: verify Bedrock connectivity by listing foundation models., log_health_check(), Log a Bedrock health check result.

### Community 74 - "Community 74"
Cohesion: 0.67
Nodes (3): Fail-closed JSON response before SSE when streaming preflight cannot complete., _stream_preflight_block_if_needed(), test_stream_preflight_helper_matches_main_block()

## Knowledge Gaps
- **527 isolated node(s):** `AWS Bedrock client for Tier-2 scanning using boto3 invoke_model.  Authentication`, `AWS Bedrock runtime client using boto3 invoke_model.      Expects:       - AWS_A`, `Send prompt to Bedrock via invoke_model and return normalized result.          R`, `Health check: verify Bedrock connectivity by listing foundation models.`, `Convenience factory using environment configuration.` (+522 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `_HealthEndpointAccessFilter` connect `Community 49` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 5`, `Community 6`, `Community 13`, `Community 15`, `Community 20`, `Community 21`, `Community 24`, `Community 31`, `Community 33`, `Community 34`, `Community 35`, `Community 36`, `Community 40`, `Community 41`, `Community 42`, `Community 44`, `Community 45`, `Community 51`, `Community 57`?**
  _High betweenness centrality (0.288) - this node is a cross-community bridge._
- **Why does `startup()` connect `Community 53` to `Community 0`, `Community 1`, `Community 2`, `Community 3`, `Community 5`, `Community 13`, `Community 20`, `Community 21`, `Community 24`, `Community 31`, `Community 33`, `Community 34`, `Community 35`, `Community 36`, `Community 37`, `Community 40`, `Community 41`, `Community 42`, `Community 44`, `Community 45`, `Community 51`, `Community 57`, `Community 69`?**
  _High betweenness centrality (0.265) - this node is a cross-community bridge._
- **Why does `proxy_chat()` connect `Community 8` to `Community 32`, `Community 65`, `Community 71`, `Community 9`, `Community 74`, `Community 43`, `Community 16`, `Community 18`, `Community 19`, `Community 21`, `Community 26`, `Community 30`?**
  _High betweenness centrality (0.115) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `proxy_chat()` (e.g. with `check_kill_switch()` and `check_model_state()`) actually correct?**
  _`proxy_chat()` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 30 inferred relationships involving `_HealthEndpointAccessFilter` (e.g. with `Tier2UnavailableStrict` and `AuthMiddleware`) actually correct?**
  _`_HealthEndpointAccessFilter` has 30 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `LLMRouter` (e.g. with `StreamRunMetrics` and `_HealthEndpointAccessFilter`) actually correct?**
  _`LLMRouter` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `startup()` (e.g. with `ConfigSync` and `LLMRouter`) actually correct?**
  _`startup()` has 24 INFERRED edges - model-reasoned connections that need verification._