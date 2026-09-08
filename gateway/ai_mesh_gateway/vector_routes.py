"""
Vector Operations API (/v1/vector/*) — Portable Multi-Tenant RAG Vector DB Interface.

This module provides production-grade endpoints for vector operations that:
  1. Automatically resolve organization from request context
  2. Use org-scoped vector provider configuration from Redis
  3. Route ALL operations through the RAG Firewall
  4. Enforce multi-tenant isolation at the collection level
  5. Support streaming for large result sets

Endpoints:
  - POST /v1/vector/query  — Retrieve documents from vector DB against firewall
  - POST /v1/vector/upsert  — Insert/update documents into vector DB (protected)
  - POST /v1/vector/delete  — Delete documents from vector DB (protected)
  - GET  /v1/vector/config  — Retrieve org's active vector DB config (protected)

All operations require Bearer token in Authorization header.
All operations are logged, traced, and scanned for policy violations.
"""

import asyncio
import json
import logging
import os
from typing import Any, Optional


from fastapi import APIRouter, Header, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

LOG = logging.getLogger("gateway.vector_routes")

router = APIRouter(prefix="/v1/vector", tags=["Vector Operations"])


# ────────────────────────────────────────────────────────────────────────────
#  CONTEXT + INIT
# ────────────────────────────────────────────────────────────────────────────


def inject_vector_globals(
    *,
    rag_pipeline=None,
    vector_clients=None,
    vector_provider_sync=None,
    telemetry=None,
    policy_sync=None,
    config=None,
    redis_client=None,
):
    """Populate this module's singleton references at gateway startup.

    The /v1/vector/* data plane resolves its provider/pipeline/policy/redis
    handles from module-level globals (RAG_PIPELINE, VECTOR_PROVIDER_SYNC, …).
    Those stay ``None`` unless startup injects the gateway's already-built
    singletons here; without this call every endpoint short-circuits (e.g.
    /query → 400 no_provider because VECTOR_PROVIDER_SYNC is None). main.py
    must call this once, after its singletons are constructed, passing them by
    keyword. Only non-None values overwrite an existing global so a partial
    call never clobbers a previously-wired handle.
    """
    global RAG_PIPELINE, VECTOR_CLIENTS, VECTOR_PROVIDER_SYNC
    global TELEMETRY, POLICY_SYNC, CONFIG, REDIS_CLIENT
    if rag_pipeline is not None:
        RAG_PIPELINE = rag_pipeline
    if vector_clients is not None:
        VECTOR_CLIENTS = vector_clients
    if vector_provider_sync is not None:
        VECTOR_PROVIDER_SYNC = vector_provider_sync
    if telemetry is not None:
        TELEMETRY = telemetry
    if policy_sync is not None:
        POLICY_SYNC = policy_sync
    if config is not None:
        CONFIG = config
    if redis_client is not None:
        REDIS_CLIENT = redis_client


def _inject_vector_globals(app, globals_dict):
    """Backwards-compatible shim for the original (never-called) signature.

    The prior implementation read ``app.state.get(...)``; Starlette's
    ``app.state`` is a plain attribute namespace with no ``.get`` method, so
    this path would have raised had it ever been invoked. Preserved only so an
    existing caller (if any) does not break — it now forwards to
    ``inject_vector_globals`` using getattr against app.state.
    """
    state = getattr(app, "state", None)
    inject_vector_globals(
        rag_pipeline=getattr(state, "rag_pipeline", None),
        vector_clients=getattr(state, "vector_clients", None),
        vector_provider_sync=getattr(state, "vector_provider_sync", None),
        telemetry=getattr(state, "telemetry", None),
        policy_sync=getattr(state, "policy_sync", None),
        config=getattr(state, "config", None),
        redis_client=getattr(state, "redis_client", None),
    )


RAG_PIPELINE = None
VECTOR_CLIENTS: dict[str, Any] = {}
VECTOR_PROVIDER_SYNC = None
TELEMETRY = None
POLICY_SYNC = None
CONFIG = {}
REDIS_CLIENT = None

# M-12 — serialize lazy Redis client construction. Without this lock,
# concurrent first-requests can each pass the ``REDIS_CLIENT is None``
# check and independently build a connection pool, leaking pools and
# racing the global assignment. The lock makes the lazy init effectively
# single-flight; the fast path (client already set) still avoids it.
_REDIS_INIT_LOCK = asyncio.Lock()


def _resolve_runtime_vector_client(provider_type: str, provider_config: dict[str, Any]):
    """
    Resolve a vector client from registered clients, with support for org-level
    custom providers that map to a Milvus-compatible URI/token contract.

    Bundle X2 — SSRF defence in depth: even though the control plane now
    rejects unsafe ``connection_url`` values at write-time, rows persisted
    before the validator existed (or pushed through a misconfigured
    backend) may still live in Redis. Re-validate the URL here and refuse
    to instantiate a client against blocked hosts. We log + return ``None``
    rather than raise so the calling handler can return a clean 400 to the
    end user instead of a 500.
    """
    client = VECTOR_CLIENTS.get(provider_type)
    if client:
        return client

    if provider_type == "custom":
        connection_url = (provider_config or {}).get("connection_url", "")
        if connection_url:
            try:
                from ai_mesh_shared.url_safety import (
                    UnsafeProviderURLError,
                    validate_safe_provider_url,
                )

                safe = validate_safe_provider_url(connection_url)
            except UnsafeProviderURLError as exc:
                LOG.warning(
                    "Refusing to instantiate custom vector client: "
                    "unsafe connection_url rejected by SSRF guard: %s",
                    exc,
                )
                return None
            except Exception as exc:  # noqa: BLE001 — never let import errors silently SSRF
                LOG.exception(
                    "url_safety validator failed; refusing custom provider: %s",
                    exc,
                )
                return None

            from vector_client import MilvusClient

            return MilvusClient(
                uri=safe.url,
                token=(provider_config or {}).get("api_key", ""),
            )
    return None


# M-10 — cross-tenant guard for user-supplied collection names.
#
# Every vector op is namespaced by the vector client as
# ``{project_id}__{collection_name}`` (see vector_client._build_collection_name),
# where ``project_id`` is derived from the *authenticated* token. The tenant
# boundary is therefore the ``__`` separator. A caller who smuggles ``__`` into
# ``collection_name`` (e.g. ``victim_project__secret``) escapes their own prefix
# and can address another org's namespace. This mirrors main.py's
# ``_is_valid_collection_name`` (the canonical RAG-path guard) so the
# portable vector routes enforce the same isolation contract.
import re as _re

_COLLECTION_NAME_RE = _re.compile(r"[A-Za-z0-9._-]+")


def _is_owned_collection_name(name: str) -> bool:
    """Return True only for collection names that stay inside the caller's tenant prefix.

    Rejects the ``__`` tenant separator (cross-tenant namespace injection),
    over-long names, and any non-allowlisted characters. Empty names are
    rejected by callers before this point.
    """
    if not name or "__" in name:
        return False
    if len(name) > 128:
        return False
    return bool(_COLLECTION_NAME_RE.fullmatch(name))


# ── RAG pipeline policy construction for the portable /v1/vector/* routes ──
#
# The 4-stage RAGFirewallPipeline.execute() treats ``policy`` as a flat DICT of
# per-request directives (it does ``(policy or {}).get(...)`` and every stage
# reads ``inp.policy.get(...)``). ``POLICY_SYNC.get_policies(org_slug)`` returns
# a ``list[dict]`` of COMPILED policy entries — a DIFFERENT shape. Passing that
# list straight through was a latent defect: a non-empty list is truthy, so
# ``policy or {}`` kept the list and the first ``.get`` raised
# ``AttributeError: 'list' object has no attribute 'get'`` (→ 500); an empty
# list silently collapsed to ``{}`` so the org slug was lost and the pipeline's
# internal ``_get_compiled_policies`` fell back to the "default" org. This
# mirrors main.py's /v1/rag/query, which builds a dict and sets ``_org_slug``
# so the pipeline can resolve the org's compiled policies + guardrail config.

_RAG_GUARDRAIL_KEYS = (
    "prompt_injection_threshold",
    "prompt_rewrite_threshold",
    "prompt_downgrade_threshold",
    "input_scan_enabled",
)


def _gateway_main_module():
    """Resolve the LIVE gateway main module (startup-populated globals).

    Same sys.modules idiom as ``_scan_redact_upsert_documents`` — gunicorn loads
    ``ai_mesh_gateway.main`` and sets CONFIG_SYNC on THAT object; a bare
    ``import main`` resolves a different module whose startup never ran.
    """
    import sys

    gm = sys.modules.get("ai_mesh_gateway.main") or sys.modules.get("main")
    if gm is None:
        try:
            from ai_mesh_gateway import main as gm  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            try:
                import main as gm  # type: ignore[no-redef]
            except Exception:
                return None
    return gm


def _build_rag_policy(org_slug: str) -> dict:
    """Build the per-request DICT policy the RAGFirewallPipeline expects.

    Sets ``_org_slug`` (so the pipeline resolves the org's compiled policies +
    ranker rules) and merges the per-org FirewallConfig guardrail keys the
    QueryStage consumes (injection thresholds + input-scan gate) so the portable
    /v1/vector/query path honors the SAME frontend config the chat and
    /v1/rag/query paths honor. Fail-open: returns just ``{"_org_slug": ...}``
    when CONFIG_SYNC is unavailable.
    """
    policy: dict = {"_org_slug": org_slug or ""}
    if not org_slug:
        return policy
    gm = _gateway_main_module()
    config_sync = getattr(gm, "CONFIG_SYNC", None) if gm is not None else None
    if config_sync is None:
        return policy
    try:
        oc = config_sync.get_config(org_slug) or {}
    except Exception:  # noqa: BLE001 — never let config lookup break the query path
        return policy
    for _k in _RAG_GUARDRAIL_KEYS:
        if _k in oc:
            policy[_k] = oc[_k]
    return policy


# ────────────────────────────────────────────────────────────────────────────
#  AUTH + ORG RESOLUTION
# ────────────────────────────────────────────────────────────────────────────


async def _ensure_redis_client():
    """
    Lazy-init fallback for ``REDIS_CLIENT``.

    The intended wiring path is ``main.py`` calling ``_inject_vector_globals``
    at startup so the same shared connection is used across the gateway.
    That injection is not currently performed for vector_routes, which left
    every endpoint here returning 401/500 because ``REDIS_CLIENT`` stayed
    ``None``. To make the routes operable without requiring a wider
    lifespan refactor, fall back to a connection built from ``REDIS_URL``
    (the same env var used elsewhere in the gateway). Once main.py wires
    ``_inject_vector_globals`` properly, the injected client takes
    precedence and this fallback is a no-op.
    """
    global REDIS_CLIENT
    # Fast path: already initialized — avoid taking the lock at all.
    if REDIS_CLIENT is not None:
        return REDIS_CLIENT
    # M-12 — guard the lazy init with an asyncio.Lock so concurrent callers
    # don't each create a connection pool. Re-check inside the lock because
    # another coroutine may have populated REDIS_CLIENT while we waited.
    async with _REDIS_INIT_LOCK:
        if REDIS_CLIENT is not None:
            return REDIS_CLIENT
        try:
            import os
            import redis.asyncio as _redis_async
            url = os.environ.get("REDIS_URL") or os.environ.get("GATEWAY_REDIS_URL")
            if not url:
                return None
            REDIS_CLIENT = _redis_async.from_url(url, decode_responses=True)
            return REDIS_CLIENT
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning("vector_routes lazy redis init failed: %s", exc)
            return None


async def _resolve_org_from_token(authorization: Optional[str]) -> Optional[dict]:
    """
    Extract organization ID from a Gateway API Key bearer token.

    Returns ``{org_id, project_id, user_id}`` or ``None`` if auth fails.

    Bundle Q3 — single source of truth alignment.

    Earlier revisions of this function read a custom ``gateway:key:{token}``
    Redis HSET that no writer in the control plane ever populated. The
    canonical key shape published by ``core.signals.sync_gateway_apikey_to_redis``
    on every ``GatewayAPIKey.save()`` is::

        auth:apikey:{sha256_hex(raw_token)}  ->  JSON-encoded build_redis_payload()

    This is also exactly what ``AuthMiddleware`` (the gateway's primary
    auth check) reads via ``validate_api_key`` in ``middleware.py``. By
    aligning on that key here we get four benefits at once:

      • every existing key works without a backfill or manual seeding;
      • create / rotate / revoke flows already maintain it via signals;
      • we cannot diverge from middleware's view of an API key (no
        bypass via a stale duplicate cache shape);
      • the dead ``jwt_cache:{token[:16]}`` lookup is removed — nothing
        in the repo wrote that key, so it was always returning None.

    Note: ``project_id`` is the control-plane CharField (e.g.
    ``"mcp-default-zeroshield"``), so we deliberately keep it as a
    string here. Call sites already coerce via ``str(project_id)``
    before persistence so this is a no-op for them.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization[7:].strip()
    if not token:
        return None

    client = await _ensure_redis_client()
    if client is None:
        LOG.warning("Redis not available for token resolution")
        return None

    try:
        import hashlib

        key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        raw = await client.get(f"auth:apikey:{key_hash}")
        if not raw:
            return None

        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            LOG.warning("Corrupt auth:apikey payload for key prefix %s: %s", token[:8], exc)
            return None

        # Honor middleware's same is_active gate — defense in depth in case
        # signal-driven cache invalidation lags after a soft-disable.
        if payload.get("is_active") is False:
            return None

        org_id_raw = payload.get("organization_id")
        try:
            org_id = int(org_id_raw) if org_id_raw is not None else None
        except (TypeError, ValueError):
            org_id = None

        return {
            "organization_id": org_id,
            "org_id": org_id,
            "project_id": payload.get("project_id") or None,
            "user_id": payload.get("user_id"),
            # org_slug is the key CONFIG_SYNC.get_config(org_slug) is indexed by;
            # surfacing it here lets the data-plane handlers resolve per-org
            # guardrail config (e.g. input_scan_enabled) without re-reading Redis.
            "org_slug": payload.get("org_slug") or "",
        }
    except Exception as exc:
        LOG.warning("Token resolution failed: %s", exc)
        return None


async def _resolve_vector_provider_for_org(org_id: int) -> Optional[dict]:
    """
    Fetch org's active vector provider config. Returns the config dict or None.

    Resolution goes through VECTOR_PROVIDER_SYNC — the same in-memory cache the
    gateway already keeps warm from Redis and refreshes over the
    ``vector_provider_updates`` pub/sub channel.

    This used to scan Redis directly for ``vector:provider:{org_id}:*``. Nothing
    has ever written that pattern: the control plane's VectorProviderConfig
    signal writes per-org COMPILED bundles under
    ``vector:providers:compiled:{org_id}`` (RAG-16 split the old all-tenant key
    to shrink credential blast radius), and vector_provider_sync.py reads
    exactly those. So this function always found zero keys and every
    /v1/vector/* request 400'd with no_provider even for orgs whose provider was
    configured, active, and loading fine into the sync cache.

    Reading through the sync keeps ONE source of truth and — unlike re-adding a
    duplicate key — does not scatter a second plaintext copy of the provider and
    embedding API keys across Redis.
    """
    if VECTOR_PROVIDER_SYNC is None:
        LOG.warning("Vector provider sync not initialized")
        return None

    try:
        providers = VECTOR_PROVIDER_SYNC.get_org_providers(org_id)
        if not providers:
            LOG.debug("No active vector provider config for org_id=%s", org_id)
            return None
        config = providers[0]  # orgs typically have exactly one
        LOG.debug(
            "Resolved vector provider for org=%s: type=%s",
            org_id,
            config.get("provider_type"),
        )
        return config
    except Exception as exc:
        LOG.exception("Failed to resolve vector provider for org_id=%s: %s", org_id, exc)
        return None


# ────────────────────────────────────────────────────────────────────────────
#  POST /v1/vector/query — Retrieve documents (through firewall)
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/query",
    summary="Query vector database with policy enforcement",
    tags=["Vector Operations"],
    status_code=200,
)
async def query_vector_db(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Query organization's vector database through the RAG Firewall.

    **Request Body:**
    ```json
    {
      "query": "What is the refund policy?",
      "collection_name": "documents",
      "n_results": 5,
      "where": {"source": "policy"}  # Optional metadata filter
    }
    ```

    **Response:**
    All responses include `x-zeroshield` metadata with policy enforcement status.

    On success (200):
    ```json
    {
      "documents": [
        {
          "id": "doc-123",
          "text": "The refund policy states...",
          "metadata": {"source": "policy", "date": "2024-01-15"}
        }
      ],
      "x-zeroshield": {
        "request_id": "zs-abc123",
        "action": "allow|flag|redact|block",
        "reason": "All checks passed",
        "firewall_stage": "ranker"
      }
    }
    ```

    On block (403):
    ```json
    {
      "error": "blocked",
      "reason": "Query matched malicious pattern: SQL injection",
      "x-zeroshield": {
        "request_id": "zs-abc123",
        "action": "block",
        "threat_type": "prompt_injection",
        "matched_patterns": ["sql_injection"]
      }
    }
    ```
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "unauthorized",
                    "message": "Valid Bearer token required",
                },
            )

        org_id = auth_context["org_id"]
        user_id = auth_context.get("user_id")

        # Parse request body
        body = await request.json()
        # M-12 — type-confusion hardening: a non-object body (list/str/number)
        # or non-string/non-int fields must yield a clean 400, never a 500 from
        # calling .strip()/int() on the wrong type.
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "bad_request", "code": "invalid_body"},
            )
        _query_raw = body.get("query", "")
        query_text = _query_raw.strip() if isinstance(_query_raw, str) else ""
        _collection_raw = body.get("collection_name", "documents")
        collection_name = (
            _collection_raw.strip() if isinstance(_collection_raw, str) else ""
        )
        try:
            n_results = int(body.get("n_results", 5))
        except (TypeError, ValueError, OverflowError):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "bad_request", "code": "invalid_n_results"},
            )
        where_filter = body.get("where")

        if not query_text:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "query required"},
            )

        # Bound query length so an oversized prompt can't drive an expensive
        # embedding call or starve the firewall pipeline. Mirrors the RAG path's
        # GATEWAY_RAG_MAX_QUERY_LENGTH; falls back to a safe default when the
        # gateway config has not been injected.
        try:
            max_query_length = int((CONFIG or {}).get("rag_max_query_length", 2000) or 2000)
        except (TypeError, ValueError):
            max_query_length = 2000
        if max_query_length > 0 and len(query_text) > max_query_length:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "query_too_long",
                    "message": f"Query exceeds maximum length of {max_query_length} characters",
                },
            )

        if n_results < 1 or n_results > 1000:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "n_results must be between 1 and 1000"},
            )

        # M-10 — org-ownership gate on the user-supplied collection name.
        # The query is namespaced by the vector client under the caller's
        # authenticated project_id ({project_id}__{collection_name}). Reject
        # any collection name that carries the "__" tenant separator (or other
        # disallowed characters), which would let a caller escape their own
        # prefix and read another org's namespace. Legitimate single-tenant
        # names (e.g. "documents", "policy-docs") pass unchanged.
        if not _is_owned_collection_name(collection_name):
            LOG.warning(
                "Rejected cross-tenant collection access: org=%s collection=%r",
                org_id, collection_name,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "forbidden",
                    "message": "Collection is not accessible for this organization",
                },
            )

        # Resolve org's vector provider config
        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            LOG.warning("No vector provider configured for org_id=%s", org_id)
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "no_provider",
                    "message": "Organization has not configured a vector database provider",
                },
            )

        provider_type = provider_config.get("provider_type", "pinecone")
        # B6: org-isolated vector namespace — bind to the immutable org_id, not the
        # client-settable project_id (same format as main._org_ns_project_id so
        # /v1/vector/* and /v1/rag/* land in the same namespace for a given org).
        project_id = f"org{org_id}-{auth_context.get('project_id') or 'default'}"

        # Execute query through RAG Firewall
        if RAG_PIPELINE is None:
            LOG.error("RAG pipeline not initialized")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "rag_unavailable"},
            )

        # Determine org slug for org-scoped policy lookup
        middleware_auth = getattr(request.state, "auth_context", None)
        org_slug = middleware_auth.org_slug if middleware_auth else "default"

        # M-04: actor identity for actor-scoped policies (ranker-stage filtering).
        actor = None
        if middleware_auth is not None or user_id is not None:
            actor = {
                "user_id": getattr(middleware_auth, "user_id", None) or user_id,
                "agent_id": getattr(middleware_auth, "prefix", None) or "",
                "roles": list(getattr(middleware_auth, "roles", None) or []),
            }

        try:
            rag_verdict = await RAG_PIPELINE.execute(
                query_text=query_text,
                collection_name=collection_name,
                project_id=str(project_id),
                vector_db_type=provider_type,
                n_results=n_results,
                where_filter=where_filter,
                # Build a DICT policy (see _build_rag_policy): passing the raw
                # POLICY_SYNC.get_policies() list here raised AttributeError in
                # the pipeline. The pipeline resolves the org's compiled policies
                # internally from ``_org_slug``.
                policy=_build_rag_policy(org_slug),
                actor=actor,
            )
        except Exception as exc:
            LOG.exception("RAG pipeline execution failed: %s", exc)
            # Never surface the raw embedder/litellm exception to the client.
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "pipeline_error"},
            )

        # Check verdict action
        if rag_verdict.action in ("block", "deny"):
            LOG.info(
                "Query blocked for org=%s (reason logged server-side only)",
                org_id,
            )
            # A BLOCK is the event a SOC most needs, and this path used to return
            # without emitting anything: allowed queries were recorded and blocked
            # ones vanished, so the retrieval lane showed zero evidence for exactly
            # the requests the firewall acted on. /v1/vector/upsert already emits on
            # its all-blocked path; query was the outlier. Emitted BEFORE the return,
            # and never carrying the detection reason — that stays server-side, which
            # is what the response redaction below is protecting.
            if TELEMETRY:
                TELEMETRY.emit({
                    "event_type": "vector_query",
                    "action": "block",
                    "organization_id": org_id,
                    "org_id": org_id,
                    "user_id": user_id,
                    "provider_type": provider_type,
                    "collection_name": collection_name,
                    "firewall_action": rag_verdict.action,
                    "threat_type": rag_verdict.scan_verdict.get("threat_type", "rag_threat"),
                    "status_code": 403,
                })
            # ── SECURITY FIX: Don't expose detection details to client ──
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "blocked",
                    "message": "Request blocked due to security policy",
                    "x-zeroshield": {
                        "request_id": rag_verdict.scan_verdict.get("request_id", ""),
                        "action": "block",
                        "threat_type": rag_verdict.scan_verdict.get("threat_type", "rag_threat"),
                    },
                },
            )

        # Emit telemetry
        if TELEMETRY:
            TELEMETRY.emit({
                "event_type": "vector_query",
                "action": "query",
                "organization_id": org_id,
                "org_id": org_id,
                "user_id": user_id,
                "provider_type": provider_type,
                "collection_name": collection_name,
                "firewall_action": rag_verdict.action,
                "status_code": 200,
            })

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "documents": rag_verdict.documents or [],
                "x-zeroshield": {
                    "request_id": rag_verdict.scan_verdict.get("request_id", ""),
                    "action": rag_verdict.action,
                    # ── SECURITY FIX: Don't expose internal details like reason/stage ──
                    "firewall_enabled": True,
                },
            },
        )

    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_json"},
        )
    except Exception as exc:
        LOG.exception("Vector query failed: %s", exc)
        # Generic body — keep raw detail server-side (logged above) only.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )


async def _scan_redact_upsert_documents(
    doc_ids: list,
    doc_texts: list,
    doc_metas: list,
    org_slug: str,
) -> tuple[list, list, list, list, bool]:
    """Apply the SAME content-scan + PII-redaction the /v1/rag/ingest path applies,
    to the documents/text/metadata of a /v1/vector/upsert batch — closing the gap
    where the portable vector endpoint wrote vectors directly with NO scan and NO
    redaction (unlike rag_ingest).

    For each document, in lockstep over the parallel id/text/metadata lists:
      (a) CONTEXT_GUARD.scan_single_document(text) — DROP the doc when the guard
          BLOCKS it (credentials / injection / hidden-instruction), mirroring
          rag_ingest's per-doc block;
      (b) redact PII in the text via main._scan_redact_embedding_inputs (gated by
          input_scan_enabled). When a value carries PII that genuinely cannot be
          masked, the helper returns a block — DROP that doc (fail-closed), exactly
          like rag_ingest's partial-success contract;
      (c) redact PII in metadata VALUES via main._scan_redact_metadata.

    Returns ``(kept_ids, kept_texts, kept_metas, scan_results, all_blocked)`` with
    the three surviving lists index-aligned. ``scan_results`` records the per-index
    decision (parity with rag_ingest). ``all_blocked`` is True when every document
    was dropped, so the caller can return the same 422 rag_ingest uses.

    Fail-safe: when the gateway scanners are unavailable (None) or main cannot be
    imported, the batch passes through UNCHANGED (no crash) — the upstream
    embedding-validity guard in vector_client still applies.
    """
    # Lazy, function-local import of the gateway singletons (same idiom as
    # mcp_proxy / mcp_scan_orchestrator) — avoids a circular import at module load
    # (main imports vector_routes at startup).
    # Resolve the LIVE app module from sys.modules (same defect class as
    # _get_input_scanner/_get_policy_sync, #18/#19): gunicorn loads
    # ``ai_mesh_gateway.main`` and its startup handler sets CONTEXT_GUARD/CONFIG_SYNC
    # on THAT module object. A bare ``import main`` resolves a *different* module
    # object (same file, separate namespace) whose startup never ran → those globals
    # stayed None → the embedding-input scan silently PASSED THROUGH. Prefer the
    # packaged module already in sys.modules; only import fresh as a last resort.
    import sys

    gateway_main = sys.modules.get("ai_mesh_gateway.main") or sys.modules.get("main")
    if gateway_main is None:
        try:
            from ai_mesh_gateway import main as gateway_main  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001 — never let an import error block a write path
            try:
                import main as gateway_main  # type: ignore[no-redef]
            except Exception:
                return doc_ids, doc_texts, doc_metas, [], False

    context_guard = getattr(gateway_main, "CONTEXT_GUARD", None)
    config_sync = getattr(gateway_main, "CONFIG_SYNC", None)
    base_config = getattr(gateway_main, "CONFIG", None)
    scan_redact_text = getattr(gateway_main, "_scan_redact_embedding_inputs", None)
    scan_redact_meta = getattr(gateway_main, "_scan_redact_metadata", None)

    # Resolve per-org guardrail config (input_scan_enabled lives here). Falls back
    # to the base CONFIG so the input-scan gate still resolves before per-org sync.
    org_config: dict = {}
    if config_sync is not None and org_slug:
        try:
            org_config = config_sync.get_config(org_slug) or {}
        except Exception:  # noqa: BLE001
            org_config = {}
    if not org_config and isinstance(base_config, dict):
        org_config = base_config

    kept_ids: list = []
    kept_texts: list = []
    kept_metas: list = []
    scan_results: list = []

    for i in range(len(doc_texts)):
        text = doc_texts[i]
        text_str = text if isinstance(text, str) else ("" if text is None else str(text))
        meta = doc_metas[i] if i < len(doc_metas) else {}
        action = "allow"
        threats: list[str] = []

        # (a) Content scan — DROP on guard block (credentials/injection/hidden).
        if context_guard is not None and text_str:
            try:
                verdict = await context_guard.scan_single_document(text_str)
            except TypeError:
                verdict = context_guard.scan_single_document(text_str)  # type: ignore[assignment]
            except Exception:  # noqa: BLE001 — a scanner error must not 500 the write
                LOG.warning("Upsert content scan failed (fail-open) for doc index %d", i)
                verdict = None
            if verdict is not None:
                if getattr(verdict, "threat_type", ""):
                    threats.append(verdict.threat_type)
                if getattr(verdict, "action", "allow") == "block":
                    action = "block"

        if action == "block":
            scan_results.append({"index": i, "action": "block", "threats": threats})
            continue

        # (b) Redact PII in the text (gated by input_scan_enabled inside the helper).
        if scan_redact_text is not None and text_str:
            try:
                _red, _blk = await scan_redact_text([text_str], org_config)
            except Exception:  # noqa: BLE001
                LOG.warning("Upsert text redaction failed (fail-open) for doc index %d", i)
                _red, _blk = [text_str], None
            if _blk is not None:
                # PII detected but could not be masked → fail-closed (drop the doc),
                # matching rag_ingest's partial-success contract.
                scan_results.append({
                    "index": i, "action": "block", "threats": ["pii"],
                    "reason": _blk.get("reason"),
                })
                continue
            if _red:
                text_str = _red[0]

        # (c) Redact PII in metadata VALUES.
        if scan_redact_meta is not None and isinstance(meta, dict) and meta:
            try:
                meta = await scan_redact_meta(meta, org_config)
            except Exception:  # noqa: BLE001
                LOG.warning("Upsert metadata redaction failed (fail-open) for doc index %d", i)

        kept_ids.append(doc_ids[i] if i < len(doc_ids) else f"doc-{i}")
        kept_texts.append(text_str)
        kept_metas.append(meta)
        scan_results.append({"index": i, "action": action, "threats": threats})

    all_blocked = len(doc_texts) > 0 and not kept_texts
    return kept_ids, kept_texts, kept_metas, scan_results, all_blocked


# ────────────────────────────────────────────────────────────────────────────
#  POST /v1/vector/upsert — Insert/update documents (through firewall)
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/upsert",
    summary="Insert or update documents in vector database",
    tags=["Vector Operations"],
    status_code=201,
)
async def upsert_vector_documents(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Insert or update documents in organization's vector database.

    **Request Body:**
    ```json
    {
      "documents": [
        {
          "id": "doc-1",
          "text": "The refund policy states that...",
          "metadata": {"source": "policy", "date": "2024-01-15"}
        }
      ],
      "collection_name": "documents"
    }
    ```

    **Response (201):**
    ```json
    {
      "inserted_count": 1,
      "upserted_count": 0,
      "x-zeroshield": {
        "request_id": "zs-def456",
        "action": "allow",
        "reason": "All checks passed"
      }
    }
    ```
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized"},
            )

        org_id = auth_context["org_id"]
        user_id = auth_context.get("user_id")

        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "bad_request", "code": "invalid_body"},
            )
        documents = body.get("documents", [])
        _collection_raw = body.get("collection_name", "documents")
        collection_name = (
            _collection_raw.strip() if isinstance(_collection_raw, str) else ""
        )

        # M-10 — org-ownership gate on the user-supplied collection name.
        # The upsert is namespaced by the vector client under the caller's
        # authenticated project_id ({project_id}__{collection_name}). Reject any
        # collection name carrying the "__" tenant separator (or other
        # disallowed characters) so a caller cannot write into another org's
        # namespace via a crafted collection name.
        if not _is_owned_collection_name(collection_name):
            LOG.warning(
                "Rejected cross-tenant collection access: org=%s collection=%r",
                org_id, collection_name,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "forbidden",
                    "message": "Collection is not accessible for this organization",
                },
            )

        if not documents:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "documents required"},
            )

        if not isinstance(documents, list):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "documents must be a list"},
            )

        # Bundle Q2 — bound the batch size. Without this, a single
        # request could pin gateway memory (each document is embedded
        # before write) and starve other tenants. The hard ceiling is
        # 1000 docs (configurable via VECTOR_UPSERT_MAX_DOCS); clients
        # that need more must paginate.
        try:
            max_docs = int(os.getenv("VECTOR_UPSERT_MAX_DOCS", "1000"))
        except ValueError:
            max_docs = 1000
        if len(documents) > max_docs:
            LOG.info(
                "Rejecting oversized vector upsert: org=%s count=%d max=%d",
                org_id, len(documents), max_docs,
            )
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "error": "batch_too_large",
                    "message": (
                        f"Upsert batch limited to {max_docs} documents; "
                        f"got {len(documents)}. Paginate the request."
                    ),
                    "max_documents": max_docs,
                },
            )

        # I7: aggregate-char cap (parity with /v1/rag/ingest + /v1/embeddings) so a
        # batch under the doc-count limit can't still exhaust the worker with a few
        # huge documents. R12: count text + id + metadata (not just text) — a small
        # `text` with a multi-MB `metadata`/`id` blob otherwise slipped the cap and
        # still amplified memory/payload downstream.
        _max_chars = int(os.getenv("VECTOR_UPSERT_MAX_CHARS", "2000000"))

        def _doc_char_weight(d):
            if not isinstance(d, dict):
                return 0
            w = len(str(d.get("text", ""))) + len(str(d.get("id", "")))
            _m = d.get("metadata")
            if _m is not None:
                w += len(str(_m))
            return w

        if sum(_doc_char_weight(d) for d in documents) > _max_chars:
            return JSONResponse(
                status_code=413,
                content={"error": "payload_too_large", "code": "vector_input_too_large", "max_chars": _max_chars},
            )

        # Each document must be an OBJECT. A list element that is a string/int/
        # null would raise AttributeError on the ``.get()`` calls below (-> 500);
        # reject malformed elements with a clean 400 instead. (Checked after the
        # size cap so an oversized list is rejected without iterating it.)
        if not all(isinstance(d, dict) for d in documents):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "bad_request",
                    "message": "each document must be an object",
                    "code": "invalid_documents",
                },
            )

        # Resolve provider
        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "no_provider",
                    "message": "Organization has not configured a vector database provider",
                },
            )

        provider_type = provider_config.get("provider_type", "pinecone")
        # B6: org-isolated vector namespace — bind to the immutable org_id, not the
        # client-settable project_id (same format as main._org_ns_project_id so
        # /v1/vector/* and /v1/rag/* land in the same namespace for a given org).
        project_id = f"org{org_id}-{auth_context.get('project_id') or 'default'}"

        # Get vector client
        vector_client = _resolve_runtime_vector_client(provider_type, provider_config)
        if not vector_client:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "provider_not_available"},
            )

        try:
            # Perform upsert through vector client
            doc_ids = [d.get("id", f"doc-{i}") for i, d in enumerate(documents)]
            doc_texts = [d.get("text", "") for d in documents]
            doc_metas = [d.get("metadata", {}) for d in documents]

            # ── Parity with /v1/rag/ingest: content scan + PII redaction ──
            # The portable vector endpoint previously wrote vectors directly with
            # NO content scan and NO redaction. Apply the SAME guard rag_ingest
            # applies before embedding/writing: drop guard-blocked docs
            # (credentials/injection), redact PII in text + metadata. Surviving
            # ids/texts/metas stay index-aligned (partial-success contract).
            doc_ids, doc_texts, doc_metas, _scan_results, _all_blocked = (
                await _scan_redact_upsert_documents(
                    doc_ids, doc_texts, doc_metas,
                    org_slug=str(auth_context.get("org_slug") or ""),
                )
            )
            if _all_blocked:
                if TELEMETRY:
                    TELEMETRY.emit({
                        "event_type": "vector_upsert",
                        "action": "block",
                        "organization_id": org_id,
                        "org_id": org_id,
                        "user_id": user_id,
                        "provider_type": provider_type,
                        "collection_name": collection_name,
                        "document_count": 0,
                        "status_code": 422,
                    })
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={
                        "error": "all_documents_blocked",
                        "message": "All document(s) were blocked by content scanning.",
                        "code": "vector_content_blocked",
                        "scan_results": _scan_results,
                    },
                )

            if hasattr(vector_client, "upsert"):
                count = await vector_client.upsert(
                    collection_name=collection_name,
                    documents=doc_texts,
                    ids=doc_ids,
                    metadatas=doc_metas,
                    project_id=str(project_id),
                )
            else:
                count = await vector_client.add(
                    collection_name=collection_name,
                    documents=doc_texts,
                    ids=doc_ids,
                    metadatas=doc_metas,
                    project_id=str(project_id),
                )

            if TELEMETRY:
                TELEMETRY.emit({
                    "event_type": "vector_upsert",
                    "action": "upsert",
                    "organization_id": org_id,
                    "org_id": org_id,
                    "user_id": user_id,
                    "provider_type": provider_type,
                    "collection_name": collection_name,
                    "document_count": count,
                    "status_code": 201,
                })

            return JSONResponse(
                status_code=status.HTTP_201_CREATED,
                content={
                    "inserted_count": count,
                    "upserted_count": 0,
                    "x-zeroshield": {
                        "request_id": f"zs-upsert-{org_id}",
                        "action": "allow",
                        "reason": "Documents inserted successfully",
                    },
                },
            )

        except Exception as exc:
            LOG.exception("Vector upsert failed: %s", exc)
            # Mirror the delete handler: generic body, no raw embedder/
            # litellm exception text leaked to the client.
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "upsert_failed"},
            )

    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_json"},
        )
    except Exception as exc:
        LOG.exception("Vector upsert handler failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )


# ────────────────────────────────────────────────────────────────────────────
#  POST /v1/vector/delete — Delete documents
# ────────────────────────────────────────────────────────────────────────────


@router.post(
    "/delete",
    summary="Delete documents from vector database",
    tags=["Vector Operations"],
    status_code=204,
)
async def delete_vector_documents(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Delete documents from organization's vector database.

    **Request Body:**
    ```json
    {
      "document_ids": ["doc-1", "doc-2"],
      "collection_name": "documents"
    }
    ```

    **Response (204):** No content on success.
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized"},
            )

        org_id = auth_context["org_id"]
        user_id = auth_context.get("user_id")

        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "bad_request", "code": "invalid_body"},
            )
        doc_ids = body.get("document_ids", [])
        _collection_raw = body.get("collection_name", "documents")
        collection_name = (
            _collection_raw.strip() if isinstance(_collection_raw, str) else ""
        )

        # M-10 — org-ownership gate on the user-supplied collection name.
        # The delete is namespaced by the vector client under the caller's
        # authenticated project_id ({project_id}__{collection_name}). Reject any
        # collection name carrying the "__" tenant separator (or other
        # disallowed characters) so a caller cannot delete from another org's
        # namespace via a crafted collection name.
        if not _is_owned_collection_name(collection_name):
            LOG.warning(
                "Rejected cross-tenant collection access: org=%s collection=%r",
                org_id, collection_name,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "forbidden",
                    "message": "Collection is not accessible for this organization",
                },
            )

        if not doc_ids:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "document_ids required"},
            )
        # R12 (#10): bound the delete batch — an unbounded document_ids list is a
        # memory/amplification vector symmetric to the upsert cap (I7).
        if not isinstance(doc_ids, list):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "bad_request", "code": "invalid_document_ids", "message": "document_ids must be a list"},
            )
        _max_delete = int(os.getenv("VECTOR_DELETE_MAX_IDS", "10000"))
        if len(doc_ids) > _max_delete:
            return JSONResponse(
                status_code=413,
                content={"error": "payload_too_large", "code": "too_many_document_ids", "max_ids": _max_delete},
            )

        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "no_provider"},
            )

        provider_type = provider_config.get("provider_type", "pinecone")
        # B6: org-isolated vector namespace — bind to the immutable org_id, not the
        # client-settable project_id (same format as main._org_ns_project_id so
        # /v1/vector/* and /v1/rag/* land in the same namespace for a given org).
        project_id = f"org{org_id}-{auth_context.get('project_id') or 'default'}"

        vector_client = _resolve_runtime_vector_client(provider_type, provider_config)
        if not vector_client:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "provider_not_available"},
            )

        try:
            await vector_client.delete(
                collection_name=collection_name,
                ids=doc_ids,
                project_id=str(project_id),
            )

            if TELEMETRY:
                TELEMETRY.emit({
                    "event_type": "vector_delete",
                    "action": "delete",
                    "organization_id": org_id,
                    "org_id": org_id,
                    "user_id": user_id,
                    "document_count": len(doc_ids),
                    "status_code": 204,
                })

            return JSONResponse(status_code=status.HTTP_204_NO_CONTENT)

        except Exception as exc:
            LOG.exception("Vector delete failed: %s", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "delete_failed"},
            )

    except json.JSONDecodeError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "invalid_json"},
        )
    except Exception as exc:
        LOG.exception("Vector delete handler failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )


# ────────────────────────────────────────────────────────────────────────────
#  GET /v1/vector/config — Retrieve org's vector provider config
# ────────────────────────────────────────────────────────────────────────────


@router.get(
    "/config",
    summary="Get organization's vector database configuration",
    tags=["Vector Operations"],
    status_code=200,
)
async def get_vector_config(
    authorization: Optional[str] = Header(None),
) -> JSONResponse:
    """
    Retrieve the organization's active vector database configuration.

    **Response:**
    ```json
    {
      "provider": {
        "provider_type": "pinecone",
        "display_name": "Customer Docs",
        "environment": "us-east-1",
        "embedding_model": "text-embedding-3-small",
        "is_active": true
      },
      "x-zeroshield": {
        "action": "allow"
      }
    }
    ```
    """
    try:
        auth_context = await _resolve_org_from_token(authorization)
        if not auth_context or not auth_context.get("org_id"):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized"},
            )

        org_id = auth_context["org_id"]

        provider_config = await _resolve_vector_provider_for_org(org_id)
        if not provider_config:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "error": "not_configured",
                    "message": "Organization has not configured a vector database provider",
                },
            )

        # Return config without exposing the API key
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "provider": {
                    "provider_type": provider_config.get("provider_type"),
                    "display_name": provider_config.get("display_name", ""),
                    "environment": provider_config.get("environment", ""),
                    "embedding_model": provider_config.get("embedding_model", ""),
                    "is_active": provider_config.get("is_active", True),
                },
                "x-zeroshield": {
                    "action": "allow",
                    "reason": "Configuration retrieved successfully",
                },
            },
        )

    except Exception as exc:
        LOG.exception("Get config failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error"},
        )
