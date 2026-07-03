#!/usr/bin/env python3
"""
AIGuardX Gateway Proxy.
Sits between clients and LLM; calls backend policy check and optional security scan;
registers as gateway agent and reports stats for SOC.
"""
import asyncio
import base64
import json
import logging
import os
import platform
import re
import sys
import time
import uvicorn
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = Path(__file__).resolve().parent
for _path in (_ROOT, _PKG):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse


try:
    from .config import load_config
    from .context_assembler import minimize_context
    from .jobs import enqueue_job
    from .policy_signing import signing_enforced, _get_signing_key
    from .telemetry_ops import (
        emit_operational_event,
        EVENT_CLASS_POLICY_HMAC_MISCONFIG,
    )
    from .bedrock_tier2_breaker import Tier2UnavailableStrict
except ImportError:
    from config import load_config
    from context_assembler import minimize_context
    from jobs import enqueue_job
    from policy_signing import signing_enforced, _get_signing_key
    from telemetry_ops import (
        emit_operational_event,
        EVENT_CLASS_POLICY_HMAC_MISCONFIG,
    )
    from bedrock_tier2_breaker import Tier2UnavailableStrict  # type: ignore[no-redef]


def _is_tier2_unavailable_strict(exc: BaseException) -> bool:
    """True if ``exc`` is a ``Tier2UnavailableStrict`` from EITHER namespace.

    The gateway package is importable both as ``ai_mesh_gateway.*`` and as
    top-level modules (PYTHONPATH includes the package dir), so
    ``bedrock_tier2_breaker`` loads TWICE with two DISTINCT
    ``Tier2UnavailableStrict`` classes (and two ``BREAKER`` singletons).
    ``scanner.py`` raises the class bound to its namespace; a plain
    ``except Tier2UnavailableStrict`` in this module is bound to the OTHER
    namespace and silently MISSES it, so the strict-breaker path returned a raw
    HTTP 500 instead of the degraded 451 envelope. Match by class name to catch
    both identities regardless of import order.
    """
    return isinstance(exc, Tier2UnavailableStrict) or type(exc).__name__ == "Tier2UnavailableStrict"


from ai_mesh_shared.openai_request_normalizer import normalize_openai_chat_request
from ai_mesh_gateway.rag_collections import (
    coerce_org_id,
    iter_vector_clients_for_org,
    list_collections_for_clients,
    no_provider_configured_payload,
    rag_vector_available,
    resolve_vector_client_for_org,
)

_gateway_public_url = (os.environ.get("GATEWAY_PUBLIC_URL", "") or "").strip().rstrip("/")
_backend_public_url = (os.environ.get("BACKEND_PUBLIC_URL", "") or "").strip().rstrip("/")
_backend_docs_url = f"{_backend_public_url}/docs/" if _backend_public_url else "/docs/"
_log_subscriber = None

LOG = logging.getLogger("gateway")
app = FastAPI(
    title="AIGuardX Gateway Proxy",
    description=(
        "## Data Plane — OpenAI-Compatible LLM Proxy\n\n"
        "The Gateway Proxy sits between client applications and LLM providers, "
        "enforcing security policies, performing content scanning, and routing "
        "requests via LiteLLM to 100+ LLM providers.\n\n"
        "### Authentication\n\n"
        "Requests require a **Gateway API Key** via `Authorization: Bearer <key>` header.\n\n"
        "Keys are created in the Control Plane (backend) and cached in Redis for "
        "zero-latency lookups. Each key carries project context, model allowlists, "
        "and rate limits.\n\n"
        "### How to Get a Gateway API Key\n\n"
        f"1. Go to the **Backend (Control Plane)** docs at `{_backend_docs_url}`\n"
        "2. Use `POST /api/auth/token/` to get a JWT token\n"
        "3. Use `POST /api/gateways/keys/` with the JWT token to create a Gateway API Key\n"
        "4. Copy the `key` field from the response (shown only once)\n"
        "5. Use it here as `Authorization: Bearer <key>`\n\n"
        "### How It Works\n\n"
        "1. Client sends an OpenAI-compatible request to `/v1/chat/completions`\n"
        "2. Gateway authenticates the request via Redis key lookup\n"
        "3. Prompt is sent to the backend for policy evaluation\n"
        "4. If allowed, request is forwarded to the configured LLM provider\n"
        "5. Response is optionally checked against policies before returning\n\n"
        "### Streaming\n\n"
        'Set `"stream": true` in the request body to receive Server-Sent Events (SSE).'
    ),
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    servers=[
        {"url": _gateway_public_url or "/", "description": "Gateway (Data Plane)"},
    ],
)

# ── Auth middleware (must be added before uvicorn starts) ──
_redis_url = os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0")
_auth_enabled = os.environ.get("GATEWAY_AUTH_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
_async_vector_ingest_enabled = os.environ.get("GATEWAY_ASYNC_VECTOR_INGEST", "true").lower() in ("true", "1", "yes")

if _auth_enabled:
    try:
        from .middleware import AuthMiddleware
    except ImportError:
        from middleware import AuthMiddleware

    app.add_middleware(AuthMiddleware, redis_url=_redis_url)

from starlette.middleware.cors import CORSMiddleware

# ── Prometheus observation middleware (Phase D) ──
# Records every request as an Counter+Histogram observation. Decision label
# distinguishes 2xx/3xx (allowed), 4xx (blocked), 5xx (error). Org label is
# resolved from auth_context if AuthMiddleware ran first, else "anonymous".
# Counters/gauges only, no PII in labels.
try:
    from .metrics import (
        inc_active_connections as _prom_inc_active,
        record_request as _prom_record_request,
    )
except ImportError:
    from metrics import (  # type: ignore[no-redef]
        inc_active_connections as _prom_inc_active,
        record_request as _prom_record_request,
    )


@app.exception_handler(ValueError)
@app.exception_handler(TypeError)
async def _bad_input_handler(request: Request, exc: Exception) -> JSONResponse:
    """Malformed/wrong-typed request fields (e.g. a non-str prompt sliced/stripped,
    a non-numeric max_tokens/n_results) → clean 400 instead of an unhandled 500
    that wastes a worker and may echo internals. Details are logged server-side."""
    LOG.warning("Bad request (%s) at %s: %s", type(exc).__name__, request.url.path, exc)
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_request", "message": "Invalid or malformed request parameter."},
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: never leak a traceback / internals to the client. Log server-side.

    PHASE-6 fix (EMB-500 / RESP-STORE-500): an exception that ESCAPES a /v1 handler
    (e.g. an unguarded ``await LLM_ROUTER.aembedding`` / ``store.save``) propagates
    THROUGH and bypasses the BaseHTTPMiddleware ``_openai_compat_shim`` — the shim can
    only post-process a response it received from ``call_next``. So this handler must
    ITSELF emit the nested OpenAI envelope + ``x-request-id``; otherwise the stock SDK
    sees a flat body / null e.code (or a transport error) with no request_id."""
    LOG.exception("Unhandled gateway error at %s", request.url.path)
    if str(request.url.path).startswith("/v1/"):
        try:
            from .responses_adapters import build_openai_error as _boe
        except ImportError:
            from responses_adapters import build_openai_error as _boe
        rid = (getattr(request.state, "gw_request_id", "")
               or request.headers.get("x-request-id")
               or f"zs-{_uuid.uuid4().hex[:12]}")
        body = _boe(500, "An internal gateway error occurred.",
                    code="gateway_internal_error", error_type="server_error")
        body["request_id"] = rid
        return JSONResponse(status_code=500, content=body, headers={"x-request-id": rid})
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "An internal gateway error occurred."},
    )


@app.exception_handler(StarletteHTTPException)
async def _openai_shaped_http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """OpenAI-SDK parity: for /v1/* paths, return the nested OpenAI error envelope
    ({"error":{message,type,code}}) instead of FastAPI's default {"detail": "..."}.
    Covers unimplemented endpoints (client.completions.create / models.retrieve /
    moderations / files / batches -> a clean OpenAI 404) and any HTTPException
    raised on the OpenAI surface. The SDK already raises the right exception CLASS
    by status code; this fixes the BODY shape so response.json()['error'] works.
    Non-/v1 paths keep FastAPI's default shape unchanged."""
    if str(request.url.path).startswith("/v1/"):
        try:
            from .responses_adapters import build_openai_error as _boe
        except ImportError:
            from responses_adapters import build_openai_error as _boe
        _detail = exc.detail if isinstance(exc.detail, str) and exc.detail else "Not Found"
        return JSONResponse(
            status_code=exc.status_code,
            content=_boe(exc.status_code, _detail),
            headers=getattr(exc, "headers", None),
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None),
    )


@app.middleware("http")
async def _prom_observe(request, call_next):
    # Skip self-observation on the metrics endpoint to avoid recursion noise.
    if request.url.path == "/metrics":
        return await call_next(request)
    _prom_inc_active(1)
    _t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = getattr(response, "status_code", 200)
        return response
    finally:
        elapsed_s = time.perf_counter() - _t0
        _prom_inc_active(-1)
        auth_ctx = getattr(request.state, "auth_context", None)
        org = getattr(auth_ctx, "org_slug", "") if auth_ctx else ""
        if status >= 500:
            decision = "error"
        elif status >= 400:
            decision = "blocked"
        else:
            decision = "allowed"
        try:
            _prom_record_request(org, decision, elapsed_s)
        except Exception:
            pass


@app.middleware("http")
async def _openai_compat_shim(request, call_next):
    """OpenAI-SDK exact-compat shim (single choke point — D2 + D3).

    For every ``/v1/*`` response:
      - D3: guarantee an ``x-request-id`` header (-> SDK ``response._request_id`` /
        ``error.request_id``), reusing the gateway's own request id when the handler
        set it on ``request.state.gw_request_id`` (so header == body).
      - D2: rewrite any FLAT error body ``{"error": "<str>", "message": ..., "code": ...}``
        into the nested OpenAI envelope ``{"error": {message,type,param,code}}`` via the
        existing ``coerce_chat_error_to_openai`` (idempotent on already-nested bodies), so
        the SDK populates ``e.code`` / ``e.type`` / ``e.param`` instead of ``None``.
        ZeroShield diagnostics (request_id, category, pipeline_trace, …) are preserved at
        the TOP level. Streaming (SSE) responses are passed through untouched.
    """
    if not request.url.path.startswith("/v1/"):
        return await call_next(request)
    response = await call_next(request)
    # SEAM-C precedence: PREFER a handler-set ``x-request-id`` so the shim never
    # clobbers a canonical, OpenAI-shaped id the handler chose (responses ``resp_…``,
    # moderations ``modr-…``, embeddings ``zs-emb-…`` threaded via gw_request_id). Only
    # mint a fresh id when no handler id and no canonical request id exist. The body
    # ``request_id`` (when present in an error body) is folded in by the error-rebuild
    # branch below, AFTER the body is buffered, so header == body == [SECURITY_BLOCK] log.
    _hdr_set = response.headers.get("x-request-id")
    rid = (_hdr_set
           or getattr(request.state, "gw_request_id", "")
           or request.headers.get("X-Request-ID")
           or request.headers.get("x-request-id")
           or f"zs-{_uuid.uuid4().hex[:12]}")
    # Never REPLACE a handler-set header with a different value; only set when absent.
    if not _hdr_set:
        try:
            response.headers["x-request-id"] = rid
        except Exception:
            pass
    ctype = response.headers.get("content-type", "")
    # Successes and streaming (SSE) bodies are left as-is (only the header was added).
    if response.status_code < 400 or "text/event-stream" in ctype or "application/json" not in ctype:
        return response
    # Error JSON: collect the (non-streaming) body and coerce it to the nested envelope.
    # NOTE: once body_iterator is consumed the original response can't be returned
    # (its body is exhausted), so EVERY path below rebuilds a fresh response from the
    # buffered bytes — never `return response`.
    _buf = b""
    try:
        async for _chunk in response.body_iterator:
            _buf += _chunk
    except Exception:
        pass
    _hdrs = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    # Phase-4 (P2-STREAM-429): a retryable upstream status (429/503) must carry a
    # Retry-After hint so the stock SDK's auto-backoff has a delay. Single choke point
    # covers EVERY 429/503 path; never override a value a handler already set.
    if response.status_code in (429, 503) and not any(k.lower() == "retry-after" for k in _hdrs):
        _hdrs["Retry-After"] = "1"
    try:
        parsed = json.loads(_buf) if _buf else {}
    except Exception:
        parsed = None
    # SEAM-C precedence (error branch): handler-set header > body request_id >
    # canonical gw_request_id > fresh. Folding in the body request_id here makes the
    # error header EQUAL the body the customer receives (and the [SECURITY_BLOCK] log
    # id, which shares the same _build_zeroshield_metadata request_id).
    rid = (_hdr_set
           or (parsed.get("request_id") if isinstance(parsed, dict) else "")
           or getattr(request.state, "gw_request_id", "")
           or request.headers.get("X-Request-ID")
           or request.headers.get("x-request-id")
           or rid)
    _hdrs["x-request-id"] = rid
    if not isinstance(parsed, dict):
        # Non-JSON / unparseable body — pass the original bytes through unchanged.
        return Response(content=_buf, status_code=response.status_code,
                        headers=_hdrs, media_type=ctype or None)
    try:
        try:
            from .responses_adapters import coerce_chat_error_to_openai as _cc
        except ImportError:
            from responses_adapters import coerce_chat_error_to_openai as _cc
        coerced = _cc(response.status_code, parsed)
    except Exception:
        coerced = parsed
    if isinstance(coerced, dict) and not coerced.get("request_id"):
        coerced["request_id"] = rid
    return JSONResponse(status_code=response.status_code, content=coerced, headers=_hdrs)


# SEC-01 FIX: Environment-based CORS origins instead of wildcard
def _split_csv_env(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


_cors_origins: list[str] = []
for _env_key in ("GATEWAY_CORS_ORIGINS", "FRONTEND_ORIGIN", "ASGI_ALLOWED_ORIGINS"):
    for _origin in _split_csv_env(os.environ.get(_env_key, "")):
        if _origin not in _cors_origins:
            _cors_origins.append(_origin)

if not _cors_origins:
    _cors_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8180",
        "http://127.0.0.1:8180",
        "http://localhost:3000",
    ]

# Always allow LOCAL-DEV origins (localhost, loopback, RFC-1918 LAN IPs on any
# port) IN ADDITION to the configured production origins. The shared .env pins
# GATEWAY_CORS_ORIGINS/FRONTEND_ORIGIN to prod hosts, so without this the local
# stack rejects dev browsers (e.g. the LAN IP used to bypass port shadowing).
# Safe: these hosts are only reachable on the local network, so an external
# attacker cannot originate a request from them.
_LOCAL_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|\[::1\]|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_LOCAL_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=[
        "accept",
        "content-type",
        "authorization",
        "x-user-id",
        "x-endpoint-id",
        "x-agent-data",
    ],
    expose_headers=["content-type", "content-length", "x-request-id"],
)

from fastapi.responses import HTMLResponse


@app.get("/docs", include_in_schema=False)
async def scalar_docs():
    """Serve Scalar API Reference UI."""
    return HTMLResponse(
        """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AIGuardX Gateway API Docs</title>
    <style>body { margin: 0; }</style>
</head>
<body>
    <script
        id="api-reference"
        data-url="/openapi.json"
        data-configuration='{"theme": "kepler", "hideDownloadButton": false}'
    ></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.25.55"></script>
</body>
</html>"""
    )


# Set at startup
CONFIG = None
CONFIG_SYNC = None
AGENT_ID = None
LLM_ROUTER = None
POLICY_SYNC = None
RATE_LIMITER = None
INPUT_SCANNER = None
VECTOR_POLICY_SYNC = None
VECTOR_CLIENTS: dict[str, object] = {}
VECTOR_PROVIDER_SYNC = None
CONTEXT_GUARD = None
REDIS_CLIENT = None
TELEMETRY = None
OUTPUT_GUARD = None

CIRCUIT_BREAKER = None
RAG_PIPELINE = None
BEDROCK_EMBEDDER = None
GROUNDING_GUARD = None

# Gateway version for auto-update check (should match backend expectation)
GATEWAY_VERSION = "0.1.0"

# In-memory metrics
METRICS = {
    "total_requests": 0,
    "blocked": 0,
    "allowed": 0,
    "sum_latency_ms": 0.0,
    "active_connections": 0,
}


def _http_request(method, url, data=None, api_key=None, timeout=30):
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data else None, method=method
    )
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("X-Agent-Key", api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except Exception:
            pass
        return e.code, body
    except Exception as e:
        LOG.exception("HTTP request failed")
        return None, str(e)

STARTUP_RETRY_MAX_ATTEMPTS = 5
STARTUP_RETRY_BASE_DELAY_SECONDS = 2.0
STARTUP_RETRY_MAX_DELAY_SECONDS = 30.0

# Boot-time control-plane calls (version check + gateway registration) must FAIL FAST.
# They are best-effort coordination, NOT enforcement: the gateway enforces from the Redis
# policy cache (POLICY_SYNC) with no control plane, and a `_background_register_loop`
# already retries registration forever with backoff once the app is serving. Previously
# these reused the STARTUP retry budget (5 attempts x ~30s timeout) AND registration nested
# that inside a 5-attempt outer loop (~25 attempts x 30s ≈ 12+ min), so when control was
# unhealthy at boot the lifespan `startup()` blocked for MANY MINUTES and every worker sat
# at "Waiting for application startup" — the gateway was unavailable even though Redis had
# the policies. A single short-timeout attempt per call bounds boot; the background loop
# (and enforcement) are unaffected.
STARTUP_CONTROL_CALL_TIMEOUT_SECONDS = 5.0


def _http_request_with_retry(
    method: str,
    url: str,
    data: dict | None = None,
    api_key: str | None = None,
    max_attempts: int = STARTUP_RETRY_MAX_ATTEMPTS,
    base_delay: float = STARTUP_RETRY_BASE_DELAY_SECONDS,
    max_delay: float = STARTUP_RETRY_MAX_DELAY_SECONDS,
    timeout: float = 30.0,
) -> tuple:
    import random

    last_code = None
    last_body = None

    for attempt in range(1, max_attempts + 1):
        code, body = _http_request(method, url, data=data, api_key=api_key, timeout=timeout)
        if code is not None:
            return code, body
        last_code = code
        last_body = body
        if attempt < max_attempts:
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            jitter = random.uniform(0, delay * 0.25)
            total_delay = delay + jitter
            LOG.warning(
                "Backend unreachable (attempt %d/%d). Retrying in %.1fs... [%s]",
                attempt, max_attempts, total_delay, url,
            )
            time.sleep(total_delay)

    LOG.error(
        "Backend unreachable after %d attempts. Gateway starting without registration. [%s]",
        max_attempts, url,
    )
    return last_code, last_body

def _parse_version(v):
    if not v:
        return (0, 0, 0)
    parts = []
    for s in str(v).strip().split(".")[:4]:
        try:
            parts.append(int(s))
        except ValueError:
            parts.append(0)
    return tuple(parts)


# ── SAFE_ERROR_CONTRACT: Client-safe error responses (BUG-1: Sensitive Data Leak Prevention) ──
# STRICT RULE: NEVER return raw prompts, secrets, PII patterns, or detection rules to clients.
# This is a CRITICAL SECURITY requirement.

_SCAN_THREAT_CATEGORIES = frozenset({
    "prompt_injection",
    "jailbreak",
    "goal_hijacking",
    "pii",
    "secret",
    "toxicity",
    "dos",
    "tool_overreach",
    "data_leakage",
    "rag_poisoning",
})


def _resolve_pipeline_blocked_by(
    *,
    code: str,
    threat_category: str,
    detection_tier: str = "",
) -> str:
    """Map a block to the pipeline stage that actually enforced it (not always policy)."""
    if code == "rate_limit_exceeded":
        return "rate_limit"
    if threat_category == "blocked_keyword":
        return "firewall_keywords"
    tier = (detection_tier or "").strip().lower()
    if tier in {"tier_1", "tier_1_5", "tier_2", "input_scan"}:
        return "input_scan"
    if tier == "policy":
        return "policy"
    if tier == "output_guard":
        return "output_guardrail"
    if tier == "threat_intel":
        return "policy"
    if threat_category in _SCAN_THREAT_CATEGORIES:
        return "input_scan"
    if threat_category in {"policy_violation", "compliance_violation"}:
        return "policy"
    if threat_category in {"high_risk_actor", "threat_intel"}:
        return "policy"
    return "input_scan"


def _scrub_trace_for_client(trace: dict | None) -> dict | None:
    """
    Return a deep-copied, evidence-stripped copy of a pipeline_trace that is safe
    to embed in a CLIENT-facing 403 body.

    The full trace (with matched_patterns / guard_findings / Evidence lines) is
    still kept on operator/telemetry channels — this helper ONLY sanitizes what
    ships to the requesting tenant, so a blocked caller cannot use the response as
    an oracle for which rule/pattern matched.

    Per-stage evidence keys are removed/emptied; any "Evidence:" detail lines are
    dropped, keeping stage name + action + category-level info only.
    """
    if not trace or not isinstance(trace, dict):
        return trace
    import copy as _copy

    scrubbed = _copy.deepcopy(trace)

    # Evidence-bearing keys to strip from each stage dict.
    _evidence_keys = (
        "matched_patterns",
        "matched_rules",
        "matched_policies",
        "guard_findings",
    )

    def _scrub_detail(detail):
        """Drop any 'Evidence:'/detail lines from a stage detail string."""
        if not isinstance(detail, str) or not detail:
            return detail
        kept = []
        for _line in detail.splitlines():
            _stripped = _line.strip()
            if _stripped.lower().startswith("evidence:"):
                continue
            kept.append(_line)
        return "\n".join(kept)

    def _scrub_stage_dict(d):
        if not isinstance(d, dict):
            return
        for _k in _evidence_keys:
            if _k in d:
                d[_k] = [] if isinstance(d[_k], list) else ""
        if "detail" in d:
            d["detail"] = _scrub_detail(d.get("detail"))
        if "guard_reason" in d:
            d["guard_reason"] = _scrub_detail(d.get("guard_reason"))

    _stages = scrubbed.get("stages")
    if isinstance(_stages, list):
        for _stage in _stages:
            _scrub_stage_dict(_stage)

    # Top-level guard_summary mirrors a stage guard dict (carries guard_findings).
    if isinstance(scrubbed.get("guard_summary"), dict):
        _scrub_stage_dict(scrubbed["guard_summary"])

    return scrubbed


# D-a (OpenAI-SDK exact-compat — block status): CONTENT-category blocks
# (prompt_injection / jailbreak / pii / secret / content-policy / toxicity ...) are
# emitted as HTTP 400 with error.code="content_filter" + error.type=
# "invalid_request_error" so the stock SDK raises BadRequestError and downstream
# LiteLLM/LangChain key on "content_filter". AUTHORIZATION / actor blocks
# (threat-intel high-risk actor, model-not-allowed) MUST stay at their current
# status (403 / 401) — they are NOT a content filter and a 400 would mislead
# callers into retrying with a "fixed" prompt. This deny-list names the categories
# that keep their incoming status; everything else flowing through this content
# funnel is a content filter.
_NON_CONTENT_BLOCK_CATEGORIES = frozenset({
    "high_risk_actor",   # threat-intel actor block (insufficient permissions)
    "threat_intel",
    "model_not_allowed",
    "tier2_degraded",    # service-unavailable surrogate (503-shaped)
})


def _resolve_content_block_status(status_code: int, threat_category: str) -> int:
    """Map a CONTENT-category 403 block to ``GATEWAY_BLOCK_STATUS`` (default 400).

    Only re-maps a content block that currently carries 403 — auth/actor
    categories and any non-403 status pass through untouched. The knob accepts
    "400" (default, new contract) or "403" (migration / rollback). Any other
    value is ignored (fail-safe to the default) and NEVER yields a retryable
    409/429/5xx.
    """
    if status_code != 403:
        return status_code
    if (threat_category or "").strip().lower() in _NON_CONTENT_BLOCK_CATEGORIES:
        return status_code
    raw = (os.getenv("GATEWAY_BLOCK_STATUS", "400") or "400").strip()
    if raw == "403":
        return 403
    return 400  # default + fail-safe for any unexpected value


def _is_redactable_pii_threat(threat_type: str) -> bool:
    """True when a scanner threat should trigger PII redaction before the upstream
    LLM (grafted from the parallel session's redaction-gap fix: bare phone / phi / pci)."""
    t = (threat_type or "").lower()
    if t in ("pii", "secret", "phi", "pci", "sensitive_content"):
        return True
    return "pii" in t or "phone" in t or t.startswith("phi") or t.startswith("pci")


def _build_safe_block_response(
    status_code: int,
    code: str,
    threat_category: str,
    request_id: str | None = None,
    internal_detail: str | None = None,
    detection_tier: str = "",
    pipeline_trace: dict | None = None,
) -> JSONResponse:
    """
    Build a client-safe error response for policy blocks.
    NEVER exposes:
      - raw prompts
      - detected secrets or PII
      - matched detection patterns/rules
      - specific threat details
    
    ONLY exposes:
      - generic policy violation message
      - request ID (for support reference)
      - threat category (generic)
      - action taken
    """
    import uuid
    _request_id = request_id or f"zs-{uuid.uuid4().hex[:12]}"
    
    # Safe category to message mapping (client-visible)
    safe_messages = {
        "prompt_injection": "Request blocked due to security policy",
        "jailbreak": "Request blocked due to security policy",
        "pii": "Request blocked due to security policy",
        "secret": "Request blocked due to security policy",
        "toxicity": "Request blocked due to security policy",
        "compliance_violation": "Request blocked due to compliance policy",
        "policy_violation": "Request blocked due to security policy",
        "threat_intel": "Request blocked: insufficient permissions",
        "mcp_injection": "Request blocked: invalid operation",
        "mcp_args_injection": "Request blocked: invalid operation",
        "model_not_allowed": "Model not in allowlist",
        "tier2_degraded": "Service temporarily unavailable",
    }
    
    message = safe_messages.get(threat_category, "Request blocked due to security policy")
    
    # Log internal details server-side ONLY (never in response)
    if internal_detail:
        LOG.warning(
            "[SECURITY_BLOCK] code=%s, category=%s, request_id=%s, detail=%s",
            code, threat_category, _request_id, internal_detail,
        )

    blocked_by = _resolve_pipeline_blocked_by(
        code=code,
        threat_category=threat_category,
        detection_tier=detection_tier,
    )
    # D-a: re-map CONTENT-category 403s to GATEWAY_BLOCK_STATUS (default 400) and
    # surface error.code="content_filter" so the stock SDK raises BadRequestError
    # and LiteLLM/LangChain key on "content_filter". Auth/actor blocks keep their
    # incoming status (and their original code). The ORIGINAL ZeroShield ``code``
    # is preserved at the TOP level for ZS/demo consumers (dual-key).
    effective_status = _resolve_content_block_status(status_code, threat_category)
    is_content_filter = effective_status == 400 and status_code == 403
    error_code = "content_filter" if is_content_filter else code
    error_type = "invalid_request_error" if is_content_filter else None
    # D2 (OpenAI-SDK exact-compat): the stock `openai` client reads
    # e.code / e.type / e.param / e.message from a NESTED body["error"] object. A
    # flat top-level "error":"blocked" string left all of those None, so customer
    # `except ... as e: if e.code == "content_filter"` blocks never fired. Emit the
    # nested OpenAI error envelope (responses_adapters.build_openai_error — the same
    # helper /v1/responses already uses). ZeroShield diagnostics stay MIRRORED at the
    # TOP level so existing ZS consumers + the /demo client keep working unchanged.
    from responses_adapters import build_openai_error as _build_openai_error
    content = {
        **_build_openai_error(
            effective_status, message, error_type=error_type, code=error_code
        ),
        "message": message,
        "code": code,  # ORIGINAL ZeroShield code preserved for ZS/demo consumers
        "request_id": _request_id,  # For support inquiries only
        "category": threat_category,  # Generic: NOT specific threat type
        "blocked_by": blocked_by,
        "detection_tier": detection_tier or "",
        "pipeline_stage": blocked_by,
    }
    if pipeline_trace:
        # Strip per-stage evidence (matched_patterns / guard_findings / Evidence
        # lines) before shipping the trace to the CLIENT — leaving exactly which
        # rule/pattern fired in a 403 body is an evasion oracle. Operator and
        # telemetry channels still receive the full, unscrubbed trace.
        content["pipeline_trace"] = _scrub_trace_for_client(pipeline_trace)
    # D3: surface the request id as the SDK-native x-request-id header so
    # response._request_id / error.request_id are populated on every block.
    return JSONResponse(
        status_code=effective_status,
        content=content,
        headers={"x-request-id": _request_id},
    )


def _redact_for_client_response(zeroshield_dict: dict | None) -> dict | None:
    """
    Take internal zeroshield metadata and produce a client-safe version.
    REMOVES all potentially sensitive fields.
    """
    if not zeroshield_dict or not isinstance(zeroshield_dict, dict):
        return None
    
    # Operator dashboard fields (Module 1.1 simulator) — no raw prompts/responses.
    safe_fields = {
        "request_id",
        "action",
        "detection_tier",
        "processing_time_ms",
        "reason",
        "detail",
        "threat_type",
        "confidence",
        "matched_patterns",
        "routing_reason",
        "decision_source",
        "policy_summary",
        "decision_factors",
        "weights",
        "selected_model",
        "original_model",
        "routed_model",
        "rerouted",
        "routing",
        "reason_code",
        "guard_reason",
        "guard_action",
        "guard_model",
        "recommended_action",
        "guard_findings",
        "enforcement_source",
        "scan_outcome",
        "risk_score",
    }
    redacted = {k: v for k, v in zeroshield_dict.items() if k in safe_fields}

    # MODEL-ID LEAK FIX: the allowlist keeps "routing" WHOLE, but that nested dict
    # carries routed_model_id = the RAW upstream provider id (e.g.
    # "anthropic/claude-3.5-haiku") plus internal id forms. Scrub the upstream id
    # from inside routing — the org-facing selected_model/routed_model already
    # convey the resolved model. (Devil's-advocate-flagged + caught live in the
    # simulator network response during frontend validation.)
    if isinstance(redacted.get("routing"), dict):
        redacted["routing"] = {
            k: v for k, v in redacted["routing"].items()
            if k not in ("routed_model_id", "model_id")
        }

    # DO NOT include:
    # - reason (exposes why blocked)
    # - detail (exposes threat specifics)
    # - matched_patterns (exposes detection rules)
    # - compliance_tags (exposes compliance framework details)
    # - original_prompt_hash (can aid targeting)
    # - redacted_prompt / redacted_response (may still contain PII)
    # - threat_type (too specific)
    # - confidence (aids evasion)
    
    return redacted if redacted else None


def _check_version():
    """Optional: call version endpoint and log if update available or below min."""
    cfg = CONFIG
    if not cfg or not cfg.get("backend_url"):
        return
    url = f"{cfg['backend_url']}/api/agents/version/"
    # Best-effort informational call — bound it so an unhealthy control plane can't stall boot.
    code, body = _http_request_with_retry(
        "GET", url, api_key=cfg.get("api_key"),
        max_attempts=1, timeout=STARTUP_CONTROL_CALL_TIMEOUT_SECONDS,
    )
    if code != 200 or not isinstance(body, dict):
        return
    min_ver = body.get("min_version") or "0"
    rec_ver = body.get("recommended_version") or min_ver
    download_url = (body.get("download_url") or "").strip()
    current = _parse_version(GATEWAY_VERSION)
    min_t = _parse_version(min_ver)
    rec_t = _parse_version(rec_ver)
    if current < min_t:
        LOG.warning(
            "Gateway version %s is below required minimum %s. Consider updating.",
            GATEWAY_VERSION,
            min_ver,
        )
    elif rec_t > current and download_url:
        LOG.info("Gateway update available: %s. Download: %s", rec_ver, download_url)


def _latin1_safe_headers(headers):
    """HTTP header values must be latin-1 encodable. Reroute reasons contain a
    '→' arrow (e.g. "Kill-switch reroute: gpt-5.2 → Haiku"), and starlette raises
    UnicodeEncodeError building the response → 500. Transliterate the arrows and
    drop any other non-latin-1 char so a response can never 500 on header
    encoding. The response BODY keeps the original unicode (used by the UI)."""
    if not headers:
        return headers
    safe = {}
    for k, v in headers.items():
        s = str(v)
        # FD: CR/LF/NUL and other C0/DEL control chars ARE latin-1-encodable, so
        # they slip past the UnicodeEncodeError branch and reach uvicorn's wire
        # serializer, which raises RuntimeError AFTER the response has started
        # (header injection / aborted half-sent response). Collapse them to a
        # space unconditionally so the "can never 500 on header encoding"
        # invariant actually holds (the reroute reason comes from LLM-adjudicator
        # free text whose internal newlines survive .strip()).
        s = re.sub(r"[\r\n\x00-\x1f\x7f]", " ", s)
        try:
            s.encode("latin-1")
        except UnicodeEncodeError:
            s = s.replace("→", "->").replace("←", "<-")
            s = s.encode("latin-1", "replace").decode("latin-1")
        safe[k] = s
    return safe


def _register():
    global AGENT_ID
    cfg = CONFIG
    url = f"{cfg['backend_url']}/api/gateways/instances/register/"
    payload = {
        "agent_type": "gateway",
        "name": cfg["gateway_name"],
        "endpoint_identifier": cfg.get("gateway_location")
        or platform.node()
        or "gateway",
    }
    # Single fast attempt: the caller (startup outer loop OR _background_register_loop)
    # owns retry/backoff, so NEVER nest the inner 5x30s budget here (that was the ~12min
    # boot stall when control was unhealthy).
    code, body = _http_request_with_retry(
        "POST", url, data=payload, api_key=cfg.get("api_key"),
        max_attempts=1, timeout=STARTUP_CONTROL_CALL_TIMEOUT_SECONDS,
    )
    if code in (200, 201) and body.get("agent_id"):
        AGENT_ID = body["agent_id"]
        LOG.info("Registered gateway agent_id=%s", AGENT_ID)
        return True
    LOG.error("Registration failed: %s %s", code, body)
    return False


async def _background_register_loop() -> None:
    """Keep trying to register until it succeeds, then start telemetry.

    A worker that loses the startup registration race (or hits a transient
    backend error) must NOT give up for its lifetime — otherwise it is stuck on
    the degraded sync path (no model routing) forever. Retry with capped backoff.
    """
    global AGENT_ID
    backoff = 5
    while not AGENT_ID:
        await asyncio.sleep(backoff)
        try:
            if await asyncio.to_thread(_register):
                LOG.info("Background re-registration succeeded: agent_id=%s", AGENT_ID)
                asyncio.create_task(_telemetry_loop())
                return
        except Exception as exc:  # never let the loop die
            LOG.warning("Background re-registration attempt failed: %s", exc)
        backoff = min(backoff * 2, 60)


def _build_policy_actor(auth_ctx, user_id=None):
    """M-04: assemble the actor identity dict for actor-scoped policy checks.

    ``agent_id`` is the authenticated API-key prefix (always trustworthy),
    ``user_id``/``roles`` come from the key's auth context, with the
    client-supplied X-User-ID header as user_id fallback for attribution.
    Returns None when there is no auth context at all, preserving the
    no-actor (policy-applies) behaviour.
    """
    if auth_ctx is None and user_id is None:
        return None
    return {
        "user_id": getattr(auth_ctx, "user_id", None) or user_id,
        "agent_id": getattr(auth_ctx, "prefix", None) or "",
        "roles": list(getattr(auth_ctx, "roles", None) or []),
    }


def _policy_check(
    prompt,
    response_text="",
    user_id=None,
    endpoint_id=None,
    agent_data=None,
    project_id=None,
    risk_score=None,
    model=None,
    actor=None,
):
    cfg = CONFIG
    url = f"{cfg['backend_url']}/api/policy/check/"
    payload = {
        "prompt": prompt,
        "response": response_text or "",
        "agent_id": AGENT_ID,
        "user_id": user_id,
        "endpoint_id": endpoint_id,
    }
    if project_id is not None:
        payload["project_id"] = project_id
    if risk_score is not None:
        payload["risk_score"] = risk_score
    if model:
        payload["metadata"] = {"model": str(model)}
    if agent_data is not None and isinstance(agent_data, dict):
        payload["agent_data"] = agent_data
    # M-04: actor identity ({user_id, agent_id (API-key prefix), roles}) for
    # actor-scoped policies. Distinct from top-level "agent_id" (the gateway's
    # registered agent UUID).
    if actor is not None and isinstance(actor, dict):
        payload["actor"] = actor
    return _http_request_with_retry("POST", url, data=payload, api_key=cfg.get("api_key"))


def _policy_check_cached(
    prompt,
    response_text="",
    user_id=None,
    endpoint_id=None,
    agent_data=None,
    project_id=None,
    risk_score=None,
    model=None,
    org_slug="default",
    actor=None,
):
    """
    Evaluate prompt/response against cached policies locally.
    Falls back to HTTP _policy_check() if cache is not loaded.

    ``actor`` (M-04): optional {user_id, agent_id, roles} dict used to scope
    actor-allowlisted policies. Build it with _build_policy_actor(auth_ctx).

    Returns (status_code, response_dict) matching the backend
    /api/policy/check/ response shape.
    """
    if POLICY_SYNC is None or not POLICY_SYNC.is_loaded:
        if CONFIG.get("policy_cache_require_loaded", True):
            LOG.warning("Policy cache unavailable; fail-closed policy check")
            return 503, {
                "action": "block",
                "matched_policies": [],
                "matched_rules": [],
                "matched_policy_names": [],
                "matched_policy_severities": [],
                "matched_policy_categories": [],
                "matched_rule_descriptions": [],
                "message": "Policy cache unavailable. Request blocked by fail-closed policy.",
            }
        LOG.debug("Policy cache not loaded, falling back to backend HTTP")
        return _policy_check(
            prompt, response_text, user_id, endpoint_id,
            agent_data, project_id, risk_score, model,
            actor=actor,
        )

    from policy_engine import evaluate, apply_redaction
    try:
        from .policy_sync import filter_policies_by_domain
    except ImportError:
        from policy_sync import filter_policies_by_domain

    compiled_policies = filter_policies_by_domain(
        POLICY_SYNC.get_policies(org_slug), "pipeline"
    )
    result = evaluate(
        prompt=prompt,
        response_text=response_text,
        compiled_policies=compiled_policies,
        actor=actor,
    )

    response = {
        "action": result.action,
        "matched_policies": result.matched_policy_codes,
        "matched_rules": result.matched_rule_names,
        "matched_policy_ids": result.matched_policy_ids,
        "matched_rule_ids": result.matched_rule_ids,
        "matched_policy_names": result.matched_policy_names,
        "matched_policy_severities": result.matched_policy_severities,
        "matched_policy_categories": result.matched_policy_categories,
        "matched_rule_descriptions": result.matched_rule_descriptions,
        "message": result.message,
    }

    # FIX-1.2a: surface the winning model_downgrade rule's target so the chat
    # path downgrades to the rule-specified model instead of falling back to a
    # same-model default (a no-op that still faked a 'downgrade' telemetry event).
    if result.action == "model_downgrade" and getattr(result, "model_downgrade_target", ""):
        response["redaction_config"] = {"downgrade_to": result.model_downgrade_target}

    if result.action == "redact" and result.redaction_hints:
        if prompt:
            response["redacted_prompt"] = apply_redaction(prompt, result.redaction_hints)
        if response_text:
            response["redacted_response"] = apply_redaction(response_text, result.redaction_hints)

    return 200, response


# The advisory backend security scan (org deep_scan / call_security_scan) is
# BEST-EFFORT: its result can only ADD a high-certainty block (main.py ~5896 and
# ~7841) — it NEVER gates an allow, and the local policy engine + Tier-1/Tier-2
# scanners are the authoritative enforcement. It therefore must FAIL FAST so a slow
# or unhealthy control plane can never stall the inline request path.
#
# Root cause it fixes: this call previously reused _http_request_with_retry's
# STARTUP-grade defaults (5 attempts x up to ~30s backoff + 30s/attempt timeout).
# The chat handler AWAITS the deep-scan task before returning the response
# (main.py ~7840), so when control was unhealthy every ALLOWED chat request hung
# ~40s and timed out — even though the model itself answered in ~2s. A single
# short-timeout attempt bounds the added latency; on failure the response is
# delivered on the strength of the local scanners (no fail-open, no regression).
SECURITY_SCAN_MAX_ATTEMPTS = 1
SECURITY_SCAN_TIMEOUT_SECONDS = 4.0


def _security_scan(prompt, response_text=""):
    cfg = CONFIG
    url = f"{cfg['backend_url']}/api/security/scan/"
    payload = {"prompt": prompt, "response": response_text or ""}
    return _http_request_with_retry(
        "POST",
        url,
        data=payload,
        api_key=cfg.get("api_key"),
        max_attempts=int(cfg.get("security_scan_max_attempts", SECURITY_SCAN_MAX_ATTEMPTS)),
        timeout=float(cfg.get("security_scan_timeout_seconds", SECURITY_SCAN_TIMEOUT_SECONDS)),
    )


def _org_ns_project_id(auth_ctx) -> str:
    """B6: bind the vector-store namespace to the IMMUTABLE organization_id.

    The data namespace is built as ``{project_id}__{collection}`` and project_id
    is client-settable and was NEVER org-validated, so two tenants that chose the
    same project_id (e.g. the common 'default'/'e2e') shared one physical
    namespace — a cross-tenant read. Prefixing the org id (taken from the
    authenticated key payload, not client-controllable) makes the namespace
    tenant-isolated regardless of project_id. Applied at EVERY project_id
    resolution so reads and writes land in the SAME namespace. project_id is kept
    inside the prefix for sub-org labeling. (Existing project_id-namespaced
    vectors are orphaned by this rekey and must be re-ingested.)
    """
    if auth_ctx is None:
        return "default"
    oid = getattr(auth_ctx, "organization_id", None)
    pid = getattr(auth_ctx, "project_id", None)
    base = str(pid) if pid else "default"
    # R12 (#18): never emit a BARE namespace for a null org — a bare "default"
    # could be shared across distinct null-org callers. Prefix it so it can never
    # collide with a real org's "org{N}-" namespace. (Org-only inference is
    # enforced upstream, so a null org should not reach here — defensive.)
    return f"org{oid}-{base}" if oid is not None else f"orgNone-{base}"


def _extract_prompt_from_responses_input(input_value, instructions=None):
    """Flatten an OpenAI Responses ``input`` (str | list of input items) + optional
    ``instructions`` into a single scannable prompt string. Mirrors
    _extract_prompt_from_messages: fold every text-bearing field, emit a stable
    placeholder for non-text parts (so multimodal base64 never trips the length cap)."""
    parts: list[str] = []
    if isinstance(instructions, str) and instructions.strip():
        parts.append(instructions)
    if isinstance(input_value, str):
        parts.append(input_value)
    elif isinstance(input_value, list):
        for item in input_value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    _t = c.get("text")
                    if isinstance(_t, str):
                        parts.append(_t)
                    elif c.get("type") not in (None, "input_text", "output_text", "text"):
                        parts.append(f"[{c.get('type', 'non_text')}]")
            # bare text on the item (some shapes)
            _it = item.get("text")
            if isinstance(_it, str):
                parts.append(_it)
    return "\n".join(p for p in parts if p)


def _extract_responses_output_text(response) -> str:
    """Concatenate every text-bearing output channel of a Responses object for the
    OUTPUT guard: output[].content[].text (output_text), plus top-level output_text
    if the provider supplied it. Mirrors _extract_scannable_output_text."""
    if not isinstance(response, dict):
        return ""
    parts: list[str] = []
    _ot = response.get("output_text")
    if isinstance(_ot, str) and _ot:
        parts.append(_ot)
    for item in (response.get("output") or []):
        if not isinstance(item, dict):
            continue
        for c in (item.get("content") or []):
            if isinstance(c, dict):
                _t = c.get("text")
                if isinstance(_t, str) and _t:
                    parts.append(_t)
    return "\n".join(parts)


def _set_responses_output_text(response, text: str) -> None:
    """Enforcement: replace the Responses output text with ``text`` across every
    output_text content block + the top-level output_text convenience field."""
    if not isinstance(response, dict):
        return
    if "output_text" in response:
        response["output_text"] = text
    _wrote = False
    for item in (response.get("output") or []):
        if not isinstance(item, dict):
            continue
        for c in (item.get("content") or []):
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                c["text"] = text if not _wrote else ""
                _wrote = True


def _parts_reveal_value(text: str) -> bool:
    """G70: True if ``text`` carries a PII/secret/credential value. Used to decide whether a
    NO-SEPARATOR concatenation of a message's text content-parts exposes a value the
    space-join hid (a value split mid-token across parts). Detector import is lazy."""
    try:
        from patterns import detect_pii, detect_secrets, detect_credential_exposure
    except ImportError:  # pragma: no cover - packaging fallback
        from .patterns import detect_pii, detect_secrets, detect_credential_exposure
    return bool(detect_pii(text) or detect_secrets(text) or detect_credential_exposure(text))


def _extract_prompt_from_messages(messages):
    """Build a single prompt string from OpenAI-style messages."""
    parts = []
    for m in messages or []:
        role = m.get("role", "")
        content = m.get("content") or ""
        if isinstance(content, list):
            _seg = []
            _text_parts = []
            for c in content:
                if not isinstance(c, dict):
                    continue
                _txt = c.get("text")
                if isinstance(_txt, str):
                    _seg.append(_txt)
                    _text_parts.append(_txt)
                else:
                    # F5: never fold a non-text part's payload (image_url/data: URI,
                    # input_audio, file…) into scan_text — its multi-KB base64 blob
                    # trips the MAX_PROMPT_LENGTH DoS cap and breaks legit OpenAI
                    # multimodal vision. Emit a stable placeholder; text parts are
                    # still fully scanned.
                    _seg.append(f"[{c.get('type') or 'non-text'}]")
            content = " ".join(_seg)
            # G70: the model receives the text parts CONCATENATED (the API inserts no
            # space between text content-parts), so a PII/secret/credential value split
            # MID-TOKEN across parts (["…my ssn is 123-","45-6789"]) is contiguous to the
            # model but the space-join above breaks the pattern -> it egressed unscanned.
            # When the no-separator concatenation of the text parts REVEALS a value the
            # space-join hid, append it so the scanner sees what the model sees. Guarded
            # (reveals-a-value + space-join didn't) so benign multi-part content and the
            # injection space-join (which needs word spaces) are unchanged.
            if len(_text_parts) >= 2:
                _joined = "".join(_text_parts)
                if (_joined and _joined != content
                        and _parts_reveal_value(_joined) and not _parts_reveal_value(content)):
                    content = content + "\n" + _joined
        # Bracket the gateway's OWN role label ("[system]:" not "system:") so the
        # role-spoof injection signature (scanner.py: `(system|developer):\s*you
        # (are|have|must|will)`) does NOT false-positive on a LEGITIMATE system
        # message like "You are a helpful assistant" once flattened. A genuine
        # attack — a user smuggling a forged "\nsystem: you are now DAN" inside
        # their OWN message content — keeps its bare (unbracketed) "system:" colon
        # and is still caught. Without the bracket, every "You are …" system prompt
        # (the single most common system prompt) was blocked as prompt_injection.
        # G60: fold the participant ``name`` into the scanned label. A ``name`` reaches
        # the model and CAN carry digit-PII — an SSN/phone/CC ("123-45-6789") fits the
        # OpenAI name charset [a-zA-Z0-9_-], so it egressed UNscanned (only content +
        # tool_calls were folded). Folding it here is injection-FP-safe (a short
        # identifier is never an imperative injection phrase); a name that IS an SSN is a
        # true positive. Enforcement drops a PII-bearing name (llm_router._apply_redaction).
        _name = m.get("name")
        _label = f"{role}/{_name}" if isinstance(_name, str) and _name else role
        parts.append(f"[{_label}]: {content}")
        # G7: fold an assistant message's tool_calls (function name + arguments)
        # into the scannable text. Injection / PII / credentials hidden inside
        # tool_calls[].function.arguments previously bypassed Tier-1/Tier-2
        # entirely (only message *content* was scanned) — a live fail-open.
        for _tc in (m.get("tool_calls") or []):
            if not isinstance(_tc, dict):
                continue
            _fn = _tc.get("function") or {}
            _name = _fn.get("name") or ""
            _args = _fn.get("arguments")
            if not isinstance(_args, str):
                try:
                    _args = json.dumps(_args) if _args is not None else ""
                except (TypeError, ValueError):
                    _args = ""
            if _name or _args:
                parts.append(f"{role}.tool_call[{_name}]: {_args}")
    return "\n".join(parts)


def _extract_tool_definitions_text(tools) -> str:
    """G7: fold top-level ``tools[].function.{name,description}`` into scannable
    text so a prompt-injection smuggled in a tool *definition* is scanned too."""
    if not isinstance(tools, list):
        return ""
    parts: list[str] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        name = fn.get("name") or ""
        desc = fn.get("description") or ""
        if name or desc:
            parts.append(f"tool_def[{name}]: {desc}")
    return "\n".join(parts)


def _extract_agent_data(body: dict, x_agent_data: str | None):
    """agent_data from body.agent_data, body.mcp_context, or X-Agent-Data header.

    OpenAI-SDK callers inject MCP / structured context via
    ``extra_body={"mcp_context": {...}}`` (the documented Scenario-4 shape). It is
    treated identically to ``agent_data`` — scanned for PII/secrets and folded into
    the input scan + MCP governance telemetry — so the same security applies
    regardless of which key the client uses.
    """
    if body:
        _bad = body.get("agent_data")
        if _bad is None:
            _bad = body.get("mcp_context")
        if isinstance(_bad, dict):
            return _bad
        # A body-level string agent_data is scanned too (parity with the
        # non-JSON header path) so it cannot bypass the input scanner.
        if isinstance(_bad, str) and _bad.strip():
            return _bad
    if x_agent_data:
        try:
            decoded = base64.b64decode(x_agent_data).decode("utf-8")
        except Exception:
            return None
        try:
            return json.loads(decoded)
        except Exception:
            # Non-JSON-but-decodable header: return the raw text so it is still
            # threat-scanned (folded into scan_text below) rather than silently
            # dropped — a bare except-pass here would be a future injection bypass.
            return decoded if (decoded or "").strip() else None
    return None


def _content_to_text(content) -> str:
    """Coerce an OpenAI message ``content`` field to a plain ``str``.

    G57: content may be a ``str`` OR a LIST of content-part dicts (multimodal /
    content blocks — some providers/models return the assistant answer this way).
    A list is joined from its ``text`` parts. Mirrors the streaming coercion
    (secure_streaming ``_extract_content_delta`` FIX-C) so the NON-stream output
    guard sees list-shaped content instead of scanning an empty string — an
    all-list-content answer previously yielded empty scan text, which SKIPPED the
    output guard entirely (``_og_scan_text`` falsy) and egressed PII/secrets raw.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text") or ""
            for p in content
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
    # G62: a non-conforming DICT content (e.g. a single content-part {"type":"text",
    # "text":"…"} returned bare instead of wrapped in a list) otherwise coerced to ""
    # and SKIPPED the output guard entirely — the exact G57 bypass class for the dict
    # shape (reasoning_content/refusal already handle dict via G61). Fold only a str
    # ``text`` value (never an image/binary payload -> no base64 bloat).
    if isinstance(content, dict):
        _t = content.get("text")
        return _t if isinstance(_t, str) else ""
    return ""


def _tool_arg_to_text(value) -> str:
    """Coerce a tool-call ``name``/``arguments`` value to scannable text.

    G58: per the OpenAI spec ``arguments`` is a JSON *string*, but some providers /
    proxies (and LiteLLM in certain paths) return a PARSED DICT (or other non-str).
    The output scan + enforcement previously only handled ``str`` here, so PII/
    secrets in a dict-shaped argument egressed UNscanned AND survived neutralization
    (the guard saw benign content -> verdict allow -> raw egress; or a redact verdict
    blanked only str channels while the dict argument shipped verbatim). Mirrors the
    INPUT-side coercion already done in _extract_prompt_from_messages.
    """
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return ""


def _extract_response_from_completion(completion):
    """Extract assistant response text from chat completion (non-streaming)."""
    choices = completion.get("choices") or []
    if not choices:
        return ""
    c = choices[0]
    msg = c.get("message") or c.get("delta") or {}
    return _content_to_text(msg.get("content"))


def _extract_scannable_output_text(completion) -> str:
    """Output text fed to the OUTPUT guard — folds the PRIMARY content AND the
    secondary text-bearing channels (reasoning_content, tool_calls function
    name/arguments) so a secret/PII present only in a reasoning or tool-call
    channel still triggers a verdict. The streaming guard already scans these
    (secure_streaming FIX-A/FIX-B); the non-stream path previously scanned
    content only — an asymmetric output-guard bypass.
    """
    choices = completion.get("choices") or []
    if not choices:
        return ""
    parts: list[str] = []
    # R12 (#14): fold EVERY choice, not just choices[0]. With n>1 the model
    # returns multiple completions; scanning only the first let PII/secrets in
    # choices[1..] ship unscanned (output-guard bypass).
    for ch in choices:
        if not isinstance(ch, dict):
            continue
        msg = ch.get("message") or ch.get("delta") or {}
        if not isinstance(msg, dict):
            continue
        # R12 (#15): include `refusal` (model-authored text channel).
        # G57: `content` may be a LIST of content-part dicts (multimodal); coerce it
        # to text (stream parity — secure_streaming FIX-C) so list-shaped content is
        # scanned instead of skipped (an all-list answer otherwise yields empty scan
        # text and the output guard is bypassed entirely).
        # G61: `reasoning_content` / `refusal` can ALSO be non-str (structured reasoning
        # blocks — a list of dicts, or a dict; Anthropic-style thinking uses a `thinking`
        # key, not `text`). Coerce via _tool_arg_to_text (json.dumps for any non-str) so
        # PII in a structured reasoning/refusal channel is scanned regardless of shape —
        # these are text-only channels (no image parts) so JSON coercion is safe.
        for _v in (
            _content_to_text(msg.get("content")),
            _tool_arg_to_text(msg.get("reasoning_content")),
            _tool_arg_to_text(msg.get("refusal")),
        ):
            if _v:
                parts.append(_v)
        # R12 (#16): audio-output transcript channel.
        _au = msg.get("audio")
        if isinstance(_au, dict):
            _t = _au.get("transcript")
            if isinstance(_t, str) and _t:
                parts.append(_t)
        for tc in (msg.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            for _k in ("name", "arguments"):
                # G58: coerce a non-str (dict) ``arguments`` to JSON text so a
                # dict-shaped tool-call payload is scanned, not skipped.
                _v = _tool_arg_to_text(fn.get(_k))
                if _v:
                    parts.append(_v)
        # I5: legacy `function_call` channel (pre-tool_calls API shape) — fold it too.
        _fc = msg.get("function_call")
        if isinstance(_fc, dict):
            for _k in ("name", "arguments"):
                _v = _tool_arg_to_text(_fc.get(_k))
                if _v:
                    parts.append(_v)
    return "\n".join(parts)


def _neutralize_secondary_output_channels(msg: dict) -> None:
    """Blank the secondary text channels (reasoning_content + tool_call function
    name/arguments) when the output guard has MODIFIED the primary content
    (redact/rewrite/block). They were scanned together, but the regex redactor
    only rewrote ``content``; clearing these prevents an unscanned secret in a
    reasoning/tool channel shipping after enforcement. Only called from
    _set_completion_response_text (enforcement-only — the allow path never sets
    the text, so legitimate reasoning/tool_calls survive)."""
    try:
        # G61: blank a TRUTHY reasoning_content of ANY type (a structured list/dict
        # reasoning channel was left verbatim by the str-only check -> post-redact leak).
        if msg.get("reasoning_content"):
            msg["reasoning_content"] = ""
        for tc in (msg.get("tool_calls") or []):
            if isinstance(tc, dict) and isinstance(tc.get("function"), dict):
                for _k in ("name", "arguments"):
                    # G58: blank a TRUTHY value of ANY type (a dict-shaped
                    # ``arguments`` was left verbatim by the str-only check, shipping
                    # its PII after a redact verdict). Neutralize to "" regardless.
                    if tc["function"].get(_k):
                        tc["function"][_k] = ""
        # I5: blank the legacy function_call channel on enforcement too.
        _fc = msg.get("function_call")
        if isinstance(_fc, dict):
            for _k in ("name", "arguments"):
                if _fc.get(_k):
                    _fc[_k] = ""
        # R12 (#15): blank the `refusal` text channel on enforcement.
        # G61: blank a TRUTHY refusal of ANY type (structured list/dict refusal too).
        if msg.get("refusal"):
            msg["refusal"] = ""
        # R12 (#16): blank an audio-output transcript on enforcement.
        # R14: also blank audio.data — the base64 audio bytes carry the SPOKEN
        # content, so redacting only the transcript still ships the secret as
        # audio to the client. Drop both on enforcement.
        _au = msg.get("audio")
        if isinstance(_au, dict):
            if isinstance(_au.get("transcript"), str) and _au.get("transcript"):
                _au["transcript"] = ""
            if isinstance(_au.get("data"), str) and _au.get("data"):
                _au["data"] = ""
    except Exception:  # noqa: BLE001 - neutralization must never crash the response
        pass


async def _resolve_rag_context_chunks(request) -> list[str]:
    """Resolve RAG context chunks for output-guard grounding from the request.

    M-05: hallucination grounding must score the answer against the ACTUAL
    retrieved RAG context, never against the user's own prompt. Mirrors the
    inline (non-streaming) output-guard block: read the RAG context id header and
    fetch the chunk list from Redis. Best-effort / fail-open — any failure (no
    header, no Redis, bad payload) returns ``[]`` so grounding takes its safe
    no-context branch and the path never crashes.
    """
    context_chunks: list[str] = []
    try:
        rag_context_id = request.headers.get("X-ZeroShield-RAG-Context-ID", "") if request else ""
    except Exception:
        rag_context_id = ""
    if rag_context_id and REDIS_CLIENT is not None:
        try:
            import json as _json_ctx
            raw = await REDIS_CLIENT.get(rag_context_id)
            if raw:
                rag_chunks = _json_ctx.loads(raw if isinstance(raw, str) else raw.decode())
                if isinstance(rag_chunks, list):
                    context_chunks.extend(str(c) for c in rag_chunks)
        except Exception:
            pass  # fail-open: context binding is best-effort
    return context_chunks


async def _apply_output_guard_nonstream(
    resp,
    *,
    org_config: dict,
    org_slug: str,
    body: dict,
    user_id,
    project_id,
    key_prefix: str,
    prompt: str,
    start: float,
    prompt_snippet: str = "",
    endpoint_id=None,
    request=None,
):
    """Run the output guard on a non-streaming completion for code paths that
    return BEFORE the main inline output-guard block — notably the
    ``sync_pre_llm`` tier-2 path, which otherwise ships the model's response
    without any output scanning (so §1.7 never fires). Mirrors the
    credential→block / PII→redact / IP→flag enforcement and emits the same
    ``output_guard`` telemetry. Returns a JSONResponse to return early (block),
    otherwise None (``resp`` mutated in place for redact/rewrite).
    """
    if OUTPUT_GUARD is None or not isinstance(resp, dict):
        return None
    response_text = _extract_response_from_completion(resp)
    # F4: scan content + reasoning_content + tool_calls (not content alone), so a
    # secret/PII in a secondary channel still triggers a verdict.
    _scan_text = _extract_scannable_output_text(resp)
    if not _scan_text:
        return None
    if not CONFIG.get("output_guard_enabled", True):
        return None
    if not org_config.get("output_scan_enabled", CONFIG.get("output_scan_enabled", True)):
        return None
    # M-05(b): ground the guard against ACTUAL retrieved RAG context (if any)
    # instead of the previously hardcoded empty list. Fail-open to [] when no
    # request/RAG context is available, preserving prior behavior.
    _context_chunks = await _resolve_rag_context_chunks(request)
    try:
        verdict = await OUTPUT_GUARD.inspect(
            _scan_text,
            context_chunks=_context_chunks,
            org_config=(CONFIG_SYNC.get_config(org_slug) if (CONFIG_SYNC is not None and org_slug) else None),
            org_slug=org_slug or "",
        )
    except Exception:  # noqa: BLE001 - output guard must never crash the response
        LOG.exception("Output guard inspect failed (sync_pre_llm path); failing open")
        return None
    if verdict is None or verdict.action == "allow":
        return None

    raw_output = response_text[:500]
    incident_logging = bool(org_config.get("output_incident_logging_enabled", True))
    common = dict(
        model=body.get("model", ""),
        user_id=user_id,
        project_id=str(project_id or ""),
        key_prefix=key_prefix,
        threat_type=verdict.threat_type,
        compliance_tags=getattr(verdict, "compliance_tags", []),
        pipeline_stage="generator",
        prompt_snippet=prompt_snippet,
        endpoint_id=endpoint_id,
    )

    def _meta(sanitized):
        return {
            "detail": verdict.detail,
            "response_snippet": raw_output,
            "raw_output": raw_output,
            "sanitized_output": sanitized,
            "guardrail_reasoning": verdict.detail,
            "matched_patterns": getattr(verdict, "matched_patterns", []),
            **_telemetry_owasp_metadata(verdict.threat_type),
        }

    if verdict.action == "block":
        METRICS["blocked"] = METRICS.get("blocked", 0) + 1
        _emit_telemetry(
            status_code=403,
            event_type="output_guard",
            action="block",
            risk_score=getattr(verdict, "confidence", 0.9),
            latency_ms=(time.perf_counter() - start) * 1000,
            metadata=_meta("[BLOCKED]"),
            **common,
        )
        return _build_block_response(
            403,
            "output_blocked",
            _build_zeroshield_metadata(
                action="block",
                reason=f"Output guard detected {verdict.threat_type} in LLM response.",
                detection_tier="output_guard",
                threat_type=verdict.threat_type,
                confidence=getattr(verdict, "confidence", 0.9),
                matched_patterns=getattr(verdict, "matched_patterns", []),
                compliance_tags=getattr(verdict, "compliance_tags", []),
                original_prompt=prompt,
                detail=verdict.detail,
                processing_time_ms=(time.perf_counter() - start) * 1000,
                security_incident=True,
            ),
            prompt=prompt,
            requested_model=body.get("model", ""),
            output_scan_verdict=verdict,
        )

    if verdict.action == "rewrite":
        # FIX-1.7c: 'rewrite' is NOT redaction. The sync_pre_llm path previously
        # routed rewrite through _sanitize_output_for_verdict (the redact codepath),
        # so the rewrite action silently degraded to a static/redacted string here
        # while the inline output-guard path did genuine re-inference. Mirror the
        # inline path: re-infer a sanitized response through the org's own routed
        # model (falls back to the deterministic static rewrite internally).
        rewritten, _rw_reinferred = await _rewrite_output_response_text_via_router(
            verdict.threat_type, verdict.detail, response_text, body
        )
        _set_completion_response_text(resp, rewritten)
        if incident_logging:
            _emit_telemetry(
                status_code=200,
                event_type="output_guard",
                action="rewrite",
                risk_score=getattr(verdict, "confidence", 0.7),
                latency_ms=(time.perf_counter() - start) * 1000,
                metadata={
                    **_meta(rewritten[:500] if rewritten else ""),
                    "rewrite_reinferred": _rw_reinferred,
                    "rewrite_degraded": not _rw_reinferred,
                },
                **common,
            )
        return None

    if verdict.action == "redact":
        sanitized = _sanitize_output_for_verdict(response_text, verdict)
        _set_completion_response_text(resp, sanitized)
        # Telemetry honesty: only claim action="redact" when bytes actually
        # changed. A semantic (tier-2) verdict can target content the regex
        # redactor has no pattern for, leaving the output verbatim — that is a
        # "flag", not a redaction, so the 1.7 dashboard must not record a
        # phantom redaction for a response delivered unchanged.
        _redact_action = "redact" if sanitized != response_text else "flag"
        if incident_logging:
            _emit_telemetry(
                status_code=200,
                event_type="output_guard",
                action=_redact_action,
                risk_score=getattr(verdict, "confidence", 0.7),
                latency_ms=(time.perf_counter() - start) * 1000,
                metadata={**_meta(sanitized[:500] if sanitized else ""), "redact_noop": sanitized == response_text},
                **common,
            )
        return None

    if verdict.action == "flag":
        if incident_logging:
            _emit_telemetry(
                status_code=200,
                event_type="output_guard",
                action="flag",
                risk_score=getattr(verdict, "confidence", 0.6),
                latency_ms=(time.perf_counter() - start) * 1000,
                metadata=_meta(raw_output),
                **common,
            )
        return None

    return None


import hashlib as _hashlib
import uuid as _uuid

try:
    from .patterns import get_compliance_tags as _get_compliance_tags
except ImportError:
    try:
        from patterns import get_compliance_tags as _get_compliance_tags
    except ImportError:
        def _get_compliance_tags(pattern_keys: list[str]) -> list[str]:
            return (CONFIG or {}).get("compliance_frameworks", [])


def _build_zeroshield_metadata(
    action: str,
    reason: str,
    detection_tier: str = "none",
    threat_type: str = "none",
    confidence: float = 0.0,
    matched_patterns: list[str] | None = None,
    compliance_tags: list[str] | None = None,
    original_prompt: str = "",
    redacted_prompt: str | None = None,
    redacted_response: str | None = None,
    rewritten_response: str | None = None,
    detail: str | None = None,
    processing_time_ms: float = 0.0,
    intent: str = "",
    review_required: bool = False,
    security_incident: bool = False,
    factuality_warning: bool = False,
    routing: dict | None = None,
) -> dict:
    """Build the unified zeroshield metadata dict for any response."""
    resolved_patterns = matched_patterns or []
    resolved_tags = compliance_tags if compliance_tags is not None else _get_compliance_tags(resolved_patterns)

    result = {
        # Phase-4 D-b root fix: reuse the canonical per-request id (_REQUEST_ID,
        # set in proxy_chat / proxy_embeddings) so the body request_id == the
        # x-request-id header (shim) == the [SECURITY_BLOCK] log id. Mint a fresh
        # id only when the ContextVar is unset (non-request / background context).
        "request_id": _REQUEST_ID.get("") or f"zs-{_uuid.uuid4().hex[:12]}",
        "action": action,
        "reason": reason,
        "detail": detail or reason,
        "detection_tier": detection_tier or "none",
        "threat_type": threat_type or "none",
        "confidence": round(confidence, 4),
        "matched_patterns": resolved_patterns,
        "compliance_tags": resolved_tags,
        "original_prompt_hash": f"sha256:{_hashlib.sha256(original_prompt.encode('utf-8')).hexdigest()[:16]}" if original_prompt else "",
        "redacted_prompt": redacted_prompt,
        "redacted_response": redacted_response,
        "rewritten_response": rewritten_response,
        "review_required": review_required,
        "security_incident": security_incident,
        "factuality_warning": factuality_warning,
        "processing_time_ms": round(processing_time_ms, 2),
    }
    if intent:
        result["intent"] = intent
    if routing:
        result["routing"] = routing
    return result


def _estimate_request_tokens(prompt: str, max_tokens: int = 0) -> int:
    """Rough token estimate for TPM pre-check (matches simulator: ~4 chars per token)."""
    prompt_tokens = max(1, len(prompt or "") // 4)
    completion_budget = max(0, int(max_tokens or 0))
    return max(1, prompt_tokens + completion_budget)


def _blocked_keyword_matches(prompt_lower: str, keyword: str) -> bool:
    """Match firewall blocked keywords on word boundaries (avoids substring false positives)."""
    kw = (keyword or "").strip().lower()
    if not kw or not prompt_lower:
        return False
    if re.search(r"\s", kw):
        return kw in prompt_lower
    return bool(re.search(rf"\b{re.escape(kw)}\b", prompt_lower))


def _coerce_string_list(*values) -> list[str]:
    items: list[str] = []

    def append_value(value) -> None:
        if value is None:
            return
        if isinstance(value, str):
            for raw_part in value.split(","):
                text = raw_part.strip()
                if text and text not in items:
                    items.append(text)
            return
        text = str(value).strip()
        if text and text not in items:
            items.append(text)

    for value in values:
        if isinstance(value, list):
            for item in value:
                append_value(item)
        else:
            append_value(value)
    return items


_SAFE_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9._:/+-]+")


_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")

# C6b: a ~1KB deeply-nested JSON body (depth >= CPython's ~1000 recursion limit)
# crashed _strip_lone_surrogates with RecursionError -> HTTP 500 (a cheap DoS),
# because it ran on the raw body BEFORE any size/DoS gate. Cap the recursion depth
# far above any legitimate request body (messages + tool JSON-schemas nest only a
# handful of levels) and well below the interpreter limit; past the cap, stop
# recursing and return the subtree unchanged so the existing downstream shape
# validation rejects the pathological body with a clean 400 instead of a 500.
_MAX_BODY_SANITIZE_DEPTH = 200


def _strip_lone_surrogates(value, _depth: int = 0):
    """Replace lone UTF-8 surrogate code points (e.g. ``\\ud800`` arriving via a
    JSON escape) with U+FFFD. A lone surrogate is not encodable to UTF-8, so left
    in place it raises ``UnicodeEncodeError`` deep in litellm/openai serialization
    (a 502 + multi-second hang) and poisons the telemetry JSON column (Postgres
    rejects it, dropping the security EnforcementEvent). Sanitizing at the gateway
    boundary keeps every downstream consumer on valid Unicode."""
    if _depth > _MAX_BODY_SANITIZE_DEPTH:
        # Over-nested body (DoS): stop recursing. The shape/size gates downstream
        # reject it (400), so this subtree is never serialized to an upstream anyway.
        return value
    if isinstance(value, str):
        return _LONE_SURROGATE_RE.sub("�", value) if _LONE_SURROGATE_RE.search(value) else value
    if isinstance(value, list):
        return [_strip_lone_surrogates(v, _depth + 1) for v in value]
    if isinstance(value, dict):
        return {k: _strip_lone_surrogates(v, _depth + 1) for k, v in value.items()}
    return value


def _normalize_chatcmpl_id(_id):
    """OAS-LEAK-I3-01: litellm passes the RAW upstream generation id through the
    top-level ``id`` (e.g. an OpenRouter ``gen-…``), leaking the provider. Rewrite a
    non-``chatcmpl-`` prefix to ``chatcmpl-`` (keeping the opaque suffix) for OpenAI
    parity + topology hygiene."""
    if isinstance(_id, str) and _id and not _id.startswith("chatcmpl-"):
        return re.sub(r"^[A-Za-z]+-", "chatcmpl-", _id, count=1) if re.match(r"^[A-Za-z]+-", _id) else f"chatcmpl-{_id}"
    return _id


def _scrub_upstream_passthrough(body: dict, request_id: str = "") -> None:
    """OAS-LEAK (class): the gateway returns the upstream LLM body verbatim apart from
    the zeroshield-envelope scrub. Comprehensively strip upstream-internal passthrough
    that leaks the provider family / topology and is NOT part of the OpenAI schema:
      - top-level ``id`` (raw upstream generation id) -> normalized ``chatcmpl-…``;
      - top-level ``citations`` (OpenRouter/Perplexity-family);
      - ``provider_specific_fields`` at the CHOICE and message/delta level (holds
        ``reasoning_details[].format`` = provider family, and native finish reasons);
      - choice-level ``native_finish_reason``.
    Denylist (not allowlist) so a legit new OpenAI field is never dropped. Mutates in place."""
    if not isinstance(body, dict):
        return
    body["id"] = _normalize_chatcmpl_id(body.get("id"))
    body.pop("citations", None)
    body.pop("provider_specific_fields", None)  # OAS-LEAK-STREAM-ROOT-PSF (top-level parity)
    for _ch in (body.get("choices") or []):
        if not isinstance(_ch, dict):
            continue
        _ch.pop("provider_specific_fields", None)
        _ch.pop("native_finish_reason", None)
        for _slot_key in ("message", "delta"):
            _slot = _ch.get(_slot_key)
            if isinstance(_slot, dict):
                _slot.pop("provider_specific_fields", None)


def _collect_nested_strings(obj, _depth: int = 0) -> list:
    """Collect every non-empty string anywhere in a nested dict/list tree (depth
    capped to bound work). Used to fold ALL agent_data values into the scanner so
    a string buried in a nested dict/list can't bypass Tier-1/Tier-2."""
    out: list = []
    if _depth > 6:
        return out
    if isinstance(obj, str):
        if obj:
            out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            # RAG-C5-META-1: also fold dict KEY NAMES into the scan. A secret/injection
            # placed as a metadata KEY (e.g. {"ghp_…":"x"} or {"ignore all previous
            # instructions":"v"}) was stored UNSCANNED and round-tripped on query,
            # because only .values() were collected. The exact token blocks as a value
            # but sailed through as a key.
            if isinstance(k, str) and k:
                out.append(k)
            out.extend(_collect_nested_strings(v, _depth + 1))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_collect_nested_strings(v, _depth + 1))
    return out


def _safe_model_echo(model: str | None) -> str:
    """Sanitize a CLIENT-supplied model string before it is reflected back into
    any response (error body, routing metadata, telemetry-to-client) (R4).

    A raw ``model`` field is attacker-controlled. Reflecting it verbatim lets a
    payload like ``<script>`` or a CSV/formula-injection string flow into the
    control plane and surface as a graph node / chart slice. Echo only a
    conservative, charset-restricted, length-capped token; a model name that is
    not a clean identifier collapses to a generic placeholder so nothing
    reflectable ever leaves the gateway.
    """
    if not model:
        return ""
    text = str(model).strip()
    if not text:
        return ""
    # Only echo the value when the WHOLE string is a clean model identifier.
    # Anything else (HTML, whitespace-embedded payloads, control chars) is
    # replaced rather than reflected.
    if len(text) <= 128 and _SAFE_MODEL_NAME_RE.fullmatch(text):
        return text
    return "(invalid model name)"


# FIX-2: hard ceilings for /v1/embeddings input. Without these a single request
# could submit an unbounded number of items / characters and trigger a costly
# fan-out + retry-storm against the embedding provider (resource-exhaustion DoS).
MAX_EMBED_INPUT_CHARS = 200_000  # total chars across all batch items
MAX_EMBED_BATCH = 256            # max items in a batch `input` array

# G63: /v1/moderations scans EVERY list item with the tier-1 scanner (~ms each), so an
# unbounded `input` array is a CPU resource-exhaustion DoS (a single authenticated request
# could burn minutes of scan CPU) — even though each item is individually bounded by
# MAX_PROMPT_LENGTH. Cap the batch count + total chars, mirroring the embeddings ceilings.
MAX_MODERATION_BATCH = 256
MAX_MODERATION_INPUT_CHARS = 200_000

# G64: /v1/completions dispatches ONE full chat call (incl. an upstream LLM inference) PER
# item of a list `prompt`, serially. An unbounded array is therefore a cost/DoS amplification
# FAR worse than a scan-only batch — a single authenticated request could fan out into
# hundreds of paid provider calls, bypassing per-request rate limits. Cap the prompt count
# (each item is already length-bounded by the chat MAX_PROMPT_LENGTH) + total chars.
MAX_COMPLETION_PROMPTS = 64
MAX_COMPLETION_INPUT_CHARS = 200_000

# input-val#6: hard ceiling on the number of chat messages per request. The
# per-message text length is already capped, but an unbounded ``messages`` array
# is itself a resource-exhaustion / scanner-amplification vector.
MAX_MESSAGES = 200               # max entries in a /v1/chat/completions messages array
# G65: cap the ``tools`` array. Every tool's free-text name/description is FOLDED into the
# scanned prompt (_extract_tool_definitions_text) and, on a redact verdict, RECURSIVELY
# masked (_redact_tool_descriptions) — measured ~5s of CPU for 100k tools (~1s for 20k). An
# attacker trivially triggers the redact path (one PII value in the prompt) + a huge tools
# array -> seconds of CPU per request, an amplification the MAX_PROMPT_LENGTH scan cap does
# NOT bound (it caps the scanned string, not the per-tool redaction recursion). OpenAI's own
# practical limit is ~128 tools, so 256 is generous headroom.
MAX_TOOLS = 256                  # max entries in a `tools` / `functions` array
MAX_OUTPUT_TOKENS_CEILING = 1_000_000  # C5: absolute upper bound on max_tokens

# C2: allowlist of recognized multimodal content-part ``type`` values. An
# unknown type (e.g. {"type":"bogus"}) is rejected at the boundary with a fast
# 400 instead of being forwarded to LiteLLM/upstream — which would retry and
# walk the model-fallback chain before erroring (slow 502 / DoS amplification).
# Covers OpenAI ("text","image_url","input_audio") + common provider variants.
_ALLOWED_CONTENT_PART_TYPES = frozenset(
    {"text", "image_url", "input_audio", "audio", "video_url", "file", "image"}
)


_GENERIC_LLM_ERROR_BY_STATUS = {
    429: "The upstream inference provider is rate-limiting this request. Retry shortly.",
    502: "The upstream inference provider returned an error. Please try again.",
    503: "The inference provider is temporarily unavailable. Please try again.",
    504: "The upstream inference provider timed out. Please try again.",
}


def _sanitize_llm_error_response(code: int, resp) -> dict:
    """Strip raw upstream/LiteLLM exception text from an error response before it
    reaches the client (R5).

    A raw LiteLLM exception leaks the configured fallback topology AND the
    OpenRouter key's embedded ``user_id`` (it appears in the provider error
    string). Log the original server-side, but return ONLY a generic,
    status-derived message. ``code`` is the HTTP status of the failed call.
    """
    # Log the raw detail server-side for operators (never sent to the client).
    try:
        _raw = resp.get("error") if isinstance(resp, dict) else resp
        LOG.warning("Upstream LLM error [%s]: %s", code, _raw)
    except Exception:  # noqa: BLE001
        pass
    generic = _GENERIC_LLM_ERROR_BY_STATUS.get(
        int(code) if code else 502,
        "The upstream inference provider returned an error. Please try again.",
    )
    # Preserve the OpenAI-compatible error envelope shape so SDK clients still
    # parse it, but replace the leaky message/type/code body.
    sanitized = {"error": {"message": generic, "type": "upstream_error"}}
    if code:
        sanitized["error"]["code"] = int(code)
    return sanitized


def _extract_chat_routing_preferences(body: dict, org_config: dict, auth_ctx, scan_verdict) -> dict:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    routing_preferences = body.get("routing_preferences") if isinstance(body.get("routing_preferences"), dict) else {}
    org_routing_enabled = bool(org_config.get("routing_enabled", True))
    routing_override = routing_preferences.get("enable_routing")
    if routing_override is None:
        routing_override = routing_preferences.get("routing_enabled")
    if routing_override is None:
        routing_override = metadata.get("enable_routing")
    if routing_override is None:
        routing_override = body.get("enable_routing")
    routing_override = routing_override if isinstance(routing_override, bool) else None
    routing_enabled = routing_override if routing_override is not None else org_routing_enabled
    preferred_model = str(body.get("model") or "auto")
    required_compliance = _coerce_string_list(
        routing_preferences.get("compliance_requirements"),
        body.get("compliance_requirements"),
        metadata.get("compliance_requirements"),
    )
    # FIX-5: ``data_sensitivity`` is client-controlled and flows into
    # ``_SENSITIVITY_ORDER.get(...)`` downstream — a non-str (e.g. a dict) would
    # raise there. Coerce to a plain string; ignore non-str → "public".
    #
    # F12 NOTE: the finding (org FirewallConfig.default_data_sensitivity is synced
    # but never read) is real, but enforcing it as a hard floor here is NOT safe in
    # the current data model — the field DEFAULT is 'internal' for ALL orgs while
    # connected models are 'public', so flooring would 403 every org's default
    # traffic (compliance_routing_unsatisfiable). Wiring the floor requires an
    # operator decision: change the field default to 'public' + migrate existing
    # rows, OR raise model sensitivity levels. Left as a flagged design item; the
    # client-supplied value (fail-closed on unknown via the filters) is used as-is.
    _ds = (
        routing_preferences.get("data_sensitivity")
        or metadata.get("data_sensitivity")
        or body.get("data_sensitivity")
        or "public"
    )
    # Normalize to lowercase so the fail-closed compliance gate and the hard
    # filter (_SENSITIVITY_ORDER.get) agree (FC-01 case-variant fix).
    data_sensitivity = (_ds.strip().lower() if isinstance(_ds, str) else "public") or "public"
    # FIX-5: client-controlled numeric fields must never 500 the request via a
    # raw int()/float() on a bad type; degrade to the documented default instead.
    try:
        latency_budget_ms = int(
            routing_preferences.get("latency_budget_ms")
            or body.get("latency_budget_ms")
            or metadata.get("latency_budget_ms")
            or 30000
        )
    except (ValueError, TypeError):
        latency_budget_ms = 30000
    # C3 (defense-in-depth): include OverflowError so a non-finite float
    # (json ``Infinity`` / ``1e999``) that slipped past the boundary clamp can
    # never crash this secondary coercion either.
    try:
        max_tokens = int(body.get("max_tokens") or 0)
    except (ValueError, TypeError, OverflowError):
        max_tokens = 0
    try:
        _req_n = int(body.get("n") or 1)
    except (ValueError, TypeError, OverflowError):
        _req_n = 1
    # #4: fold the requested output budget (max_tokens × n) into the pre-inference
    # TPM reservation so a high-n/high-max_tokens request can't under-charge it.
    estimated_tokens = LLM_ROUTER.estimate_prompt_tokens(body.get("messages") or [], max_tokens=max_tokens, n=_req_n)
    # Per-request weight of exactly 0 is a legitimate caller value (e.g. "ignore
    # risk entirely"); `or`-coalescing would silently fall back to the default
    # and violate the Σ=1.0 intent. Honor an explicit 0 by checking for None.
    # FIX-5: a non-numeric per-request weight must degrade to the default, never 500.
    def _weight(pref_key: str, org_key: str, default: float) -> float:
        _w = routing_preferences.get(pref_key)
        if _w is None:
            _w = org_config.get(org_key, CONFIG.get(org_key, default))
        try:
            _wf = float(_w)
        except (ValueError, TypeError):
            return float(default)
        # R2-RT-3: a caller-supplied weight is unvalidated input. Clamp to [0,1] so a
        # NEGATIVE weight can't invert _normalize_weights (negative Σ flips every
        # component → forces the worst/dead model) and a huge weight (1e308) can't
        # dominate. 0 stays legitimate ("ignore this dimension").
        if _wf != _wf:  # NaN
            return float(default)
        return max(0.0, min(_wf, 1.0))

    weights = {
        "risk": _weight("risk_weight", "routing_risk_weight", 0.30),
        "cost": _weight("cost_weight", "routing_cost_weight", 0.20),
        "latency": _weight("latency_weight", "routing_latency_weight", 0.20),
        "priority": _weight("priority_weight", "routing_priority_weight", 0.30),
    }
    # FIX-1.5c: ``request_risk_score`` is RISK, not classification CONFIDENCE.
    # The old `or`-chain folded ``scan_verdict.confidence`` straight in, so a
    # benign prompt the scanner was *highly confident* was safe (confidence≈1.0,
    # action=="allow") produced request_risk_score≈1.0 and forced safest/most
    # expensive routing. It also `or`-coalesced a genuine 0.0 away.
    #
    # Resolution order (explicit None-checks so a real 0.0 is honored):
    #   1. an explicit per-request / metadata model_risk_score override;
    #   2. a derived risk from the scan verdict — confidence ONLY when the
    #      verdict is malicious (block / flag, or a non-benign threat_type);
    #      benign/allow verdicts contribute near-zero risk, NOT their confidence;
    #   3. the auth context risk_score;
    #   4. 0.0.
    def _risk_from_verdict(_v) -> float | None:
        if _v is None:
            return None
        _action = str(getattr(_v, "action", "") or "").lower()
        _threat = str(getattr(_v, "threat_type", "") or "").lower()
        _conf = getattr(_v, "confidence", None)
        try:
            _conf = float(_conf) if _conf is not None else None
        except (ValueError, TypeError):
            _conf = None
        _malicious = _action in ("block", "flag", "redact", "rewrite") or (
            _threat not in ("", "none", "benign", "safe", "allow")
        )
        if _malicious:
            # Confidence-weighted high risk; clamp into [0,1]. Unknown confidence
            # on a malicious verdict defaults to a conservative high risk.
            return min(max(_conf if _conf is not None else 0.85, 0.0), 1.0)
        # Benign/allow: low risk regardless of how confident the scanner was.
        return 0.0

    def _first_present(*candidates) -> float | None:
        for _c in candidates:
            if _c is not None:
                return _c
        return None

    _risk_candidate = _first_present(
        routing_preferences.get("model_risk_score"),
        metadata.get("model_risk_score"),
        _risk_from_verdict(scan_verdict),
        getattr(auth_ctx, "risk_score", None),
    )
    try:
        request_risk_score = float(_risk_candidate) if _risk_candidate is not None else 0.0
    except (ValueError, TypeError):
        request_risk_score = 0.0
    token_budget_tpm = getattr(auth_ctx, "rate_limit_tpm", None) if auth_ctx is not None else None
    return {
        "routing_enabled": routing_enabled,
        "routing_override": routing_override,
        "org_routing_enabled": org_routing_enabled,
        "preferred_model": preferred_model,
        "required_compliance": required_compliance,
        "data_sensitivity": data_sensitivity,
        "latency_budget_ms": latency_budget_ms,
        "estimated_tokens": estimated_tokens,
        "weights": weights,
        "request_risk_score": request_risk_score,
        "token_budget_tpm": token_budget_tpm,
    }


def _build_routing_metadata(
    selection,
    routing_enabled: bool,
    routing_override: bool | None,
    org_routing_enabled: bool,
    latency_budget_ms: int,
    data_sensitivity: str,
    required_compliance: list[str],
    weights: dict[str, float],
    request_risk_score: float,
    estimated_tokens: int,
    token_budget_tpm: int | None,
) -> dict:
    # ``requested_model`` is the RAW client-supplied model; echo only a sanitized
    # form so a malicious model name can't reflect into routing graphs/charts (R4).
    original_model = _safe_model_echo(selection.requested_model) or "auto"
    rerouted = bool(
        selection.model_name
        and original_model not in ("", "auto")
        and selection.model_name != original_model
    )
    return {
        "original_model": original_model,
        "selected_model": selection.model_name,
        "routed_model": selection.model_name,
        "routing_enabled": routing_enabled,
        "routing_override": routing_override,
        "org_routing_enabled": org_routing_enabled,
        "routed_model_id": selection.model_id,
        "routing_score": selection.score,
        "rerouted": rerouted,
        "reroute_reason": selection.reason,
        "routing_reason": selection.reason,
        "fallback_chain": selection.fallback_chain,
        "decision_source": selection.decision_source,
        "policy_summary": selection.policy_summary,
        "decision_factors": selection.decision_factors,
        "candidate_count": selection.candidate_count,
        "data_sensitivity": data_sensitivity,
        "compliance_requirements": required_compliance,
        "request_risk_score": request_risk_score,
        "estimated_tokens": estimated_tokens,
        "token_budget_tpm": token_budget_tpm,
        "latency_budget_ms": latency_budget_ms,
        "weights": weights,
    }


def _build_isolation_reroute_metadata(audit: dict, routing_prefs: dict) -> dict:
    """Route metadata envelope when kill-switch / model-state reroute skips adjudicator."""
    original = str(audit.get("original_model") or "")
    selected = str(audit.get("selected_model") or "")
    trigger = str(audit.get("trigger_source") or "kill_switch")
    reason = str(audit.get("reason") or "").strip()
    decision_source = "kill_switch" if trigger == "kill_switch" else "model_state"
    if trigger == "kill_switch":
        routing_reason = f"Kill-switch reroute: {original} → {selected}."
        if reason:
            routing_reason = f"{routing_reason} {reason}"
    else:
        routing_reason = f"Model-state reroute: {original} → {selected}."
        if reason:
            routing_reason = f"{routing_reason} {reason}"
    return {
        "original_model": original,
        "selected_model": selected,
        "routed_model": selected,
        "rerouted": bool(original and selected and original != selected),
        "reroute_reason": reason,
        "routing_reason": routing_reason.strip(),
        "decision_source": decision_source,
        "trigger_source": trigger,
        "isolation_action": audit.get("action") or "reroute",
        "routing_enabled": bool(routing_prefs.get("routing_enabled")),
        "routing_override": routing_prefs.get("routing_override"),
        "org_routing_enabled": bool(routing_prefs.get("org_routing_enabled")),
        "policy_summary": f"Isolation reroute via {decision_source.replace('_', ' ')}",
        "compliance_requirements": routing_prefs.get("required_compliance"),
        "data_sensitivity": routing_prefs.get("data_sensitivity"),
        "request_risk_score": routing_prefs.get("request_risk_score"),
        "estimated_tokens": routing_prefs.get("estimated_tokens"),
        "token_budget_tpm": routing_prefs.get("token_budget_tpm"),
        "latency_budget_ms": routing_prefs.get("latency_budget_ms"),
        "weights": routing_prefs.get("weights"),
        "fallback_reason_code": audit.get("fallback_reason_code"),
        "isolation_scope": audit.get("isolation_scope"),
    }


def _isolation_reroute_context(
    org_slug: str,
    routing_models: list,
    routing_prefs: dict,
    allowed_models: list | None,
    body: dict | None = None,
) -> dict:
    """Shared context for compliant kill-switch / model-state reroutes."""
    fallback_chains = CONFIG_SYNC.get_fallback_chains(org_slug) if CONFIG_SYNC else {}
    allowed_set = None
    if allowed_models:
        allowed_set = {str(m).lower() for m in allowed_models if m}
    body = body if isinstance(body, dict) else {}
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    data_sensitivity = (
        routing_prefs.get("data_sensitivity")
        or body.get("data_sensitivity")
        or metadata.get("data_sensitivity")
        or "public"
    )
    required_compliance = _coerce_string_list(
        routing_prefs.get("required_compliance"),
        body.get("compliance_requirements"),
        metadata.get("compliance_requirements"),
    )
    return {
        "routing_models": routing_models,
        "fallback_chains": fallback_chains,
        "data_sensitivity": data_sensitivity,
        "required_compliance": required_compliance,
        "allowed_set": allowed_set,
    }


def _apply_compliant_isolation_reroute(
    *,
    primary_model: str,
    requested_fallback: str,
    ctx: dict,
    scope: str,
    trigger_source: str,
    reason: str,
) -> tuple[str | None, dict]:
    """
    Resolve reroute target under the same hard eligibility filters as Stage-2.

    Returns (selected_model_or_none, audit_metadata). None => disable-first block.
    """
    try:
        from routing_isolation import (
            build_isolation_audit_metadata,
            resolve_compliant_fallback,
        )
    except ImportError:
        from .routing_isolation import (
            build_isolation_audit_metadata,
            resolve_compliant_fallback,
        )

    compliant, reason_code = resolve_compliant_fallback(
        primary_model=primary_model,
        requested_fallback=requested_fallback,
        routing_models=ctx["routing_models"],
        fallback_chains=ctx["fallback_chains"],
        data_sensitivity=ctx["data_sensitivity"],
        required_compliance=ctx["required_compliance"],
        allowed_models=ctx["allowed_set"],
    )
    chains = ctx.get("fallback_chains") or {}
    audit_meta = build_isolation_audit_metadata(
        event_kind="isolation_reroute",
        scope=scope,
        trigger_source=trigger_source,
        action="reroute" if compliant else "block",
        reason=reason or reason_code,
        original_model=primary_model,
        selected_model=compliant or "",
        data_sensitivity=ctx["data_sensitivity"],
        compliance_tags=ctx["required_compliance"],
        fallback_chain_version=chains.get("version"),
        fallback_reason_code=reason_code,
    )
    return compliant, audit_meta


try:
    from .stream_orchestration import (
        StreamFinalizeHooks,
        StreamLaunchContext,
        StreamRunMetrics,
        StreamScanMode,
        build_base_stream_headers,
        enrich_stream_headers,
        resolve_scan_mode,
        stream_with_finalize,
        streaming_preflight_block_body,
        wrap_secure_stream_if_needed,
    )
    from .metrics import record_stream_complete as _prom_record_stream_complete
    from .metrics import record_stream_guard_action as _prom_record_stream_guard
except ImportError:
    from stream_orchestration import (
        StreamFinalizeHooks,
        StreamLaunchContext,
        StreamRunMetrics,
        StreamScanMode,
        build_base_stream_headers,
        enrich_stream_headers,
        resolve_scan_mode,
        stream_with_finalize,
        streaming_preflight_block_body,
        wrap_secure_stream_if_needed,
    )
    from metrics import record_stream_complete as _prom_record_stream_complete
    from metrics import record_stream_guard_action as _prom_record_stream_guard


def _stream_preflight_block_if_needed(org_config: dict) -> JSONResponse | None:
    """Fail-closed JSON response before SSE when streaming preflight cannot complete."""
    merged = {**CONFIG, **(org_config or {})}
    block_body = streaming_preflight_block_body(
        merged,
        policy_sync_loaded=POLICY_SYNC is not None and POLICY_SYNC.is_loaded,
    )
    if block_body is None:
        return None
    METRICS["blocked"] += 1
    return JSONResponse(status_code=503, content=block_body)


def _stream_finalize_hooks() -> StreamFinalizeHooks:
    def _record_stream(org_slug: str, model: str, decision: str, metrics: StreamRunMetrics, elapsed_ms: float, **_kw):
        _prom_record_stream_complete(
            org_slug,
            model,
            decision,
            ttft_seconds=metrics.ttft_ms / 1000.0 if metrics.ttft_ms else 0.0,
            duration_seconds=elapsed_ms / 1000.0,
            had_error=metrics.had_error,
            fallback_before_token=metrics.fallback_before_first_token,
        )

    return StreamFinalizeHooks(
        circuit_breaker=CIRCUIT_BREAKER,
        rate_limiter=RATE_LIMITER,
        record_stream_complete=_record_stream,
        emit_telemetry=_emit_telemetry,
    )


def _wrap_secure_stream_with_context(
    inner,
    *,
    org_config: dict,
    org_slug: str,
    request,
    ctx,
    stream_metrics,
    enforcement_mode: str,
):
    """Build the OUTPUT_GUARD secure stream WITH request context (M-05).

    Mirrors ``wrap_secure_stream_if_needed`` for the OUTPUT_GUARD mode but also
    threads ``org_config`` (tri-state tier-2 gating), ``org_slug`` (breaker org
    attribution + grounding telemetry) and a lazy RAG ``context_resolver`` into
    ``SecureStreamingResponse`` so streaming output is no longer scanned
    context-blind. Defensive: any import/construction failure falls back to the
    shared wrapper so the stream still gets (context-blind) output scanning
    rather than none.
    """
    try:
        from secure_streaming import SecureStreamingResponse
    except ImportError:  # pragma: no cover - packaging fallback
        from .secure_streaming import SecureStreamingResponse

    merged_cfg = {**CONFIG, **(org_config or {})}

    async def _context_resolver():
        return await _resolve_rag_context_chunks(request)

    try:
        secure = SecureStreamingResponse(
            inner_generator=inner,
            scanner=INPUT_SCANNER,
            redaction_enabled=True,
            buffer_max_bytes=int(merged_cfg.get("stream_max_buffer_bytes", merged_cfg.get("scan_buffer_max_bytes", 4096))),
            max_buffer_chunks=int(merged_cfg.get("stream_max_buffer_chunks", 64)),
            output_guard=OUTPUT_GUARD,
            telemetry=TELEMETRY,
            user_id=ctx.user_id,
            organization_id=ctx.organization_id,
            model=ctx.model or ctx.body.get("model", ""),
            project_id=ctx.project_id,
            source_ip=ctx.source_ip,
            request_id=ctx.request_id,
            record_guard_metric=_prom_record_stream_guard,
            org_slug=org_slug,
            stream_metrics=stream_metrics,
            enforcement_mode=enforcement_mode,
            org_config=(CONFIG_SYNC.get_config(org_slug) if (CONFIG_SYNC is not None and org_slug) else None),
            context_resolver=_context_resolver,
            request=request,  # streaming #4: detect client disconnect, stop generating/billing
        )
        return secure.__aiter__()
    except Exception:
        LOG.exception("Context-aware secure stream wrap failed; falling back to shared wrapper")
        return wrap_secure_stream_if_needed(
            inner,
            scan_mode=StreamScanMode.OUTPUT_GUARD,
            config=merged_cfg,
            input_scanner=INPUT_SCANNER,
            output_guard=OUTPUT_GUARD,
            telemetry=TELEMETRY,
            ctx=ctx,
            record_guard_metric=_prom_record_stream_guard,
            stream_metrics=stream_metrics,
            enforcement_mode=enforcement_mode,
            request=request,  # streaming #4
        )


def _build_stream_zeroshield_base(
    *,
    request_id: str,
    scan_verdict=None,
    redacted_prompt: str | None = None,
    route_selection=None,
) -> dict:
    """M-51: client-safe zeroshield metadata for the terminal SSE trace frame.

    Mirrors the non-streaming response pipeline: _build_zeroshield_metadata ->
    enrich_zeroshield_from_verdict -> _redact_for_client_response, so streaming
    clients get the SAME metadata shape they would receive on a JSON response.
    The streaming choke point (stream_orchestration.stream_with_finalize)
    overlays the mid-stream output-guard outcome before emitting the frame.
    """
    try:
        if redacted_prompt is not None:
            _patterns = list(getattr(scan_verdict, "matched_patterns", None) or []) if scan_verdict else []
            zs = _build_zeroshield_metadata(
                action="redact",
                reason=(
                    f"PII detected in prompt ({', '.join(_patterns)}). Redacted before forwarding to LLM."
                    if _patterns
                    else "Sensitive data redacted before forwarding to the LLM."
                ),
                detection_tier=str(getattr(scan_verdict, "tier", "") or "tier_1") if scan_verdict else "tier_1",
                threat_type=str(getattr(scan_verdict, "threat_type", "") or "pii") if scan_verdict else "pii",
                confidence=float(getattr(scan_verdict, "confidence", 0.85) or 0.85) if scan_verdict else 0.85,
                matched_patterns=_patterns,
                detail=str(getattr(scan_verdict, "detail", "") or "") if scan_verdict else "",
            )
        else:
            zs_action, zs_reason, zs_threat, zs_conf, zs_patterns, zs_detail = (
                _resolve_success_metadata_from_verdict(scan_verdict)
            )
            zs = _build_zeroshield_metadata(
                action=zs_action,
                reason=zs_reason,
                detection_tier=str(getattr(scan_verdict, "tier", "") or "none") if scan_verdict else "none",
                threat_type=zs_threat,
                confidence=zs_conf,
                matched_patterns=zs_patterns,
                detail=zs_detail,
            )
            try:
                from pipeline_trace import enrich_zeroshield_from_verdict

                zs = enrich_zeroshield_from_verdict(zs, scan_verdict=scan_verdict, final_action=zs_action)
            except Exception:
                pass
        if route_selection is not None:
            requested = getattr(route_selection, "requested_model", None) or "auto"
            routed = getattr(route_selection, "model_name", "") or ""
            _rerouted = bool(requested and requested != "auto" and routed and routed != requested)
            _routing_reason = getattr(route_selection, "reason", "") or ""
            _decision_source = getattr(route_selection, "decision_source", "") or ""
            zs["selected_model"] = routed
            zs["original_model"] = requested
            zs["rerouted"] = _rerouted
            zs["routing_reason"] = _routing_reason
            zs["decision_source"] = _decision_source
            # STREAMING PIPELINE FIX: also surface the NESTED ``routing`` object so a
            # streamed terminal frame has the SAME shape as the non-stream response
            # (zeroshield.routing.*). Without this, clients that read
            # ``zeroshield.routing.selected_model`` (the demo's routing visualizer,
            # any customer dashboard) get null routing on streamed requests even
            # though the decision happened. Org-facing names ONLY — routed_model_id
            # (the raw upstream id) is never set here and is scrubbed by
            # _redact_for_client_response regardless.
            zs["routing"] = {
                "requested_model": requested,
                "original_model": requested,
                "selected_model": routed,
                "routed_model": routed,
                "rerouted": _rerouted,
                "routing_reason": _routing_reason,
                "decision_source": _decision_source,
            }
        client_zs = _redact_for_client_response(zs) or {}
        client_zs["request_id"] = request_id
        return client_zs
    except Exception:
        LOG.exception("Failed to build streaming zeroshield base; using minimal trace")
        return {"request_id": request_id, "action": "allow"}


def _launch_chat_stream_response(
    *,
    request: Request,
    body: dict,
    org_config: dict,
    org_slug: str,
    auth_ctx,
    redacted_prompt: str | None,
    route_selection=None,
    scan_verdict=None,
    user_id=None,
    project_id: str = "",
    key_hash: str = "",
    rate_limit_tpm: int = 0,
    estimated_tokens: int = 20,
    org_tpm_limit: int = 0,
    secure_output_scan: bool = True,
):
    """
    stream_phase + finalization_phase for /v1/chat/completions (SSE).

    Assumes eligibility_phase and selection_phase already ran in proxy_chat.
    """
    scan_mode = resolve_scan_mode(
        output_scan_enabled=bool(org_config.get("output_scan_enabled", CONFIG.get("output_scan_enabled", True))),
        input_scanner=INPUT_SCANNER if secure_output_scan else None,
        output_guard=OUTPUT_GUARD if secure_output_scan else None,
    )
    # Use the canonical per-request id set in proxy_chat (_REQUEST_ID ContextVar,
    # line ~3780) so EVERY event of a streamed request (model_routed, input_blocked,
    # output_guard, …) shares ONE request_id. Previously this minted a separate
    # `zs-stream-<uuid>` id, so a streamed request's output_guard event carried a
    # DIFFERENT id than its model_routed event — making distinct-request counting
    # (module 1.1 "Requests inspected") see ONE request as TWO. Fall back to the
    # header, then a fresh id, only when the ContextVar is somehow unset.
    request_id = (
        _REQUEST_ID.get("")
        or request.headers.get("X-Request-ID")
        or f"zs-stream-{_uuid.uuid4().hex[:12]}"
    )
    model = body.get("model", "")
    enforcement_mode = str(org_config.get("enforcement_mode", CONFIG.get("enforcement_mode", "block")))
    ctx = StreamLaunchContext(
        body=body,
        redacted_prompt=redacted_prompt,
        org_slug=org_slug or "default",
        model=model,
        key_hash=key_hash,
        rate_limit_tpm=rate_limit_tpm or 0,
        estimated_tokens=estimated_tokens,
        org_tpm_limit=org_tpm_limit or 0,
        route_selection=route_selection,
        scan_verdict=scan_verdict,
        scan_mode=scan_mode,
        request_id=request_id,
        user_id=user_id,
        project_id=str(project_id or ""),
        organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
        source_ip=request.client.host if request.client else "",
    )
    headers = enrich_stream_headers(
        build_base_stream_headers(),
        route_selection=route_selection,
        scan_verdict=scan_verdict,
        redacted_prompt=redacted_prompt,
        scan_mode=scan_mode,
        emit_debug=bool(org_config.get("stream_emit_debug_headers", CONFIG.get("stream_emit_debug_headers", False))),
    )
    stream_metrics = StreamRunMetrics()

    # MODEL-ID LEAK FIX: echo the ORIGINAL requested model in stream chunks (parity
    # with the non-stream path), never the raw upstream id. route_selection carries
    # the original requested name; fall back to the resolved body model.
    _stream_echo_model = (getattr(route_selection, "requested_model", "") or model or "")

    # E13: re-check the ACTIVE model's live kill-switch / model-state DURING the
    # stream (throttled inside the router chunk loop) so an operator who trips the
    # kill-switch mid-stream stops the remainder, not just future requests. The
    # active model is body["model"] (post-reroute) and key_prefix is the caller's
    # credential prefix — the same inputs the request-gate kill-switch check uses.
    _midstream_state_check = _build_midstream_state_check(
        str(body.get("model", "") or ""),
        org_slug or "default",
        getattr(auth_ctx, "prefix", "") if auth_ctx else "",
    )

    async def _provider_stream():
        async for chunk in LLM_ROUTER.acompletion_stream(
            body,
            redacted_prompt,
            metrics=stream_metrics,
            echo_model=_stream_echo_model,
            state_check=_midstream_state_check,
        ):
            yield chunk

    inner = _provider_stream()
    if scan_mode != StreamScanMode.NONE and INPUT_SCANNER is not None and CONFIG.get("output_scan_enabled", True):
        if scan_mode == StreamScanMode.OUTPUT_GUARD and OUTPUT_GUARD is not None:
            # M-05(a): the OUTPUT_GUARD streaming path must run the guard WITH
            # request context (org tri-state tier-2 gating + correct breaker org
            # attribution + RAG grounding). wrap_secure_stream_if_needed does not
            # forward org_config/org_slug/context, so build the secure stream here
            # to thread them through. Other scan modes still delegate to the shared
            # wrapper (unchanged behavior). All additive/defensive — context is
            # resolved lazily and fails open to no-context.
            inner = _wrap_secure_stream_with_context(
                inner,
                org_config=org_config,
                org_slug=org_slug or "default",
                request=request,
                ctx=ctx,
                stream_metrics=stream_metrics,
                enforcement_mode=enforcement_mode,
            )
        else:
            inner = wrap_secure_stream_if_needed(
                inner,
                scan_mode=scan_mode,
                config={**CONFIG, **org_config},
                input_scanner=INPUT_SCANNER,
                output_guard=OUTPUT_GUARD,
                telemetry=TELEMETRY,
                ctx=ctx,
                record_guard_metric=_prom_record_stream_guard,
                stream_metrics=stream_metrics,
                enforcement_mode=enforcement_mode,
                request=request,  # streaming #4
            )

    # M-51: terminal zeroshield trace frame (emitted once before [DONE]).
    _stream_zs_base = _build_stream_zeroshield_base(
        request_id=request_id,
        scan_verdict=scan_verdict,
        redacted_prompt=redacted_prompt,
        route_selection=route_selection,
    )
    # FULL-PIPELINE-ON-STREAM: build the SAME 9-stage pipeline_trace the non-stream
    # path returns, so streaming clients render the COMPLETE pipeline (auth → rate
    # limit → policy → input scan → kill switch → routing → model input → model
    # output → output guard), not just the routing summary. Stages come from
    # scan_verdict + the routing already in the zeroshield base; the output_guard
    # stage is overlaid with the mid-stream guard outcome inside
    # build_stream_trace_frame. Fail-open — a trace build error never breaks the stream.
    _stream_pt_base = None
    try:
        from pipeline_trace import build_pipeline_trace as _bpt_stream

        _stream_pt_base = _bpt_stream(
            forwarded_prompt=redacted_prompt or "",
            scan_verdict=scan_verdict,
            zeroshield=_stream_zs_base,
            requested_model=_stream_echo_model,
            final_action=str((_stream_zs_base or {}).get("action") or "allow"),
            http_status=200,
        )
    except Exception:
        LOG.debug("streaming pipeline_trace build failed; routing-only trace emitted", exc_info=True)

    finalized = stream_with_finalize(
        inner,
        ctx,
        _stream_finalize_hooks(),
        decision="allowed",
        metrics=stream_metrics,
        request=request,  # streaming #4: poll is_disconnected() to halt abandoned streams
        finalize_timeout_ms=int(
            org_config.get("stream_finalize_timeout_ms", CONFIG.get("stream_finalize_timeout_ms", 5000))
        ),
        zeroshield_base=_stream_zs_base,
        pipeline_trace_base=_stream_pt_base,
    )
    return StreamingResponse(
        finalized,
        media_type="text/event-stream",
        headers=headers,
    )


_ROUTING_SENTINEL_MODELS = frozenset({"", "auto"})


def _is_routing_sentinel_model(model: str) -> bool:
    """Client hint meaning 'pick via router' — not an allowlist entry.

    Defensive ``str(...)`` coercion: the chat boundary validator rejects a
    non-string ``model`` with a 400, but this helper is also reached from
    embeddings/estimate paths where a non-string (e.g. ``123``) would make a
    bare ``(model or "").strip()`` raise AttributeError -> unhandled 500.
    """
    return str(model or "").strip().lower() in _ROUTING_SENTINEL_MODELS


def _resolve_routing_hint_model(
    requested_model: str,
    org_config: dict,
    inference_models: list[dict] | None,
) -> str:
    """Map auto/empty to org default_model or first connected inference model."""
    current = (requested_model or "").strip()
    if not _is_routing_sentinel_model(current):
        return current
    default = str(org_config.get("default_model") or "").strip()
    if default and not _is_routing_sentinel_model(default):
        return default
    for entry in inference_models or []:
        name = str(entry.get("model_name") or "").strip()
        # R#7: never auto-resolve a chat request to a guard/internal model OR an
        # embedding-only model — the latter cannot produce a scannable chat
        # completion. Skip both and fall through to the next candidate.
        if name and not _is_guard_only_model(entry) and not _is_embedding_model(entry):
            return name
    return current


def _guard_model_names() -> frozenset[str]:
    """Models reserved for ZeroShield input/output scanning — never chat inference.

    P5c: delegate to / union with the authoritative platform guard set so this
    local copy can't silently drift from ``platform_models.guard_model_names()``.
    Falls back to a hardened local set (env default ``zeroshield-model`` + the
    known guard ids) when that module is not importable in the gateway.
    """
    names = {
        os.getenv("ZEROSHIELD_GUARD_MODEL_NAME", "zeroshield-model").strip().lower(),
        "zeroshield-model",
        "zeroshield-guard-120b",
        "bedrock-gpt-oss-120b",
        "bedrock-gpt-oss-120b-long-context",
    }
    try:
        try:
            from platform_models import guard_model_names as _authoritative_guard_names
        except ImportError:
            from .platform_models import guard_model_names as _authoritative_guard_names  # type: ignore
        names |= {str(n).strip().lower() for n in _authoritative_guard_names()}
    except Exception:  # noqa: BLE001 — never let the union break the guard check
        pass
    return frozenset(n for n in names if n)


def _is_guard_only_model(model: dict) -> bool:
    name = str(model.get("model_name") or "").strip().lower()
    if name in _guard_model_names():
        return True
    provider = str(model.get("provider") or "").strip().lower()
    return provider == "internal"


def _is_embedding_model(model: dict) -> bool:
    """True when a routing entry is an EMBEDDING model (not chat-capable).

    R#7: kill-switch / model-state reroute and auto-routing must never land a
    chat request on an embedding-only model — that path produces a malformed /
    empty completion and the output guard (single-choice, message-content based)
    cannot scan it. There is no dedicated chat-vs-embedding flag on the routing
    payload, so detect by the conventional markers: a ``model_type`` / ``mode``
    of ``embedding``, an explicit ``embedding`` capability, or the de-facto
    signal — ``embed`` in the user-facing name or LiteLLM model id
    (e.g. ``text-embedding-3-small``, ``cohere/embed-english-v3``).
    """
    if not isinstance(model, dict):
        return False
    model_type = str(model.get("model_type") or model.get("mode") or "").strip().lower()
    if model_type == "embedding":
        return True
    caps = model.get("capabilities")
    if isinstance(caps, (list, tuple, set)) and "embedding" in {str(c).strip().lower() for c in caps}:
        return True
    name = str(model.get("model_name") or "").strip().lower()
    model_id = str(model.get("model_id") or "").strip().lower()
    return "embed" in name or "embed" in model_id


def _chat_capable_models(routing_models: list[dict] | None) -> list[dict]:
    """Routing entries that may serve a chat completion — excludes guard/internal
    and embedding-only models. Used to bound reroute/auto-routing candidates so a
    chat request is never satisfied by an embedding model (R#7)."""
    out: list[dict] = []
    for model in routing_models or []:
        if not isinstance(model, dict):
            continue
        if _is_guard_only_model(model) or _is_embedding_model(model):
            continue
        out.append(model)
    return out


def _filter_inference_eligible_models(routing_models: list[dict] | None) -> list[dict]:
    """Org-connected customer models only — not ZeroShield guard / internal scan models."""
    models = routing_models or []
    eligible: list[dict] = []
    for model in models:
        if _is_guard_only_model(model):
            continue
        if not model.get("is_active", True):
            continue
        if not (model.get("model_name") or model.get("model_id")):
            continue
        provider = str(model.get("provider") or "").strip().lower()
        api_key_set = bool(model.get("api_key_set"))
        # BYOK-via-env: a model may carry its key by ENV-VAR REFERENCE
        # (api_key_env_var -> litellm_params api_key="os.environ/NAME") instead
        # of a stored encrypted key. Treat it as credentialed when that env var
        # is actually present in the gateway environment — the "connect a key
        # without persisting it in the DB" path.
        env_var = str(model.get("api_key_env_var") or "").strip()
        api_key_via_env = bool(env_var and os.environ.get(env_var))
        # Organization-owned inference must carry tenant credentials.
        # Local/self-hosted ollama can run without an API key. AWS Bedrock
        # authenticates via the AWS credential chain (env keys, shared config,
        # or — in prod — the EC2 instance role), NOT an api_key, so a keyless
        # Bedrock config is legitimately credentialed via that chain.
        if provider not in {"ollama", "bedrock", "aws_bedrock"} and not (
            api_key_set or api_key_via_env
        ):
            continue
        eligible.append(model)
    return eligible


async def _drop_isolated_or_killed_candidates(models, org_slug: str, key_prefix: str):
    """B1 FIX (CRITICAL): drop models that are runtime-ISOLATED (model_state) or
    KILL-SWITCHED from the routing candidate set so the adjudicator can never SELECT
    a disabled model. Isolation/kill-switch were previously enforced only on the
    pre-routing *requested* model (``check_model_state``/``check_kill_switch`` at the
    request gate), but ``_score_routing_models`` hard-filters only on ``is_active`` —
    so ``model='auto'`` / adjudicated routing routed straight onto isolated/killed
    models (B1). Returns ``(servable_models, excluded_names)``. Fails OPEN on a check
    error (never breaks routing) but honours the checks' own fail-CLOSED verdicts
    (``suspended``/``is_killed``). Logs exclusions (B3 audit trail)."""
    if not models or REDIS_CLIENT is None:
        return models, []
    try:
        from model_state import check_model_state as _cms
        from kill_switch import check_kill_switch as _cks
    except ImportError:
        from .model_state import check_model_state as _cms
        from .kill_switch import check_kill_switch as _cks
    import asyncio as _aio

    async def _servable(m: dict) -> bool:
        name = str(m.get("model_name") or m.get("model_id") or "").strip()
        if not name:
            return True
        try:
            ms = await _cms(REDIS_CLIENT, name, org_slug or "default")
            if str(getattr(ms, "status", "active")) in ("isolated", "suspended") and \
                    str(getattr(ms, "action", "")) != "alert":
                return False
            ks = await _cks(REDIS_CLIENT, name, org_slug or "", key_prefix or "")
            if getattr(ks, "is_killed", False):
                return False
        except Exception:
            return True  # never break routing on a transient check error
        return True

    flags = await _aio.gather(*[_servable(m) for m in models])
    kept = [m for m, ok in zip(models, flags) if ok]
    excluded = [str(m.get("model_name")) for m, ok in zip(models, flags) if not ok]
    if excluded:
        LOG.warning(
            "B1: excluded isolated/kill-switched models from routing candidates: %s (org=%s)",
            excluded, org_slug,
        )
    return kept, excluded


def _build_midstream_state_check(model: str, org_slug: str, key_prefix: str):
    """E13: build the async state-check callback handed to
    ``LLM_ROUTER.acompletion_stream``.

    A stream that has STARTED has no per-chunk policy gate, so if an operator
    trips the kill-switch (or model-state isolation) for the ACTIVE model DURING
    an active stream, the gateway would keep streaming the remainder from the
    now-disabled model. The router calls this callback THROTTLED inside the chunk
    loop; we return ``True`` ONLY on a DEFINITIVE kill/isolate verdict so the
    router halts the stream with a terminal error SSE.

    FAIL-OPEN: any error (Redis down, import failure, REDIS unavailable, missing
    model) returns ``False`` — a transient hiccup must never terminate a
    legitimate live stream. Only a concrete ``is_killed`` / ``isolated`` /
    ``suspended`` verdict ends the stream. Mirrors the dual-check the
    kill-switch reroute path performs at the request gate (check_kill_switch +
    check_model_state) so mid-stream and pre-stream enforcement agree.
    """
    if not model or REDIS_CLIENT is None:
        return None

    async def _check() -> bool:
        try:
            try:
                from model_state import check_model_state as _cms
                from kill_switch import check_kill_switch as _cks
            except ImportError:
                from .model_state import check_model_state as _cms
                from .kill_switch import check_kill_switch as _cks
            ks = await _cks(REDIS_CLIENT, model, org_slug or "", key_prefix or "")
            # FAIL-OPEN mid-stream: check_kill_switch fails CLOSED on a Redis error
            # by RETURNING is_killed=True with scope='redis_unavailable' (correct for
            # the request gate). But a transient Redis blip must NOT tear down an
            # ALREADY-ADMITTED live stream, so the redis-unavailable artifact is NOT
            # treated as a kill here. A REAL operator kill (scope credential/org_model)
            # still halts the stream.
            if getattr(ks, "is_killed", False) and \
                    str(getattr(ks, "scope", "")) != "redis_unavailable":
                return True
            ms = await _cms(REDIS_CLIENT, model, org_slug or "default")
            # Mirror _drop_isolated_or_killed_candidates: an 'alert'-only degraded
            # model is NOT disabled, so do not terminate on it. Also fail-OPEN on the
            # model-state Redis-error/malformed artifact (status 'suspended' with a
            # 'Redis unavailable' / 'Malformed state data' reason) — only a REAL
            # isolate/suspend halts the stream.
            _ms_reason = str(getattr(ms, "reason", "") or "")
            if str(getattr(ms, "status", "active")) in ("isolated", "suspended") and \
                    str(getattr(ms, "action", "")) != "alert" and \
                    not _ms_reason.startswith(("Redis unavailable", "Malformed state data")):
                return True
        except Exception:
            # FAIL-OPEN: never kill a legitimate stream on a check error.
            return False
        return False

    return _check


def _filter_embedding_eligible_models(routing_models: list[dict] | None) -> list[dict]:
    """Org-connected EMBEDDING models only — the embeddings-path analogue of
    ``_filter_inference_eligible_models``. Keeps only active, credentialed
    embedding-typed entries owned by the org so /v1/embeddings can enforce the
    same tenant boundary as /v1/chat (a model-less org must not reach another
    org's BYOK embedding model via the shared global router)."""
    models = routing_models or []
    eligible: list[dict] = []
    for model in models:
        if not _is_embedding_model(model):
            continue
        if _is_guard_only_model(model):
            continue
        if not model.get("is_active", True):
            continue
        if not (model.get("model_name") or model.get("model_id")):
            continue
        provider = str(model.get("provider") or "").strip().lower()
        api_key_set = bool(model.get("api_key_set"))
        env_var = str(model.get("api_key_env_var") or "").strip()
        api_key_via_env = bool(env_var and os.environ.get(env_var))
        if provider not in {"ollama"} and not (api_key_set or api_key_via_env):
            continue
        eligible.append(model)
    return eligible


def _build_no_inference_provider_response() -> JSONResponse:
    """Client must connect their own LLM; developer guard credentials are not used for inference."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "no_provider_configured",
            "message": (
                "No organization inference model is configured. "
                "Connect your provider API key under Firewall → Multi-Model Governance (1.5) → Model Connection. "
                "Organization keys are encrypted at rest and used for inference billing. "
                "ZeroShield guard models are used only for input and output scanning."
            ),
            "code": "no_provider_configured",
            "blocked_by": "model_routing",
            "category": "inference_not_configured",
        },
    )


def _validate_org_inference_model(
    *,
    requested_model: str,
    body: dict,
    inference_models: list[dict],
) -> JSONResponse | None:
    """Return an error response when inference would use a non-org model."""
    allowed = _routing_identity_set(inference_models)
    if not allowed:
        return _build_no_inference_provider_response()

    final_model = str(body.get("model") or requested_model or "").strip()
    if final_model.lower() in _guard_model_names():
        return JSONResponse(
            status_code=422,
            content={
                "error": "guard_model_not_for_inference",
                "message": (
                    "ZeroShield Guard Model is for input/output scanning only. "
                    "Connect your organization's inference model under Model Connection (module 1.5)."
                ),
                "code": "guard_model_not_for_inference",
                "blocked_by": "model_routing",
                "category": "inference_not_configured",
            },
        )
    # Echo only a sanitized form of the client model in the error body (R4).
    _safe_final = _safe_model_echo(final_model)
    if final_model and final_model not in allowed and final_model != "auto":
        return JSONResponse(
            status_code=404,
            content={
                "error": "model_not_configured",
                "message": (
                    f"Model '{_safe_final}' is not configured for organization inference. "
                    "Add it under Model Connection with your provider API key."
                ),
                "code": "model_not_configured",
                "blocked_by": "model_routing",
                "category": "inference_not_configured",
            },
        )
    if final_model == "auto" or not final_model:
        return None
    if final_model not in allowed:
        return JSONResponse(
            status_code=404,
            content={
                "error": "model_not_configured",
                "message": f"Model '{_safe_final}' is not available for this organization.",
                "code": "model_not_configured",
                "blocked_by": "model_routing",
            },
        )
    return None


def _routing_identity_set(routing_models: list[dict] | None) -> set[str]:
    identities: set[str] = set()
    for model in routing_models or []:
        model_name = str(model.get("model_name") or "").strip()
        model_id = str(model.get("model_id") or "").strip()
        if model_name:
            identities.add(model_name)
        if model_id:
            identities.add(model_id)
    return identities


def _routing_compliance_required(routing_prefs: dict) -> bool:
    """FIX-1.5a: True when the request carries a compliance/sensitivity demand.

    A request demands compliant routing when it lists required compliance tags OR
    its data sensitivity resolves above 'public'. Benign (no-compliance) requests
    return False and are never subject to the fail-closed gate below.
    """
    if [t for t in (routing_prefs.get("required_compliance") or []) if t]:
        return True
    return str(routing_prefs.get("data_sensitivity") or "public").strip().lower() not in (
        "",
        "public",
    )


def _find_routing_model_entry(
    routing_models: list[dict] | None, model_name: str
) -> dict | None:
    """Return the routing-model entry whose model_name/model_id matches, or None."""
    if not model_name:
        return None
    for model in routing_models or []:
        if str(model.get("model_name") or "") == model_name or str(
            model.get("model_id") or ""
        ) == model_name:
            return model
    return None


def _compliance_unsatisfiable_reason(routing_prefs: dict) -> str:
    """Human-readable reason naming the unsatisfied compliance/sensitivity demand."""
    tags = [t for t in (routing_prefs.get("required_compliance") or []) if t]
    sens = str(routing_prefs.get("data_sensitivity") or "public").strip().lower()
    parts = []
    if tags:
        parts.append(f"required compliance tag(s) {tags}")
    if sens not in ("", "public"):
        parts.append(f"data sensitivity '{sens}'")
    demand = " and ".join(parts) if parts else "compliance/sensitivity requirement"
    return f"No connected model satisfies {demand}."


_OUTPUT_ACTION_PRIORITY = {
    "allow": 0,
    "flag": 1,
    "rewrite": 2,
    "redact": 3,
    "block": 4,
}


def _normalize_output_action(action: str | None) -> str:
    if action == "monitor":
        return "flag"
    if action in _OUTPUT_ACTION_PRIORITY:
        return str(action)
    return "allow"


def _set_completion_response_text(completion: dict, text: str) -> None:
    choices = completion.get("choices") or []
    if not choices:
        return
    # R12 (#14): enforcement (redact/rewrite/block) must sanitize EVERY choice.
    # Previously only choices[0] was overwritten + neutralized, so with n>1 the
    # offending content survived in choices[1..] (output-guard bypass). The whole
    # response violated policy, so every choice gets the sanitized text + has its
    # secondary text channels blanked.
    for ch in choices:
        if not isinstance(ch, dict):
            continue
        if isinstance(ch.get("message"), dict):
            ch["message"]["content"] = text
            ch["message"]["role"] = ch["message"].get("role") or "assistant"
            _neutralize_secondary_output_channels(ch["message"])
        elif isinstance(ch.get("delta"), dict):
            ch["delta"]["content"] = text
            _neutralize_secondary_output_channels(ch["delta"])
        else:
            ch["message"] = {"role": "assistant", "content": text}


def _rewrite_output_response_text(threat_type: str, detail: str | None = None) -> str:
    # Static deterministic fallback (used when no original text / no model is
    # available, or as the safety net if re-inference fails).
    try:
        from output_guard import rewrite_output_response_text
        return rewrite_output_response_text(threat_type, detail)
    except Exception:
        return "The original model output was rewritten to comply with response safety policy."


async def _rewrite_output_response_text_via_router(
    threat_type: str,
    detail: str | None,
    original_text: str | None,
    base_body: dict | None,
) -> tuple[str, bool]:
    """Content-preserving 'rewrite' output action via real re-inference.

    A model has no memory of the response it just produced, so a meaningful
    rewrite MUST feed the FULL original output back in. This re-infers a
    sanitized version through the org's own routed model (LLM_ROUTER → the BYOK
    provider), neutralizing the offending content while preserving the rest.
    Falls back to the deterministic static rewrite when no original text / no
    router / inference fails, so the rewrite action can never raise into the
    response path. The result always passes through ``redact_all`` as a final
    deterministic safety net so a residual secret/PII can't survive the rewrite.

    R3: returns ``(text, reinferred)`` — ``reinferred`` is True when the result
    came from a genuine content-preserving model re-inference, and False when it
    fell back to the static canned template (no original text / no router /
    inference failed/empty). Callers surface ``rewrite_degraded = not reinferred``
    in telemetry + trace so operators can tell a model-sanitized rewrite from a
    canned whole-response replacement.
    """
    if not original_text or LLM_ROUTER is None or not isinstance(base_body, dict):
        return _rewrite_output_response_text(threat_type, detail), False
    try:
        from output_guard import (
            _REWRITE_SYSTEM_PROMPT,
            _REWRITE_THREAT_GUIDANCE,
            _MAX_REWRITE_INPUT_CHARS,
            redact_all,
        )

        guidance = _REWRITE_THREAT_GUIDANCE.get(
            threat_type or "", _REWRITE_THREAT_GUIDANCE.get("policy_violation", "")
        )
        snippet = str(original_text)[:_MAX_REWRITE_INPUT_CHARS]
        user_text = (
            f"Violation type: {threat_type or 'policy_violation'}\n"
            f"Instruction: {guidance}\n\n"
            f"Original response to rewrite:\n{snippet}"
        )
        rewrite_body = {
            "model": base_body.get("model", ""),
            "messages": [
                {"role": "system", "content": _REWRITE_SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            "max_tokens": 1024,
            "temperature": 0.0,
            # Never recurse the firewall / routing adjudication for the rewrite.
            "routing_preferences": {"enable_routing": False},
            # H7: inherit the org so the rewrite re-inference also routes to THIS
            # org's deployment + BYOK key.
            "_zs_org_slug": base_body.get("_zs_org_slug", ""),
        }
        code, resp = await LLM_ROUTER.acompletion(rewrite_body, None)
        if code == 200:
            text = _extract_response_from_completion(resp)
            if text and text.strip():
                return redact_all(text.strip()), True
        LOG.warning(
            "Output rewrite re-inference returned no usable text (code=%s); using static fallback",
            code,
        )
    except Exception:  # noqa: BLE001 — rewrite must never raise into the response path
        LOG.exception("Output rewrite re-inference failed; using static fallback")
    return _rewrite_output_response_text(threat_type, detail), False


def _sanitize_output_for_verdict(response_text: str, verdict) -> str:
    """Apply the correct sanitization for an output-guard verdict action."""
    from output_guard import sanitize_output_for_verdict

    redact_fn = INPUT_SCANNER.redact_pii if INPUT_SCANNER is not None else None
    return sanitize_output_for_verdict(response_text, verdict, redact_pii_fn=redact_fn)


def _output_guard_telemetry_meta(verdict, *, raw_output: str, sanitized_output: str) -> dict:
    from output_guard import output_guard_telemetry_meta

    return output_guard_telemetry_meta(verdict, raw_output=raw_output, sanitized_output=sanitized_output)


async def _scan_redact_embedding_inputs(
    texts: list[str],
    org_config: dict,
) -> tuple[list[str], dict | None]:
    """G1/G3/G4: scan + redact PII/secrets in embedding inputs before they leave
    the gateway, mirroring the chat path's pre-LLM input redaction.

    Both ``/v1/embeddings`` (proxy_embeddings) and RAG-ingest send raw text to an
    upstream embedding provider. Unlike ``/v1/chat``, that text was NEVER scanned
    or redacted, so a customer email/SSN/API-key was embedded verbatim by the
    third-party provider. This helper closes that gap with the SAME tier-1 input
    scanner + verdict-aware redactor the chat path uses, gated by the SAME
    ``input_scan_enabled`` config so there is no behavior change when input
    scanning is disabled.

    Tier-1 only (``scan_prompt`` — NOT ``scan_prompt_with_tier2``): embeddings are
    batched/large, so the per-item ML round-trip of Tier-2 is intentionally NOT run
    here (parity with the moderations surface).

    Returns ``(redacted_texts, None)`` on success. When an input carries PII/secret
    that genuinely CANNOT be masked (redaction was a no-op AND the fail-closed digit
    backstop also can't mask it), returns ``(partial_texts, block_meta)`` where
    ``block_meta`` is ``{"blocked": True, "reason": ..., "index": i}`` so the caller
    fails closed rather than embedding the raw value (G4).
    """
    # No-op (and therefore NO behavior change) when scanning is disabled or the
    # scanner is unavailable. This is REQUIRED for the no-regression guarantee.
    if INPUT_SCANNER is None or not org_config.get("input_scan_enabled", True):
        return texts, None

    # B4 (egress parity / one redaction implementation): redact embedding inputs with
    # the SAME deterministic egress redactor the chat & operator-simulator path uses
    # (``LLMRouter._apply_redaction`` -> ``llm_router._redact_text_with_backstop``), so
    # /v1/embeddings and RAG-ingest egress are BYTE-IDENTICAL to chat for the same
    # input. ``redact_pii`` (== ``redact_all`` + Tier-2 evidence-digit-spans) is the
    # firewall's authoritative "what must never reach the provider"; the shared
    # backstop then masks ONLY digit runs the firewall removed — so an order-id the
    # firewall deliberately KEPT stays intact (no over-redaction divergence) while any
    # separator-split phone the firewall masked can never ride raw (egress = truth).
    try:
        from llm_router import _redact_text_with_backstop  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover - packaging fallback
        from .llm_router import _redact_text_with_backstop  # type: ignore[no-redef]

    redacted_texts: list[str] = []
    for i, text in enumerate(texts):
        if not isinstance(text, str) or not text.strip():
            redacted_texts.append(text)
            continue

        verdict = await INPUT_SCANNER.scan_prompt(text)
        red_pii = INPUT_SCANNER.redact_pii(text, verdict=verdict)

        _detected = (
            getattr(verdict, "threat_type", "") in ("pii", "secret")
            or bool(getattr(verdict, "matched_patterns", None))
            or bool(getattr(verdict, "matched_values", None))
        )
        # Shared egress redactor — identical to the chat/simulator wire path. Uses the
        # firewall-authoritative ``red_pii`` as the egress-truth reference so the
        # masked output matches chat byte-for-byte (Tier-2 evidence spans are already
        # folded into ``red_pii`` and so are honored transitively).
        redacted = _redact_text_with_backstop(text, red_pii)

        # BYTE-VERIFY FAIL-CLOSED (G4): the scanner detected PII/secret but the
        # redaction (incl. the digit backstop above) could not change the input.
        # Masking is genuinely impossible (e.g. a detected value with no
        # deterministic mask, like a bare name), so this input cannot be safely
        # embedded — fail closed instead of leaking the raw value to the provider.
        if _detected and redacted == text:
            return redacted_texts, {
                "blocked": True,
                "reason": "PII detected in embedding input could not be redacted",
                "index": i,
            }

        redacted_texts.append(redacted)

    return redacted_texts, None


async def _scan_redact_metadata(meta: dict, org_config: dict) -> dict:
    """Redact PII/secrets in metadata string VALUES at ingest, at PARITY with the
    document content — gated by the SAME ``input_scan_enabled`` config. Caller
    metadata was previously only redacted under ``rag_redaction_enabled`` (off by
    default), so PII in a metadata field was stored RAW in the vector store (proven
    live on Pinecone: author_email/owner_phone/owner_ssn persisted verbatim).
    Recurses into nested dict/list so a value can't hide one level deep; uses the
    same INPUT_SCANNER redactor + digit backstop as ``_scan_redact_embedding_inputs``.
    """
    if not isinstance(meta, dict) or INPUT_SCANNER is None or not org_config.get("input_scan_enabled", True):
        return meta

    async def _r(v):
        if isinstance(v, str) and v.strip():
            red, _blk = await _scan_redact_embedding_inputs([v], org_config)
            # ``red`` is non-empty for clean/maskable values; it is empty only when
            # the value carried PII that could not be masked -> never persist raw.
            return red[0] if red else "[REDACTED]"
        if isinstance(v, dict):
            return {k: await _r(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [await _r(item) for item in v]
        return v

    return {k: await _r(v) for k, v in meta.items()}


def _merge_output_enforcement_state(existing: dict | None, candidate: dict | None) -> dict | None:
    if candidate is None:
        return existing

    normalized_candidate = dict(candidate)
    normalized_candidate["action"] = _normalize_output_action(normalized_candidate.get("action"))
    if existing is None:
        return normalized_candidate

    current_priority = _OUTPUT_ACTION_PRIORITY.get(_normalize_output_action(existing.get("action")), 0)
    candidate_priority = _OUTPUT_ACTION_PRIORITY.get(normalized_candidate["action"], 0)
    merged = dict(existing if current_priority > candidate_priority else normalized_candidate)
    merged["review_required"] = bool(existing.get("review_required")) or bool(normalized_candidate.get("review_required"))
    merged["security_incident"] = bool(existing.get("security_incident")) or bool(normalized_candidate.get("security_incident"))
    merged["factuality_warning"] = bool(existing.get("factuality_warning")) or bool(normalized_candidate.get("factuality_warning"))
    if merged.get("action") == "allow" and merged.get("review_required"):
        merged["action"] = "flag"
    return merged


def _is_tier2_degraded_verdict(verdict) -> bool:
    if verdict is None:
        return False
    if verdict.tier != "tier_2":
        return False
    if verdict.threat_type == "bedrock_degraded":
        return True
    return str(getattr(verdict, "reason_code", "")).startswith("degraded")


def _resolve_success_metadata_from_verdict(scan_verdict) -> tuple[str, str, str, float, list[str], str]:
    """
    Resolve action/reason/threat/confidence from scanner verdict for successful
    (HTTP 200) responses so flagged Tier-2 results are not flattened.
    """
    if scan_verdict is None:
        return (
            "allow",
            "All security checks passed. No threats detected.",
            "none",
            0.0,
            [],
            "",
        )

    action = scan_verdict.action if scan_verdict.action in ("allow", "flag") else "allow"
    threat_type = scan_verdict.threat_type or "none"
    confidence = float(scan_verdict.confidence or 0.0)
    matched_patterns = list(scan_verdict.matched_patterns or [])
    detail = scan_verdict.detail or ""
    if action == "flag":
        reason = detail or f"Scanner flagged potential threat ({threat_type})."
    else:
        reason = detail or "All security checks passed. No threats detected."

    return action, reason, threat_type, confidence, matched_patterns, detail


def _redact_trace_text(text) -> str:
    """Deterministically redact PII/secrets from any text destined for the
    operator pipeline_trace (R17).

    The pipeline_trace is surfaced verbatim in the operator simulator UI, so the
    RAW prompt / response text written into it must pass through ``redact_all``
    first — otherwise live PII (the very thing the firewall redacts before the
    LLM ever sees it) re-appears in the trace. Fail OPEN to an empty string so a
    redaction error can never break the response path."""
    if not text:
        return ""
    try:
        try:
            from .patterns import redact_all
        except ImportError:
            from patterns import redact_all  # type: ignore[no-redef]
        return redact_all(str(text))
    except Exception:  # noqa: BLE001 — trace redaction must never raise into the response
        LOG.exception("pipeline_trace redaction failed; dropping raw text")
        return ""


def _build_block_response(
    status_code: int,
    code: str,
    zeroshield: dict,
    *,
    stage_metrics: dict | None = None,
    prompt: str = "",
    route_metadata: dict | None = None,
    requested_model: str = "",
    scan_verdict=None,
    output_scan_verdict=None,
) -> JSONResponse:
    """
    Build a unified blocked JSONResponse with zeroshield metadata.
    CRITICAL: Uses _build_safe_block_response to NEVER expose sensitive details.
    In zeroshield dict: extracts threat category, redacts internal details, and keeps only safe metadata.
    """
    from pipeline_trace import build_pipeline_trace

    threat_category = zeroshield.get("threat_type", "policy_violation")
    request_id = zeroshield.get("request_id")
    internal_detail = zeroshield.get("detail")  # Will be logged server-side only
    detection_tier = str(zeroshield.get("detection_tier") or "")
    blocked_stage = _resolve_pipeline_blocked_by(
        code=code,
        threat_category=threat_category,
        detection_tier=detection_tier,
    )
    pipeline_trace = build_pipeline_trace(
        prompt=_redact_trace_text(prompt),
        stage_metrics=stage_metrics,
        final_action="block",
        blocked_stage=blocked_stage,
        http_status=status_code,
        scan_verdict=scan_verdict,
        zeroshield=zeroshield,
        route_metadata=route_metadata,
        blocked_detail=internal_detail or "",
        requested_model=requested_model,
        output_scan_verdict=output_scan_verdict,
    )

    return _build_safe_block_response(
        status_code=status_code,
        code=code,
        threat_category=threat_category,
        request_id=request_id,
        internal_detail=internal_detail,
        detection_tier=detection_tier,
        pipeline_trace=pipeline_trace,
    )


def _audit_fire_and_forget(
    *,
    org_slug: str,
    decision: str,
    rule_code: str,
    metadata: dict | None = None,
) -> None:
    """D_G5: emit a query-audit event without blocking the hot path.

    Safe to call from anywhere; never raises. ``decision == "allow"`` is a
    no-op inside ``emit_query_audit_event`` so callers don't have to branch.
    """
    if not org_slug:
        return
    try:
        from telemetry_ops import emit_query_audit_event, _log_task_exception  # type: ignore
        task = asyncio.create_task(
            emit_query_audit_event(
                org_slug=org_slug,
                decision=decision,
                rule_code=str(rule_code or "unknown"),
                metadata=metadata or {},
            )
        )
        task.add_done_callback(_log_task_exception)
    except Exception as _exc:  # never break the request flow
        LOG.debug("audit fire-and-forget skipped: %r", _exc)


_CATEGORY_THREAT_MAP: dict[str, str] = {
    "threat_detection": "prompt_injection",
    "pii_protection": "pii",
    "data_protection": "data_leakage",
    "content_safety": "toxicity",
    "compliance": "compliance_violation",
    "rag_security": "rag_poisoning",
    "access_control": "access_violation",
}


def _category_to_threat_type(category: str) -> str:
    """Map a policy category to a threat_type for telemetry enrichment."""
    return _CATEGORY_THREAT_MAP.get(category, "policy_violation")


# ── Per-request context for telemetry enrichment ──
# Set at the start of proxy_chat so all build_telemetry_event calls
# within that request automatically pick up org/IP/method/status.
import contextvars as _ctxvars

_REQUEST_ID: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("gw_request_id", default="")
_REQUEST_ORG_ID: _ctxvars.ContextVar[int | None] = _ctxvars.ContextVar("_req_org_id", default=None)
_REQUEST_ORG_SLUG: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("_req_org_slug", default="")
_REQUEST_SOURCE_IP: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("_req_src_ip", default="")
_REQUEST_METHOD: _ctxvars.ContextVar[str] = _ctxvars.ContextVar("_req_method", default="POST")

# Event types that represent isolation / kill-switch / circuit-breaker actions.
# These are security-critical and MUST always be recorded, even when an org
# has disabled audit logging — otherwise a tenant could silence its own
# containment trail.
_ALWAYS_AUDIT_EVENT_TYPES = ("kill_switch", "model_isolation", "circuit_breaker")


def _org_audit_logging_enabled() -> bool:
    """Resolve the current request org's ``audit_logging_enabled`` setting.

    The control plane maps ``FirewallConfig.audit_logging_enabled`` onto the
    gateway config key ``telemetry_enabled`` (per-org, synced to Redis). This
    helper reads it for the *current request's* org so telemetry/audit
    emission can be skipped when a tenant has turned audit logging off.

    Defaults to ``True`` whenever no per-org value is resolvable, preserving
    backward-compatible behavior for unauthenticated / no-org requests.
    """
    try:
        slug = _REQUEST_ORG_SLUG.get()
        if slug and CONFIG_SYNC is not None:
            oc = CONFIG_SYNC.get_config(slug)
            if oc is not None and "telemetry_enabled" in oc:
                return bool(oc.get("telemetry_enabled", True))
    except Exception:  # noqa: BLE001 — never let the audit gate break the hot path
        pass
    return bool(CONFIG.get("telemetry_enabled", True))


def _telemetry_owasp_metadata(threat_type: str = "", *, verdict=None, extra: dict | None = None) -> dict:
    """Resolve OWASP vector codes for gateway telemetry metadata."""
    from ai_mesh_shared.owasp_telemetry import resolve_owasp_codes

    payload = dict(extra or {})
    codes_from_verdict = list(getattr(verdict, "owasp_codes", None) or [])
    if codes_from_verdict:
        payload.setdefault("owasp_codes", codes_from_verdict)
    codes = resolve_owasp_codes(threat_type or "", payload)
    return {"owasp_codes": codes} if codes else {}


_AUDIT_DROP_LOG_THROTTLE: dict = {}  # org_id -> last monotonic ts of the drop warning


def _emit_telemetry(status_code: int = 200, **kwargs):
    """Convenience: build_telemetry_event + inject per-request context + emit."""
    if TELEMETRY is None:
        return
    from telemetry import build_telemetry_event
    kwargs.setdefault("organization_id", _REQUEST_ORG_ID.get())
    kwargs.setdefault("source_ip", _REQUEST_SOURCE_IP.get())
    kwargs.setdefault("method", _REQUEST_METHOD.get())
    kwargs.setdefault("status_code", status_code)
    # P9c: forward the per-request correlation id to control via the telemetry
    # event metadata (the enforcement-event emit path is the Redis producer, not
    # a direct HTTP POST). Additive; never overwrite an explicit caller value.
    try:
        _rid = _REQUEST_ID.get("")
        if _rid:
            _md = dict(kwargs.get("metadata") or {})
            _md.setdefault("request_id", _rid)
            kwargs["metadata"] = _md
    except Exception:
        pass
    event_type = kwargs.get("event_type") or ""
    _is_isolation = event_type in _ALWAYS_AUDIT_EVENT_TYPES
    # Per-org audit-logging gate (audit_logging_enabled → telemetry_enabled).
    # When a tenant disables audit logging we skip emission, EXCEPT for
    # isolation/kill-switch/circuit-breaker events which are always recorded.
    if not _is_isolation and not _org_audit_logging_enabled():
        # Observability hardening: this drop was previously SILENT, so a STALE
        # synced telemetry_enabled=False would make all chat audit/telemetry
        # (incl. blocked-403s) vanish with no signal, looking like data loss.
        # Emit a per-org rate-limited (1/min) WARNING so the condition is visible
        # without flooding the log for a deliberately-disabled tenant.
        try:
            _org = _REQUEST_ORG_ID.get()
            _now = time.monotonic()
            if _now - _AUDIT_DROP_LOG_THROTTLE.get(_org, 0.0) >= 60.0:
                _AUDIT_DROP_LOG_THROTTLE[_org] = _now
                LOG.warning(
                    "Telemetry/audit DROPPED for org=%s (audit_logging/telemetry_enabled is OFF) "
                    "— event_type=%s. Re-enable audit logging if this is unexpected.",
                    _org,
                    event_type or "?",
                )
        except Exception:
            pass
        return
    if _is_isolation:
        md = dict(kwargs.get("metadata") or {})
        md["is_isolation_event"] = True
        kwargs["metadata"] = md
        if event_type == "kill_switch" and float(kwargs.get("risk_score") or 0) <= 0.60:
            kwargs["risk_score"] = 0.85
    event = build_telemetry_event(**kwargs)
    TELEMETRY.emit(event)
    # Phase 1 pilot: best-effort fan-out to Mongo append-only sink.
    # Gated by GATEWAY_MONGO_TELEMETRY_ENABLED; never blocks the hot path.
    try:
        from telemetry_mongo import is_mongo_telemetry_enabled, record_enforcement_event
        if is_mongo_telemetry_enabled():
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(record_enforcement_event(event if isinstance(event, dict) else dict(event)))
            except RuntimeError:
                pass  # No running loop (e.g., called from sync context); skip silently.
    except Exception:
        pass


async def _enforce_org_tpm_rate_limit(
    auth_ctx,
    *,
    event_type: str,
    model: str = "",
    user_id: int | None = None,
    project_id: str = "",
    estimated_tokens: int = 20,
) -> JSONResponse | None:
    """
    Phase 1 hardening (Module 1 §1.1): enforce the per-org TPM ceiling on
    any endpoint that consumes upstream model / vector-DB capacity.

    Thin shim — actual logic in rate_limit_enforcement.py so it can be unit-
    tested without dragging the full main.py import chain (Py3.9 CI).
    """
    from rate_limit_enforcement import enforce_org_tpm_rate_limit

    return await enforce_org_tpm_rate_limit(
        auth_ctx,
        rate_limiter=RATE_LIMITER,
        config_sync=CONFIG_SYNC,
        metrics=METRICS,
        emit_telemetry=_emit_telemetry,
        event_type=event_type,
        model=model,
        user_id=user_id,
        project_id=project_id,
        estimated_tokens=estimated_tokens,
    )


async def _enforce_org_burst_rpm(
    auth_ctx,
    *,
    event_type: str,
    model: str = "",
    user_id: int | None = None,
    project_id: str = "",
) -> JSONResponse | None:
    """Apply the same per-org burst (req/s) + RPM (req/min) ceiling the chat hot
    path enjoys to other capacity-consuming endpoints (embeddings, RAG).

    Thin shim — actual logic in rate_limit_enforcement.py. Fail-OPEN on Redis
    errors (matches proxy_chat); the fail-CLOSED tenant gate is the TPM check.
    """
    from rate_limit_enforcement import enforce_org_burst_rpm

    return await enforce_org_burst_rpm(
        auth_ctx,
        redis_client=REDIS_CLIENT,
        config_sync=CONFIG_SYNC,
        gateway_config=CONFIG,
        metrics=METRICS,
        emit_telemetry=_emit_telemetry,
        event_type=event_type,
        model=model,
        user_id=user_id,
        project_id=project_id,
    )


def _require_admin_role(request) -> JSONResponse | None:
    """
    Phase A.2b hardening (Module 1): gate /v1/admin/* endpoints behind an
    admin RBAC check. Returns a JSONResponse (401/403) when the caller is
    not admin — handler must return it verbatim.

    Phase 1 Fx-2a: accept a server-to-server bypass when the request carries
    ``X-Internal-Key`` matching ``GATEWAY_INTERNAL_API_KEY``. This lets the
    control plane proxy admin reads/mutations on behalf of a Django-RBAC'd
    user without exposing gateway admin endpoints to user JWTs directly.

    Thin shim — actual predicate in admin_auth.py so it is Py3.9-testable
    without dragging the full main.py import chain.
    """
    from admin_auth import require_admin

    # Header name matches the existing mcp_proxy convention so the control
    # plane can use a single shared secret for all server-to-server calls.
    internal_key = os.environ.get("GATEWAY_INTERNAL_API_KEY", "").strip()
    if internal_key:
        header_key = (request.headers.get("x-gateway-internal-key") or "").strip()
        if header_key and header_key == internal_key:
            return None

    auth_ctx = getattr(request.state, "auth_context", None)
    return require_admin(auth_ctx)


async def _telemetry_loop():
    while True:
        await asyncio.sleep(CONFIG["stats_interval_sec"])
        if not AGENT_ID:
            continue
        url = f"{CONFIG['backend_url']}/api/gateways/instances/telemetry/"
        total = METRICS["total_requests"]
        blocked = METRICS["blocked"]
        allowed = METRICS["allowed"]
        sum_ms = METRICS["sum_latency_ms"]
        avg_ms = sum_ms / (total - blocked) if (total - blocked) > 0 else 0
        payload = {
            "agent_id": AGENT_ID,
            "total_requests": total,
            "blocked": blocked,
            "allowed": allowed,
            "avg_latency_ms": round(avg_ms, 2),
            "active_connections": METRICS["active_connections"],
            "location": CONFIG.get("gateway_location") or "",
        }
        code, _ = await asyncio.to_thread(
            _http_request, "POST", url, data=payload, api_key=CONFIG.get("api_key")
        )
        if code == 200:
            LOG.debug("Telemetry sent")


@app.on_event("startup")
async def startup():
    global CONFIG, CONFIG_SYNC, LLM_ROUTER, POLICY_SYNC, RATE_LIMITER, INPUT_SCANNER
    global VECTOR_POLICY_SYNC, VECTOR_CLIENTS, CONTEXT_GUARD, VECTOR_PROVIDER_SYNC
    global REDIS_CLIENT, TELEMETRY, OUTPUT_GUARD, CIRCUIT_BREAKER
    global BEDROCK_EMBEDDER, GROUNDING_GUARD
    import sys

    CONFIG = load_config()
    app.state.config = CONFIG

    # Phase 0 D-G1-v3: detect POLICY_SIGNING_KEY misconfiguration at startup.
    # We intentionally do NOT SystemExit here; that is deferred to release
    # N+2 per docs/UPGRADE.md. Instead: log CRITICAL, emit a Mongo event,
    # and let /health return 503 until operator fixes config.
    if signing_enforced() and _get_signing_key() is None:
        LOG.critical(
            "POLICY_SIGNING_KEY is not set but GATEWAY_POLICY_SIGNING_REQUIRED is true. "
            "Gateway will refuse all policy bundles and /health will report 503. "
            "Set POLICY_SIGNING_KEY explicitly or set GATEWAY_POLICY_SIGNING_REQUIRED=false "
            "for the cutover window only. SystemExit on this condition is deferred to "
            "release N+2; see docs/UPGRADE.md."
        )
        asyncio.create_task(
            emit_operational_event(
                EVENT_CLASS_POLICY_HMAC_MISCONFIG,
                severity="critical",
                metadata={
                    "reason": "POLICY_SIGNING_KEY missing while signing enforced",
                    "site": "startup",
                },
            )
        )
    from config_sync import ConfigSync
    CONFIG_SYNC = ConfigSync(redis_url=CONFIG["redis_url"], config=CONFIG)
    await CONFIG_SYNC.start()
    if CONFIG["backend_url"] and not CONFIG.get("allow_http_backend", True):
        LOG.error(
            "Backend URL must use HTTPS in production. Set AIGUARDX_BACKEND_URL to https://... "
            "or set GATEWAY_ALLOW_HTTP=1 only for development."
        )
        sys.exit(1)
    if CONFIG.get("upstream_llm_url") and not CONFIG.get("allow_http_llm", True):
        LOG.error(
            "Upstream LLM URL must use HTTPS in production. Set GATEWAY_UPSTREAM_LLM_URL to https://... "
            "or set GATEWAY_ALLOW_HTTP_LLM=1 only for development."
        )
        sys.exit(1)

    from llm_router import LLMRouter

    LLM_ROUTER = LLMRouter(CONFIG)
    # Ensure Redis-backed model routes are active immediately at startup.
    if CONFIG_SYNC is not None:
        await CONFIG_SYNC.reload_models_now()

    from rate_limiter import RateLimiter

    RATE_LIMITER = RateLimiter(redis_url=CONFIG["redis_url"])

    # ── Embedding Vault (Tier-1.6 semantic injection check) ──
    # M9: the legacy EmbeddingVault (system pgvector attack-pattern store between
    # Tier-1 regex and the Tier-2 ML guard) is fully REMOVED. It was disabled by
    # default and failed OPEN on credential/config failure (silently disabling a
    # whole detection layer); Tier-2 Bedrock semantic scanning covers it. Input
    # scanning is now Tier-1 + Tier-2 only — no system embedding model.
    _embedding_vault = None

    if CONFIG.get("input_scan_enabled", True):
        from scanner import InputScanner

        INPUT_SCANNER = InputScanner(
            thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
            config=CONFIG,
        )

    # Shared async Redis client for kill-switch + telemetry
    import redis.asyncio as aioredis

    try:
        REDIS_CLIENT = aioredis.from_url(
            CONFIG["redis_url"],
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=2,
        )
        LOG.info("Shared async Redis client initialized")
    except Exception:
        LOG.exception("Failed to create shared async Redis client")

    # Telemetry producer
    if CONFIG.get("telemetry_enabled", True) and REDIS_CLIENT is not None:
        from telemetry import TelemetryProducer

        TELEMETRY = TelemetryProducer(
            redis_client=REDIS_CLIENT,
            flush_interval=CONFIG.get("telemetry_flush_interval", 2.0),
            max_buffer_size=CONFIG.get("telemetry_buffer_size", 100),
        )
        await TELEMETRY.start()

    # Output guard
    if INPUT_SCANNER is not None and CONFIG.get("output_guard_enabled", True):
        from output_guard import OutputGuard

        OUTPUT_GUARD = OutputGuard(scanner=INPUT_SCANNER, config=CONFIG)
        LOG.info("Output guard initialized")

    # Semantic leakage detector (attach to output guard)
    if OUTPUT_GUARD is not None and CONFIG.get("semantic_leakage_enabled", False) and REDIS_CLIENT is not None:
        from leakage_detector import SemanticLeakageDetector

        _leakage = SemanticLeakageDetector(redis_client=REDIS_CLIENT)
        OUTPUT_GUARD.set_leakage_detector(_leakage)
        LOG.info("Semantic leakage detector attached to output guard")

    # Circuit breaker (auto-trigger on error rate threshold)
    if CONFIG.get("circuit_breaker_enabled", True) and REDIS_CLIENT is not None:
        from circuit_breaker import CircuitBreaker

        CIRCUIT_BREAKER = CircuitBreaker(
            redis_client=REDIS_CLIENT,
            error_threshold=CONFIG.get("circuit_breaker_error_threshold", 0.5),
            min_requests=CONFIG.get("circuit_breaker_min_requests", 10),
            cooldown_seconds=CONFIG.get("circuit_breaker_cooldown_seconds", 120),
        )
        LOG.info("Circuit breaker initialized")

    # Semantic grounding (Bedrock Titan v2) -> attach to output guard
    if (
        OUTPUT_GUARD is not None
        and CIRCUIT_BREAKER is not None
        and CONFIG.get("output_grounding_enabled", True)
    ):
        try:
            from rag_pipeline.bedrock_embedder import BedrockEmbedder
            from rag_pipeline.grounding_guard import GroundingGuard
            from bedrock_client import default_bedrock_client

            BEDROCK_EMBEDDER = BedrockEmbedder(
                bedrock_client=default_bedrock_client(),
                circuit_breaker=CIRCUIT_BREAKER,
            )
            GROUNDING_GUARD = GroundingGuard(embedder=BEDROCK_EMBEDDER)
            OUTPUT_GUARD.set_grounding_guard(GROUNDING_GUARD)
            LOG.info(
                "Semantic grounding (Bedrock Titan v2) attached to output guard"
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning(
                "Failed to initialize semantic grounding guard: %s (lexical fallback in effect)",
                exc,
            )

    # E1: NON-FATAL Bedrock credential preflight. Surface an expired/invalid
    # credential ONCE at boot (as a clear WARNING) instead of per-request
    # retry-spam from the embedding-vault / tier-2 paths. Runs off the event
    # loop and NEVER crashes or blocks startup.
    async def _bedrock_credential_preflight() -> None:
        try:
            from bedrock_client import default_bedrock_client

            ok = await asyncio.to_thread(
                lambda: default_bedrock_client().is_available()
            )
            if not ok:
                LOG.warning(
                    "Bedrock credential preflight FAILED — embedding-vault/tier-2 "
                    "features may be degraded: is_available() returned False"
                )
            else:
                LOG.info("Bedrock credential preflight OK")
        except Exception as exc:  # noqa: BLE001
            LOG.warning(
                "Bedrock credential preflight FAILED — embedding-vault/tier-2 "
                "features may be degraded: %s",
                exc,
            )

    try:
        asyncio.create_task(_bedrock_credential_preflight())
    except Exception:  # noqa: BLE001 — never let preflight wiring break startup
        pass

    if CONFIG.get("policy_cache_enabled", True):
        from policy_sync import PolicySync

        POLICY_SYNC = PolicySync(redis_url=CONFIG["redis_url"])
        await POLICY_SYNC.start()

    if CONFIG.get("rag_enabled", False):
        from vector_policy_sync import VectorPolicySync
        from vector_client import PineconeClient, MilvusClient
        from context_guard import ContextGuard

        VECTOR_POLICY_SYNC = VectorPolicySync(redis_url=CONFIG["redis_url"])
        await VECTOR_POLICY_SYNC.start()

        # NOTE: Chroma is now a per-org BYOK provider (the client connects their
        # own Chroma server via VectorProviderConfig connection_url), resolved on
        # demand in _resolve_vector_client / client_from_provider_config — it is
        # NO LONGER a gateway-built-in env-wired default. The old CHROMA_URL
        # startup registration was removed along with the bundled chromadb
        # docker-compose service.

        if CONFIG.get("pinecone_api_key"):
            VECTOR_CLIENTS["pinecone"] = PineconeClient(
                api_key=CONFIG["pinecone_api_key"],
                environment=CONFIG.get("pinecone_environment", ""),
                embedding_model=CONFIG.get("pinecone_embedding_model", "text-embedding-3-small"),
                thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
            )
            LOG.info("Pinecone vector client registered")

        if CONFIG.get("milvus_uri"):
            VECTOR_CLIENTS["milvus"] = MilvusClient(
                uri=CONFIG["milvus_uri"],
                token=CONFIG.get("milvus_token", ""),
                thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
            )
            LOG.info("Milvus vector client registered (uri=%s)", CONFIG["milvus_uri"])

        CONTEXT_GUARD = ContextGuard(
            thread_pool_size=CONFIG.get("scan_thread_pool_size", 4),
        )

        # ── Org-level vector provider credential sync ──
        from vector_provider_sync import VectorProviderSync
        VECTOR_PROVIDER_SYNC = VectorProviderSync(redis_url=CONFIG["redis_url"])
        await VECTOR_PROVIDER_SYNC.start()

        LOG.info(
            "RAG Firewall initialized (vector_clients=%s, context_guard=enabled, provider_sync=%d)",
            list(VECTOR_CLIENTS.keys()),
            VECTOR_PROVIDER_SYNC.provider_count,
        )

        # ── Pipeline-Aware RAG Firewall (4-stage governed pipeline) ──
        global RAG_PIPELINE
        from rag_pipeline import RAGFirewallPipeline

        _leakage_for_rag = None
        if CONFIG.get("semantic_leakage_enabled", True) and REDIS_CLIENT is not None:
            from leakage_detector import SemanticLeakageDetector
            _leakage_for_rag = SemanticLeakageDetector(redis_client=REDIS_CLIENT)
            LOG.info("Semantic leakage detector enabled")

        # ── New detection layers ──
        _llm_judge = None
        if CONFIG.get("llm_judge_enabled", True):
            from llm_judge import LLMJudge
            _bedrock_model = CONFIG.get("llm_judge_model") or os.getenv("BEDROCK_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
            _llm_judge = LLMJudge(model=_bedrock_model)
            LOG.info("LLM Judge initialized (bedrock_model=%s)", _bedrock_model)

        # Embedding Vault already initialized above (hoisted before InputScanner
        # so it can be injected as the Tier-1.6 semantic-injection check).
        # The same instance is reused here by the RAG pipeline.

        _canary_manager = None
        if CONFIG.get("canary_tokens_enabled", True):
            from canary_tokens import CanaryTokenManager
            _canary_manager = CanaryTokenManager()
            LOG.info("Canary Token Manager initialized")

        _intent_classifier = None
        if CONFIG.get("intent_classifier_enabled", True):
            from intent_classifier import IntentClassifier
            _intent_classifier = IntentClassifier()
            LOG.info("Intent Classifier initialized")

        RAG_PIPELINE = RAGFirewallPipeline(
            input_scanner=INPUT_SCANNER,
            context_guard=CONTEXT_GUARD,
            leakage_detector=_leakage_for_rag,
            vector_clients=VECTOR_CLIENTS,
            circuit_breaker=CIRCUIT_BREAKER,
            rate_limiter=RATE_LIMITER,
            telemetry=TELEMETRY,
            redis_client=REDIS_CLIENT,
            config=CONFIG,
            policy_sync=POLICY_SYNC,
            llm_judge=_llm_judge,
            embedding_vault=_embedding_vault,
            canary_token_manager=_canary_manager,
            intent_classifier=_intent_classifier,
        )
        LOG.info("RAG Firewall Pipeline initialized (stages=4, escalation_levels=3, detection_layers=%d)",
                 sum(1 for x in [_llm_judge, _embedding_vault, _canary_manager, _intent_classifier, _leakage_for_rag] if x))

    # ── Centralized logging via Redis Pub/Sub ──
    from ai_mesh_shared.redis_log_handler import RedisLogPublisher

    _redis_log_publisher = RedisLogPublisher(
        redis_url=CONFIG["redis_url"],
        service_name="Gateway",
    )
    _redis_log_publisher.setFormatter(logging.Formatter("%(message)s"))

    # Attach to parent "gateway" logger only. Children (gateway.scanner,
    # gateway.middleware, etc.) propagate=True by default, so their records
    # reach this handler without separate attachment. This fixes the
    # duplicate-record bug in the old BufferHandler setup.
    gateway_logger = logging.getLogger("gateway")
    gateway_logger.addHandler(_redis_log_publisher)
    gateway_logger.setLevel(logging.DEBUG)

    # Bedrock logger has propagate=False, so it needs its own publisher.
    bedrock_logger = logging.getLogger("bedrock")
    bedrock_logger.addHandler(RedisLogPublisher(
        redis_url=CONFIG["redis_url"],
        service_name="Bedrock",
    ))

    # Subscribe to Redis Pub/Sub and feed into local LogBuffer for SSE
    from log_buffer import LOG_BUFFER, RedisLogSubscriber

    global _log_subscriber
    _log_subscriber = RedisLogSubscriber(
        redis_url=CONFIG["redis_url"],
        buffer=LOG_BUFFER,
    )
    await _log_subscriber.start()
    LOG.info("Centralized log pipeline started (Redis Pub/Sub -> LogBuffer -> SSE)")

    # Seed the buffer so the viewer always has something on first connect
    LOG.info("ZeroShield Gateway ready \u2014 real-time log streaming active")

    # Initialize dedicated Bedrock logger (separate file + ring buffer)
    try:
        from bedrock_logger import bedrock_log as _bedrock_log, BEDROCK_LOG_RING
        LOG.info(
            "Bedrock dedicated logger initialized: log_dir=%s, level=%s, json=%s, ring_size=%d",
            os.getenv("BEDROCK_LOG_DIR", "/var/log/bedrock"),
            os.getenv("BEDROCK_LOG_LEVEL", "DEBUG"),
            os.getenv("BEDROCK_LOG_JSON", "true"),
            BEDROCK_LOG_RING._buf.maxlen,
        )
    except Exception as exc:
        LOG.warning("Bedrock dedicated logger failed to initialize: %s", exc)

    # MCP Proxy (Secure-MCP-Gateway + direct upstream + stdio adapter)
    try:
        from mcp_proxy import router as mcp_proxy_router, org_gateway_router
    except ImportError:
        from .mcp_proxy import router as mcp_proxy_router, org_gateway_router
    app.include_router(mcp_proxy_router)
    app.include_router(org_gateway_router)
    LOG.info("MCP proxy routes mounted at /v1/mcp")
    LOG.info("MCP org gateway routes mounted at /gateway/{org}/mcp/{server}")

    # MCP OAuth 2.1 (enables VS Code "Add MCP Server" auto-auth flow)
    try:
        from mcp_oauth import router as mcp_oauth_router
    except ImportError:
        from .mcp_oauth import router as mcp_oauth_router
    app.include_router(mcp_oauth_router)
    LOG.info("MCP OAuth 2.1 routes mounted (/.well-known/oauth-authorization-server, /oauth/*)")

    # MCP Upstream OAuth Proxy (per-org OAuth for external MCP servers like Linear)
    try:
        from mcp_oauth_proxy import router as mcp_oauth_proxy_router
    except ImportError:
        from .mcp_oauth_proxy import router as mcp_oauth_proxy_router
    app.include_router(mcp_oauth_proxy_router)
    LOG.info("MCP upstream OAuth proxy mounted (/gateway/{org}/mcp/{server}/oauth/*)")

    # Vector Operations API (/v1/vector/*) — see vector_routes.py for the
    # full route surface (query, upsert, delete, config). The router was
    # historically defined but never mounted, leaving the documented
    # vector data-plane unreachable; this restores it. Mounting is purely
    # additive — endpoints enforce the same per-request firewall checks
    # and SSRF/payload guards as the legacy gateway routes.
    try:
        from vector_routes import router as vector_router
    except ImportError:
        from .vector_routes import router as vector_router
    app.include_router(vector_router)
    LOG.info("Vector operations routes mounted at /v1/vector/*")

    # MCP Stdio & WebSocket adapter lifecycle
    try:
        from mcp_stdio_adapter import start_reaper as stdio_start_reaper
        from mcp_ws_adapter import start_reaper as ws_start_reaper
        stdio_start_reaper()
        ws_start_reaper()
        LOG.info("MCP stdio & WebSocket adapters initialized (reapers started)")
    except Exception as exc:
        LOG.warning("MCP adapter init failed (non-fatal): %s", exc)

    # Agent proxy excluded in AI Mesh Firewall standalone (device fleet not in SKU)

    if not CONFIG["backend_url"]:
        LOG.warning(
            "AIGUARDX_BACKEND_URL not set; gateway will not register or enforce"
        )
        return
    await asyncio.to_thread(_check_version)
    # Bounded synchronous registration: each _register() is now a single ~5s attempt, and
    # _background_register_loop() keeps retrying forever after the app is already serving.
    # So cap the inline attempts low — worst-case boot stall when control is unhealthy is
    # ~3x(5s+2s) instead of the old 5x5x30s nested-retry storm. Enforcement (Redis policy
    # cache) and app readiness never wait on control-plane registration.
    max_register_attempts = 3
    register_delay_sec = 2
    for attempt in range(1, max_register_attempts + 1):
        if await asyncio.to_thread(_register):
            break
        if attempt < max_register_attempts:
            LOG.warning("Registration failed (attempt %s/%s), retrying in %ss ...", attempt, max_register_attempts, register_delay_sec)
            await asyncio.sleep(register_delay_sec)

    if AGENT_ID:
        asyncio.create_task(_telemetry_loop())
    else:
        # Do NOT give up: keep retrying in the background so a worker that lost the
        # startup race recovers and uses the fully-featured main path (routing,
        # deep-scan, backend audit) instead of being pinned to the sync path.
        LOG.warning(
            "Gateway not registered after %s startup attempts; starting background "
            "re-registration loop (will keep retrying).", max_register_attempts,
        )
        asyncio.create_task(_background_register_loop())


@app.on_event("shutdown")
async def shutdown():
    if TELEMETRY is not None:
        await TELEMETRY.stop()
    if CONFIG_SYNC is not None:
        await CONFIG_SYNC.stop()
    if REDIS_CLIENT is not None:
        await REDIS_CLIENT.close()
    if POLICY_SYNC is not None:
        await POLICY_SYNC.stop()
    if VECTOR_POLICY_SYNC is not None:
        await VECTOR_POLICY_SYNC.stop()
    if _log_subscriber is not None:
        await _log_subscriber.stop()
    # Shut down MCP adapters
    try:
        from mcp_stdio_adapter import shutdown_all as stdio_shutdown
        await stdio_shutdown()
    except Exception:
        pass
    try:
        from mcp_ws_adapter import shutdown_all as ws_shutdown
        await ws_shutdown()
    except Exception:
        pass


def _detect_rag_request(body: dict, messages: list[dict]) -> bool:
    if body.get("rag_context") is not None:
        return True
    if body.get("documents") is not None:
        return True
    for msg in messages:
        if msg.get("role") == "system":
            content = (msg.get("content") or "").lower()
            if any(
                marker in content
                for marker in ("context:", "retrieved documents:", "knowledge base:", "search results:")
            ):
                return True
    return False


@app.post(
    "/v1/chat/completions",
    summary="Chat completions (OpenAI-compatible proxy)",
    description=(
        "Proxy endpoint for OpenAI-compatible chat completions.\n\n"
        "**Flow:**\n"
        "1. Authenticates the request via Gateway API Key\n"
        "2. Checks the model against the key's allowed_models list\n"
        "3. Runs optional security scan on the prompt\n"
        "4. Evaluates the prompt against enabled policies\n"
        "5. Forwards to the LLM provider via LiteLLM\n"
        "6. Optionally checks the response against policies\n"
        "7. Returns the LLM response (or blocks/redacts)\n\n"
        "**Streaming:** Set `stream: true` to receive Server-Sent Events (SSE). "
        "When streaming, post-response policy checks are skipped.\n\n"
        "**Headers:**\n"
        "- `Authorization: Bearer <gateway_api_key>` (required)\n"
        "- `X-User-ID: <int>` (optional, legacy)\n"
        "- `X-Endpoint-ID: <int>` (optional, legacy)\n"
        "- `X-Agent-Data: <base64_json>` (optional, agentic context)"
    ),
    response_description="OpenAI-compatible chat completion response or SSE stream",
    tags=["Chat Completions"],
    responses={
        200: {
            "description": "Successful completion (JSON or SSE stream)",
            "content": {
                "application/json": {
                    "example": {
                        "id": "chatcmpl-abc123",
                        "object": "chat.completion",
                        "created": 1700000000,
                        "model": "gpt-4o-mini",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Hello! How can I help you?",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 8,
                            "total_tokens": 18,
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid JSON body"},
        403: {
            "description": "Blocked by policy, security scan, input scanner, or model not in allowlist",
            "content": {
                "application/json": {
                    "examples": {
                        "policy_block": {
                            "value": {
                                "error": "blocked",
                                "message": "Request blocked by policy.",
                                "code": "content_blocked",
                            }
                        },
                        "model_not_allowed": {
                            "value": {
                                "error": "forbidden",
                                "message": "Model 'claude-3-opus' is not in your allowlist.",
                                "code": "model_not_allowed",
                            }
                        },
                        "input_scanner_block": {
                            "value": {
                                "error": "blocked",
                                "message": "Request blocked: prompt_injection detected.",
                                "code": "content_blocked",
                                "threat_type": "prompt_injection",
                            }
                        },
                    }
                }
            },
        },
        429: {
            "description": "Token rate limit exceeded (TPM)",
            "content": {
                "application/json": {
                    "example": {
                        "error": "rate_limited",
                        "message": "Token rate limit exceeded (95000/100000 TPM).",
                        "code": "rate_limit_exceeded",
                    }
                }
            },
        },
        502: {"description": "Upstream LLM request failed"},
        503: {
            "description": "Policy evaluation unavailable (fail-closed)",
            "content": {
                "application/json": {
                    "example": {
                        "error": "service_unavailable",
                        "message": "Policy evaluation unavailable. Request denied (fail-closed).",
                        "code": "policy_unavailable",
                    }
                }
            },
        },
    },
)
@app.post(
    "/v1/chat-completions",
    include_in_schema=False,
)
async def proxy_chat(
    request: Request,
    x_user_id: str | None = Header(None),
    x_endpoint_id: str | None = Header(None),
    x_agent_data: str | None = Header(None),
):
    """Proxy to upstream LLM after policy check. OpenAI-compatible endpoint."""
    METRICS["total_requests"] += 1
    METRICS["active_connections"] += 1
    start = time.perf_counter()
    org_slug = ""
    chat_outcome = "success"
    stage_metrics = {
        "auth_ms": 0.0,
        "policy_ms": 0.0,
        "tier1_ms": 0.0,
        "tier2_ms": 0.0,
        "upstream_ms": 0.0,
        "telemetry_enqueue_ms": 0.0,
    }

    # Set per-request telemetry context
    _REQUEST_SOURCE_IP.set(request.client.host if request.client else "")
    _REQUEST_METHOD.set(request.method)
    # P9c: establish ONE request_id for the whole request and thread it through
    # logs + the control telemetry emit. Honour an inbound correlation header,
    # else mint a fresh one. Never crash if headers are unusual.
    _rid = (
        request.headers.get("X-Request-ID")
        or request.headers.get("x-request-id")
        or f"zs-{_uuid.uuid4().hex[:12]}"
    )
    _REQUEST_ID.set(_rid)
    # D3: expose this same id on the x-request-id RESPONSE header (the OpenAI-compat
    # shim middleware reads request.state.gw_request_id after the handler returns) so
    # the SDK's response._request_id / error.request_id is populated and matches the body.
    request.state.gw_request_id = _rid

    try:
        try:
            raw_body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        # Sanitize lone UTF-8 surrogates anywhere in the parsed body BEFORE any
        # scanning, forwarding, or telemetry: a `\ud800` (valid JSON escape, not
        # UTF-8 encodable) otherwise reaches litellm serialization (502 + hang)
        # and poisons the EnforcementEvent JSON column (dropping the event).
        raw_body = _strip_lone_surrogates(raw_body)
        try:
            body = normalize_openai_chat_request(raw_body, strip_unknown_top_level=True)
        except (ValueError, TypeError) as exc:
            # Do NOT echo the raw CPython exception string to the client (it
            # leaks internals like "'int' object is not iterable"). Log the
            # detail server-side and return a fixed, generic message; the clean
            # per-field validators below produce precise messages for the common
            # malformed shapes that the normalizer now tolerates.
            LOG.warning("Chat request normalization failed: %s", exc)
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "Invalid chat completion request body.",
                    "code": "invalid_request_body",
                },
            )

        # Boundary type validation. Downstream code assumes ``body`` is a dict
        # and that ``model``/``max_tokens`` are str/int; without these guards a
        # non-object body, ``{"model": 123}`` or ``{"max_tokens": "abc"}``
        # reaches code like ``_is_routing_sentinel_model(model).strip()`` or
        # ``int(body.get("max_tokens"))`` and raises an unhandled 500.
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "Request body must be a JSON object.",
                    "code": "invalid_request_body",
                },
            )
        _model_val = body.get("model")
        if _model_val is not None and not isinstance(_model_val, str):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "'model' must be a string.",
                    "param": "model",
                    "code": "invalid_model",
                },
            )
        _max_tokens_val = body.get("max_tokens")
        if _max_tokens_val is None:
            # Explicit JSON ``null`` is treated identically to absent. Leaving an
            # explicit None in the body makes the downstream
            # ``min(body.get("max_tokens", default), default)`` do ``min(None, int)``
            # which raises TypeError -> unhandled 500. Drop the key.
            body.pop("max_tokens", None)
        else:
            # C5: a non-integral float (e.g. 1.5) was silently truncated by
            # int() (1.5 -> 1) instead of being rejected as documented. Catch it
            # before coercion. A whole-valued float (100.0) and integer string
            # ("100") are still accepted.
            if isinstance(_max_tokens_val, float) and not _max_tokens_val.is_integer():
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": "'max_tokens' must be an integer.",
                        "param": "max_tokens",
                        "code": "invalid_max_tokens",
                    },
                )
            try:
                _max_tokens_int = int(_max_tokens_val)
            except (TypeError, ValueError, OverflowError):
                # OverflowError: Python's json.loads accepts the non-standard
                # literals ``Infinity`` / ``1e999`` -> float('inf'); ``int(inf)``
                # raises OverflowError (NOT ValueError), so it must be in the
                # tuple or it escapes as an unhandled 500.
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": "'max_tokens' must be an integer.",
                        "param": "max_tokens",
                        "code": "invalid_max_tokens",
                    },
                )
            if _max_tokens_int < 0:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": "'max_tokens' must be a positive integer.",
                        "param": "max_tokens",
                        "code": "invalid_max_tokens",
                    },
                )
            if _max_tokens_int == 0:
                # max_tokens == 0 is the ZeroShield "scan-only / no-inference" probe
                # sentinel: the rest of the gateway already treats it that way
                # (needs_inference = max_tokens > 0). Rejecting 0 here as "invalid"
                # CONTRADICTED that and broke every input-scan probe — the simulator
                # (and any client that sends 0 to run guardrails WITHOUT paying for a
                # completion) got a 400 "'max_tokens' must be a positive integer"
                # instead of an input-scan verdict. Treat it like an absent
                # max_tokens: drop the key so the request runs input scan + policy
                # with no inference. (A negative value is still rejected above.)
                body.pop("max_tokens", None)
            # C5: cap an absurd upper bound. A huge int was previously forwarded
            # upstream unbounded (provider-side error / quota burn). 1,000,000 is
            # a generous absolute ceiling well above any real output budget.
            elif _max_tokens_int > MAX_OUTPUT_TOKENS_CEILING:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": f"'max_tokens' must not exceed {MAX_OUTPUT_TOKENS_CEILING}.",
                        "param": "max_tokens",
                        "code": "invalid_max_tokens",
                    },
                )
            else:
                # Normalize to the coerced int so the later min()/comparison is
                # type-safe regardless of whether the client sent "100" / 100.0 / etc.
                body["max_tokens"] = _max_tokens_int

        # SEAM-A: ``max_completion_tokens`` (the OpenAI replacement for ``max_tokens``
        # on reasoning models o1/o3/gpt-5) was NEVER inspected here, so a client could
        # smuggle a negative or absurd-over-ceiling completion budget straight to the
        # provider while the same value on ``max_tokens`` was rejected. Apply the SAME
        # ceiling + sign + integral rules so the two fields are symmetric.
        _mct_val = body.get("max_completion_tokens")
        if _mct_val is None:
            # Explicit JSON ``null`` == absent: drop so downstream never does
            # ``min(None, int)`` / ``int(None)``.
            body.pop("max_completion_tokens", None)
        else:
            if isinstance(_mct_val, float) and not _mct_val.is_integer():
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": "'max_completion_tokens' must be an integer.",
                        "param": "max_completion_tokens",
                        "code": "invalid_max_completion_tokens",
                    },
                )
            try:
                _mct_int = int(_mct_val)
            except (TypeError, ValueError, OverflowError):
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": "'max_completion_tokens' must be an integer.",
                        "param": "max_completion_tokens",
                        "code": "invalid_max_completion_tokens",
                    },
                )
            if _mct_int < 0:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": "'max_completion_tokens' must be a positive integer.",
                        "param": "max_completion_tokens",
                        "code": "invalid_max_completion_tokens",
                    },
                )
            if _mct_int == 0:
                # Parity with max_tokens==0: treat a zero completion budget as the
                # scan-only sentinel and drop the key rather than forwarding 0.
                body.pop("max_completion_tokens", None)
            elif _mct_int > MAX_OUTPUT_TOKENS_CEILING:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": f"'max_completion_tokens' must not exceed {MAX_OUTPUT_TOKENS_CEILING}.",
                        "param": "max_completion_tokens",
                        "code": "invalid_max_completion_tokens",
                    },
                )
            else:
                body["max_completion_tokens"] = _mct_int

        messages_raw = body.get("messages")
        if messages_raw is not None and not isinstance(messages_raw, list):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "'messages' must be an array.",
                    "param": "messages",
                    "code": "invalid_messages",
                },
            )
        # R#1: a missing ``messages`` array (after the normalizer's input->messages
        # fallback) is just as unusable as an empty one — reject at the boundary
        # rather than letting it hang the upstream fallback chain (~52s).
        if messages_raw is None:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "'messages' must contain at least one message with content.",
                    "param": "messages",
                    "code": "invalid_messages",
                },
            )
        # input-val#6: cap the message COUNT (the per-message text length is
        # capped elsewhere, but an unbounded messages array is its own DoS /
        # scanner-amplification vector).
        if isinstance(messages_raw, list) and len(messages_raw) > MAX_MESSAGES:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "too_many_messages",
                    "message": "messages array exceeds the maximum of 200 entries.",
                    "code": "too_many_messages",
                },
            )
        # G65: cap the tools array — each entry's free text is folded into the scan AND
        # recursively masked on a redact verdict (~5s CPU for 100k tools), a DoS amplification
        # the per-prompt length cap does not bound (it caps the scanned string, not the
        # per-tool redaction recursion). Reject an over-limit array with 400 up front.
        _tools_arr = body.get("tools")
        if isinstance(_tools_arr, list) and len(_tools_arr) > MAX_TOOLS:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "too_many_tools",
                    "message": f"'tools' array exceeds the maximum of {MAX_TOOLS} entries.",
                    "param": "tools",
                    "code": "too_many_tools",
                },
            )
        if isinstance(messages_raw, list):
            for idx, msg in enumerate(messages_raw):
                if not isinstance(msg, dict):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "invalid_request",
                            "message": f"messages[{idx}] must be an object.",
                            "param": f"messages[{idx}]",
                            "code": "invalid_messages",
                        },
                    )
                # ``content`` must be a string (normal) or a list (multimodal
                # content-parts); ``None`` is valid for assistant/tool messages.
                # A non-string/non-list content (e.g. a dict or int) otherwise
                # flows downstream into ``prompt.lower()`` (-> AttributeError 500)
                # and to the upstream provider (-> hang). Reject at the boundary.
                _content = msg.get("content")
                if _content is not None and not isinstance(_content, (str, list)):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "error": "invalid_request",
                            "message": f"messages[{idx}].content must be a string or an array.",
                            "param": f"messages[{idx}].content",
                            "code": "invalid_messages",
                        },
                    )
                # Multimodal content-PART shape validation. A list content carries
                # parts like {"type":"text","text":...} or {"type":"image_url",
                # "image_url":{...}}. Without per-part validation a malformed part
                # (e.g. type=image_url with the image_url key missing, or a
                # non-string text) passes the boundary and crashes LiteLLM's
                # gpt_transformation with KeyError -> unhandled HTTP 500 (must be
                # a 400 per the input-validation contract).
                if isinstance(_content, list):
                    for pidx, part in enumerate(_content):
                        _bad = None
                        if not isinstance(part, dict):
                            _bad = "must be an object"
                        else:
                            _ptype = part.get("type")
                            if not isinstance(_ptype, str) or not _ptype:
                                _bad = "missing a string 'type'"
                            # C2: ALLOWLIST the content-part type. Previously ANY
                            # non-empty string type passed (e.g. {"type":"bogus"}),
                            # slipping through to LiteLLM/upstream which rejected it
                            # AFTER a retry + cross-model fallback walk — a slow 502
                            # and DoS amplification (one tiny request burned ~8-34s).
                            # Reject unknown part types fast at the boundary (400).
                            elif _ptype not in _ALLOWED_CONTENT_PART_TYPES:
                                _bad = f"unsupported content part type '{_ptype}'"
                            elif _ptype == "text" and not isinstance(part.get("text"), str):
                                _bad = "type=text requires a string 'text'"
                            elif _ptype == "image_url":
                                _iu = part.get("image_url")
                                # C2: an empty dict {} previously satisfied the
                                # isinstance(...,dict) check and reached upstream.
                                # Require a non-empty url (string form, or a dict
                                # carrying a non-empty string "url").
                                if isinstance(_iu, str):
                                    if not _iu.strip():
                                        _bad = "type=image_url requires a non-empty 'image_url'"
                                elif isinstance(_iu, dict):
                                    if not isinstance(_iu.get("url"), str) or not _iu.get("url").strip():
                                        _bad = "type=image_url requires 'image_url.url' (non-empty string)"
                                else:
                                    _bad = "type=image_url requires an 'image_url' (string or object)"
                        if _bad:
                            return JSONResponse(
                                status_code=400,
                                content={
                                    "error": "invalid_request",
                                    "message": f"messages[{idx}].content[{pidx}] {_bad}.",
                                    "param": f"messages[{idx}].content[{pidx}]",
                                    "code": "invalid_messages",
                                },
                            )

            # R#1: an empty messages array (or one whose messages all carry
            # no usable content) is NOT a valid completion request. Without this
            # guard it falls through to LLM_ROUTER.acompletion and the upstream
            # fallback chain hangs (~52s) before erroring. Reject at the boundary
            # with a clean 400. Tool/assistant flows are preserved: a request
            # that carries tool context (top-level ``tools``, a message with
            # ``tool_calls``, or a ``tool``/``function`` role message) is allowed
            # through even with empty content, so only the genuinely empty
            # (no-content, no-tool-context) request is rejected.
            def _msg_has_content(_m: dict) -> bool:
                if not isinstance(_m, dict):
                    return False
                _c = _m.get("content")
                if isinstance(_c, str):
                    return bool(_c.strip())
                if isinstance(_c, list):
                    return len(_c) > 0
                return False

            def _has_tool_context() -> bool:
                if body.get("tools"):
                    return True
                for _m in messages_raw:
                    if not isinstance(_m, dict):
                        continue
                    if _m.get("tool_calls"):
                        return True
                    if str(_m.get("role") or "").strip().lower() in ("tool", "function"):
                        return True
                return False

            if not messages_raw or (
                not any(_msg_has_content(_m) for _m in messages_raw)
                and not _has_tool_context()
            ):
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "invalid_request",
                        "message": "'messages' must contain at least one message with content.",
                        "param": "messages",
                        "code": "invalid_messages",
                    },
                )

        # R#2: the output guard is single-choice by design — it only inspects and
        # rewrites ``choices[0]`` (_extract_response_from_completion /
        # _set_completion_response_text). With ``n>1`` the upstream returns extra
        # choices[1..] that would be shipped to the client UNSCANNED and
        # UN-REDACTED (PII / credential bypass). Clamp ``n`` to 1 at the boundary
        # so every returned choice passes through the guard. ``n`` is a known
        # OpenAI passthrough field, so it survives normalization into ``body``.
        _n_val = body.get("n")
        if _n_val is not None:
            try:
                _n_int = int(_n_val)
            except (TypeError, ValueError, OverflowError):
                # C3: json.loads accepts ``Infinity`` / ``1e999`` -> float('inf');
                # ``int(inf)`` raises OverflowError (NOT ValueError). Without it in
                # the tuple the error escaped to an unhandled 500 (valid-key,
                # payload-shaped DoS). Any un-coercible / non-finite ``n`` clamps
                # to the safe 1.
                _n_int = 1
            # C3: ALWAYS write back the coerced int. Previously this only ran
            # ``if _n_int != 1`` — so a non-finite ``n`` (whose except-branch set
            # _n_int=1) left the RAW float('inf') in body["n"], which then crashed
            # a SECOND int(body["n"]) in _extract_chat_routing_preferences. The
            # output guard is single-choice, so n is always forced to 1 anyway.
            body["n"] = 1 if _n_int != 1 else _n_int

        # C-5: locally reject non-finite float sampling params (temperature/top_p/
        # frequency_penalty/presence_penalty). Without this a NaN/Inf value was
        # forwarded to LiteLLM, failed upstream, and retried twice before bouncing
        # as a 400 — wasting an upstream round-trip + quota and flooding logs with
        # tracebacks. Parity with the local max_tokens/n guards above.
        import math as _math
        for _p in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
            _pv = body.get(_p)
            if _pv is None:
                continue
            try:
                _pf = float(_pv)
            except (TypeError, ValueError):
                return JSONResponse(
                    status_code=400,
                    content={"error": "invalid_request",
                             "message": f"'{_p}' must be a finite number.",
                             "param": _p,
                             "code": "invalid_sampling_param"},
                )
            if not _math.isfinite(_pf):
                return JSONResponse(
                    status_code=400,
                    content={"error": "invalid_request",
                             "message": f"'{_p}' must be a finite number (not NaN/Infinity).",
                             "param": _p,
                             "code": "invalid_sampling_param"},
                )

        # Preserve routing controls that are not part of strict OpenAI payload schema.
        if isinstance(raw_body, dict):
            raw_routing_preferences = raw_body.get("routing_preferences")
            if isinstance(raw_routing_preferences, dict):
                body["routing_preferences"] = raw_routing_preferences

            raw_enable_routing = raw_body.get("enable_routing")
            if isinstance(raw_enable_routing, bool):
                body["enable_routing"] = raw_enable_routing

            raw_metadata = raw_body.get("metadata")
            if isinstance(raw_metadata, dict):
                metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
                if isinstance(raw_metadata.get("enable_routing"), bool):
                    metadata["enable_routing"] = raw_metadata["enable_routing"]
                if raw_metadata.get("data_sensitivity"):
                    metadata["data_sensitivity"] = raw_metadata["data_sensitivity"]
                raw_meta_compliance = raw_metadata.get("compliance_requirements")
                if raw_meta_compliance is not None:
                    metadata["compliance_requirements"] = raw_meta_compliance
                if metadata:
                    body["metadata"] = metadata

            # Isolation / routing controls stripped by OpenAI normalizer — restore for gateway only.
            if raw_body.get("data_sensitivity"):
                body["data_sensitivity"] = raw_body["data_sensitivity"]
            raw_compliance = raw_body.get("compliance_requirements")
            if raw_compliance is not None:
                body["compliance_requirements"] = raw_compliance

            # Agent-context payload (body.agent_data) is stripped by the strict
            # OpenAI normalizer; restore it so _extract_agent_data folds it into
            # the input scan (parity with the X-Agent-Data header). Scan-only —
            # agent_data is not in llm_router passthrough, so it never reaches the
            # upstream model; without this restore a body-level injection bypasses
            # ALL input scanning.
            raw_agent_data = raw_body.get("agent_data")
            if isinstance(raw_agent_data, (dict, str)):
                body["agent_data"] = raw_agent_data

        # ── Identity: prefer auth_context from AuthMiddleware, fall back to legacy headers ──
        auth_ctx = getattr(request.state, "auth_context", None)
        stage_metrics["auth_ms"] = round((time.perf_counter() - start) * 1000, 2)

        route_selection = None
        route_metadata = None

        # ── Org-aware config lookup ──
        org_slug = auth_ctx.org_slug if auth_ctx else ""
        # H7: carry the org on the body so LLMRouter._build_kwargs can org-qualify
        # the litellm routing key ({org}::{model}) and select THIS org's deployment
        # + BYOK key — never a same-named peer from another tenant. Inert otherwise
        # (not in litellm passthrough params). Flows to the stream + rewrite bodies.
        body["_zs_org_slug"] = org_slug
        org_config = CONFIG_SYNC.get_config(org_slug) if CONFIG_SYNC else CONFIG
        routing_models = CONFIG_SYNC.get_model_routing(org_slug) if CONFIG_SYNC else []
        if not routing_models and CONFIG_SYNC is not None:
            await CONFIG_SYNC.reload_models_now(org_slug=org_slug)
            routing_models = CONFIG_SYNC.get_model_routing(org_slug)
        inference_models = _filter_inference_eligible_models(routing_models)
        scan_verdict = None
        routing_prefs = _extract_chat_routing_preferences(body, org_config, auth_ctx, scan_verdict)
        routing_active = bool(
            inference_models and LLM_ROUTER is not None and routing_prefs["routing_enabled"]
        )

        needs_inference = bool(body.get("stream")) or int(body.get("max_tokens") or 0) > 0
        # #27: a provider-less org should still get the INPUT scan run (so the
        # Attack Simulator's single-run mode demonstrates BLOCKED instead of
        # short-circuiting to "Connect a model"). Defer the no-provider 422
        # until AFTER input scanning when scanning will actually run — the
        # post-scan _validate_org_inference_model gate (same auth_ctx +
        # needs_inference condition) re-emits the identical 422 for clean
        # prompts. When scanning is off (firewall disabled / no scanner /
        # input_scan disabled) there is nothing to gain, so gate early as before.
        _input_scan_will_run = bool(
            INPUT_SCANNER is not None
            and org_config.get("firewall_enabled") is not False
            and org_config.get("input_scan_enabled", True)
        )
        if auth_ctx is not None and not inference_models and needs_inference and not _input_scan_will_run:
            return _build_no_inference_provider_response()

        routing_identities = _routing_identity_set(inference_models)
        isolation_reroute_locked = False
        isolation_reroute_audit = None

        # Set org context for telemetry enrichment
        _REQUEST_ORG_ID.set(auth_ctx.organization_id if auth_ctx else None)
        _REQUEST_ORG_SLUG.set((getattr(auth_ctx, "org_slug", "") or "") if auth_ctx else "")

        # ── Master firewall toggle ──
        firewall_disabled = org_config.get("firewall_enabled") is False
        enforcement_mode = org_config.get("enforcement_mode", "block")
        tier2_execution_mode = str(org_config.get("tier2_execution_mode", "sync_pre_llm")).strip().lower()
        global_allowed_models = _coerce_string_list(org_config.get("allowed_models", []))
        if auth_ctx is not None:
            user_id = auth_ctx.user_id
            project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace
            risk_score = auth_ctx.risk_score
            allowed_models = auth_ctx.allowed_models
            rate_limit_tpm = auth_ctx.rate_limit_tpm

            # Model allowlist enforcement (before any downstream calls)
            requested_model = body.get("model", "")
            # Echo only a sanitized form of the client model in any
            # client/telemetry-facing string so a malicious model name can't
            # reflect into charts / threat-feed (R4).
            _safe_req = _safe_model_echo(requested_model)
            # P6-missing-model: an absent/empty ``model`` with routing OFF is a
            # MALFORMED request, not a forbidden one — return the OpenAI 400
            # invalid_request_error (param='model') the stock SDK raises as
            # BadRequestError, NOT the 403 allowlist block below (which leaks
            # "Model '' is not in your allowlist"). Gate on ``not routing_active``
            # (same as the 403) so auto-routing — which legitimately omits the
            # model — is untouched.
            if not str(requested_model or "").strip() and not routing_active:
                from responses_adapters import build_openai_error as _boe_missing_model
                return JSONResponse(
                    status_code=400,
                    content=_boe_missing_model(
                        400,
                        "Missing required parameter: 'model'.",
                        error_type="invalid_request_error",
                        code="missing_required_parameter",
                        param="model",
                    ),
                )
            if allowed_models and requested_model not in allowed_models and not routing_active:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=403,
                    event_type="input_blocked",
                    model=requested_model,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=0.50,
                    threat_type="model_not_allowed",
                    metadata={"detail": f"Model '{_safe_req}' not in key allowlist"},
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Model '{_safe_req}' is not in your allowlist.",
                        "code": "model_not_allowed",
                    },
                )
            if requested_model and routing_identities and requested_model not in routing_identities and not routing_active:
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": "model_not_configured",
                        "message": f"Model '{_safe_req}' is not configured for external inference in this organization.",
                        "code": "model_not_configured",
                    },
                )
            endpoint_id = None  # Not used in GatewayAPIKey auth path
        else:
            # Legacy fallback (X-User-ID / X-Endpoint-ID headers)
            user_id = int(x_user_id) if x_user_id and x_user_id.isdigit() else None
            endpoint_id = (
                int(x_endpoint_id) if x_endpoint_id and x_endpoint_id.isdigit() else None
            )
            project_id = None
            risk_score = None
            allowed_models = None
            rate_limit_tpm = None
            requested_model = body.get("model", "")

        key_allowed_models = _coerce_string_list(allowed_models or [])
        routing_allowed_models = key_allowed_models or []
        if global_allowed_models:
            routing_allowed_models = [
                model for model in (routing_allowed_models or global_allowed_models) if model in global_allowed_models
            ]
        if not routing_allowed_models:
            routing_allowed_models = global_allowed_models or key_allowed_models

        if _is_routing_sentinel_model(requested_model) and inference_models:
            requested_model = _resolve_routing_hint_model(
                requested_model, org_config, inference_models
            )
            if requested_model and not _is_routing_sentinel_model(requested_model):
                body["model"] = requested_model

        # ── Reserved platform/guard model guard (enforced REGARDLESS of routing) ──
        # zeroshield-guard-120b (and Bedrock foundation IDs) are reserved for the
        # platform's internal ML — Tier-2 scanning / adjudication — and must NEVER
        # be served as an org inference target. The global-isolation check below is
        # gated on `not routing_active`, so with routing on a request for the guard
        # model was silently routed to the org default (served) or reached the final
        # allowlist check and leaked the internal name in the 403. Reject up front
        # with a generic message that neither serves nor names the internal model.
        try:
            from platform_models import is_platform_model_name as _is_platform_model
        except ImportError:
            from .platform_models import is_platform_model_name as _is_platform_model
        if requested_model and not _is_routing_sentinel_model(requested_model) and _is_platform_model(requested_model):
            METRICS["blocked"] += 1
            _emit_telemetry(
                status_code=403,
                event_type="input_blocked",
                model=requested_model,
                user_id=user_id,
                project_id=str(project_id or ""),
                key_prefix=auth_ctx.prefix if auth_ctx else "",
                action="block",
                risk_score=0.5,
                threat_type="model_not_allowed",
                metadata={"reason": "platform_model_not_inferable", "requested_model": requested_model},
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "The requested model is not available for inference.",
                    "code": "model_not_allowed",
                },
            )

        # ── Global model isolation (from firewall config) ──
        if not firewall_disabled and org_config.get("model_isolation_enabled", False):
            global_allowed = global_allowed_models
            if (
                global_allowed
                and requested_model
                and not _is_routing_sentinel_model(requested_model)
                and requested_model not in global_allowed
                and not routing_active
            ):
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    # Echo only a sanitized client model (R4).
                    _safe_global = _safe_model_echo(requested_model)
                    _emit_telemetry(
                        status_code=403,
                        event_type="input_blocked",
                        model=requested_model,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.50,
                        threat_type="model_not_allowed",
                        metadata={"detail": f"Model '{_safe_global}' not in global allowlist"},
                    )
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "forbidden",
                            "message": f"Model '{_safe_global}' is not in the global allowlist.",
                            "code": "model_not_allowed",
                        },
                    )
                else:
                    LOG.warning(
                        "MONITOR: model '%s' not in global allowlist (user=%s)",
                        requested_model, user_id,
                    )

        # ── Threat intelligence check (risk_score from auth context) ──
        from ai_mesh_gateway.playground_auth import should_skip_threat_intel

        _skip_threat_intel = should_skip_threat_intel(auth_ctx)
        if _skip_threat_intel:
            LOG.debug("threat_intel skipped for live-test gateway key")

        if (
            not firewall_disabled
            and not _skip_threat_intel
            and org_config.get("threat_intel_enabled", True)
        ):
            ctx_risk_score = getattr(auth_ctx, "risk_score", None) if auth_ctx else None
            if ctx_risk_score is not None:
                threat_threshold = org_config.get("threat_score_threshold", 75) / 100.0
                if ctx_risk_score >= threat_threshold and org_config.get("auto_block_threats", True):
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        _emit_telemetry(
                            status_code=403,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            threat_type="high_risk_actor",
                            risk_score=ctx_risk_score,
                            metadata={"detail": f"Risk score {ctx_risk_score:.2f} exceeds threshold {threat_threshold:.2f}"},
                        )
                        return _build_block_response(403, "threat_intel_blocked", _build_zeroshield_metadata(
                            action="block",
                            reason=f"Blocked by threat intelligence (risk_score={ctx_risk_score:.2f}, threshold={threat_threshold:.2f}).",
                            detection_tier="threat_intel",
                            threat_type="high_risk_actor",
                            confidence=ctx_risk_score,
                            original_prompt=body.get("messages", [{}])[-1].get("content", "") if body.get("messages") else "",
                            processing_time_ms=elapsed_ms,
                        ))
                    else:
                        LOG.warning(
                            "MONITOR: threat_intel would block user=%s risk_score=%.2f (threshold=%.2f)",
                            user_id, ctx_risk_score, threat_threshold,
                        )

        # ── Kill-switch check (Redis, ~0.1ms) ──
        if org_config.get("kill_switch_enabled", True):
            if REDIS_CLIENT is None:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=503,
                    event_type="kill_switch",
                    model=requested_model,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=0.60,
                    threat_type="kill_switch",
                    metadata={
                        "reason": "Kill-switch Redis unavailable — fail-closed",
                        "trigger_source": "kill_switch",
                        "kill_switch_truth_mode": "per_request_redis",
                    },
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": "Kill-switch enforcement unavailable.",
                        "code": "kill_switch_active",
                    },
                )
            from kill_switch import check_kill_switch
            try:
                from .metrics import record_kill_switch as _prom_ks
            except ImportError:
                from metrics import record_kill_switch as _prom_ks  # type: ignore[no-redef]

            ks_key_prefix = auth_ctx.prefix if auth_ctx else ""
            ks_verdict = await check_kill_switch(
                REDIS_CLIENT,
                requested_model,
                org_slug=org_slug,
                key_prefix=ks_key_prefix,
            )
            if ks_verdict.is_killed:
                _prom_ks(requested_model, ks_verdict.action or "block")
                ks_original = requested_model
                # R#7: bound reroute candidates to CHAT-capable models so a
                # kill-switch reroute can never land on an embedding-only (or
                # guard/internal) model that cannot serve a scannable completion.
                iso_ctx = _isolation_reroute_context(
                    org_slug, _chat_capable_models(routing_models), routing_prefs, allowed_models, body=body
                )
                if ks_verdict.action == "reroute":
                    compliant_model, ks_audit = _apply_compliant_isolation_reroute(
                        primary_model=ks_original,
                        requested_fallback=ks_verdict.fallback_model,
                        ctx=iso_ctx,
                        scope=ks_verdict.scope or "org_model",
                        trigger_source="kill_switch",
                        reason=ks_verdict.reason,
                    )
                    if compliant_model:
                        # B7: the reroute resolver derives is_active from the static
                        # synced catalog and never consults the live kill_switch:
                        # Redis namespace, so it can select a target that is ITSELF
                        # operator-disabled by a kill-switch. Re-check the chosen
                        # target; if it too is killed, fail closed rather than
                        # serving an operator-disabled model.
                        _tgt_ks = await check_kill_switch(
                            REDIS_CLIENT, compliant_model, org_slug=org_slug, key_prefix=ks_key_prefix,
                        )
                        # G6: also re-check the live MODEL-STATE namespace on the
                        # target (symmetric with the model-state branch) — a
                        # kill-switch reroute must not land on a model-state-isolated
                        # model either.
                        try:
                            from model_state import check_model_state as _cms_g6
                        except ImportError:
                            from .model_state import check_model_state as _cms_g6
                        _tgt_ms = await _cms_g6(REDIS_CLIENT, compliant_model, org_slug=org_slug or "default")
                        if getattr(_tgt_ks, "is_killed", False) or getattr(_tgt_ms, "status", "") in ("isolated", "suspended"):
                            METRICS["blocked"] += 1
                            return JSONResponse(
                                status_code=503,
                                content={
                                    "error": "service_unavailable",
                                    "message": "All eligible models are currently disabled by an operator kill-switch.",
                                    "code": "kill_switch_active",
                                    "zeroshield": {"routing": route_metadata or {}},
                                },
                            )
                        LOG.warning(
                            "Kill-switch reroute: %s -> %s (reason: %s)",
                            ks_original, compliant_model, ks_verdict.reason,
                        )
                        body["model"] = compliant_model
                        requested_model = compliant_model
                        isolation_reroute_locked = True
                        isolation_reroute_audit = ks_audit
                        ks_audit["kill_switch_action"] = "reroute"
                        _emit_telemetry(
                            event_type="kill_switch",
                            model=requested_model,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=ks_key_prefix,
                            action="reroute",
                            risk_score=0.60,
                            threat_type="kill_switch",
                            metadata=ks_audit,
                        )
                    else:
                        block_reason = (
                            f"{ks_verdict.reason}; no compliant fallback "
                            f"({ks_audit.get('fallback_reason_code', 'ineligible')})"
                        )
                        METRICS["blocked"] += 1
                        ks_audit["kill_switch_action"] = "block"
                        _emit_telemetry(
                            status_code=503,
                            event_type="kill_switch",
                            model=ks_original,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=ks_key_prefix,
                            action="block",
                            risk_score=0.60,
                            threat_type="kill_switch",
                            metadata={**ks_audit, "reason": block_reason},
                        )
                        return JSONResponse(
                            status_code=503,
                            content={
                                "error": "service_unavailable",
                                "message": f"Model '{ks_original}' is disabled (no compliant fallback).",
                                "code": "kill_switch_active",
                            },
                        )
                if ks_verdict.is_killed and ks_verdict.action != "reroute":
                    METRICS["blocked"] += 1
                    _emit_telemetry(
                        status_code=503,
                        event_type="kill_switch",
                        model=requested_model,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.60,
                        threat_type="kill_switch",
                        metadata={
                            "reason": ks_verdict.reason,
                            "original_model": ks_original,
                            "isolation_scope": ks_verdict.scope,
                            "trigger_source": "kill_switch",
                            "kill_switch_truth_mode": "per_request_redis",
                        },
                    )
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "service_unavailable",
                            "message": f"Model '{ks_original}' is currently disabled.",
                            "code": "kill_switch_active",
                        },
                    )

        # ── Model state check (Redis, org-scoped, ~0.1ms) ──
        if REDIS_CLIENT is not None:
            from model_state import check_model_state

            ms_verdict = await check_model_state(REDIS_CLIENT, requested_model, org_slug=org_slug or "default")
            ms_original = requested_model
            # R#7: same chat-capability bound for model-state reroutes.
            iso_ctx = _isolation_reroute_context(
                org_slug, _chat_capable_models(routing_models), routing_prefs, allowed_models, body=body
            )

            if ms_verdict.status == "suspended":
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=503,
                    event_type="model_isolation",
                    model=ms_original,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=1.0,
                    threat_type="model_state_unavailable",
                    metadata={
                        "reason": ms_verdict.reason,
                        "original_model": ms_original,
                        "status": "suspended",
                        "trigger_source": "model_state",
                        "kill_switch_truth_mode": "per_request_redis",
                    },
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": f"Model state unavailable for '{ms_original}'.",
                        "code": "model_state_unavailable",
                        "reason": ms_verdict.reason,
                    },
                )

            if ms_verdict.status == "isolated":
                if ms_verdict.action == "reroute":
                    compliant_model, ms_audit = _apply_compliant_isolation_reroute(
                        primary_model=ms_original,
                        requested_fallback=ms_verdict.fallback_model,
                        ctx=iso_ctx,
                        scope="org_model",
                        trigger_source="model_state",
                        reason=ms_verdict.reason,
                    )
                    if compliant_model:
                        # G6: mirror the B7 re-check on the MODEL-STATE reroute target
                        # (B7 patched only the kill-switch branch — this is its
                        # untouched sibling). resolve_compliant_fallback derives
                        # is_active from the STATIC synced catalog and is blind to
                        # BOTH live Redis override namespaces (kill_switch:* and
                        # model_state:*), so a model-state reroute could land on a
                        # model that is itself kill-switched OR model-state-isolated
                        # for the org. Re-check both; fail closed if the target is
                        # operator-disabled rather than serving it.
                        try:
                            from kill_switch import check_kill_switch as _ck_g6
                        except ImportError:
                            from .kill_switch import check_kill_switch as _ck_g6
                        _tgt_ks = await _ck_g6(
                            REDIS_CLIENT, compliant_model,
                            org_slug=org_slug, key_prefix=auth_ctx.prefix if auth_ctx else "",
                        )
                        _tgt_ms = await check_model_state(
                            REDIS_CLIENT, compliant_model, org_slug=org_slug or "default",
                        )
                        if getattr(_tgt_ks, "is_killed", False) or getattr(_tgt_ms, "status", "") in ("isolated", "suspended"):
                            METRICS["blocked"] += 1
                            return JSONResponse(
                                status_code=503,
                                content={
                                    "error": "service_unavailable",
                                    "message": "All eligible models are currently disabled by an operator control.",
                                    "code": "model_unavailable",
                                    "zeroshield": {"routing": route_metadata or {}},
                                },
                            )
                        LOG.warning(
                            "Model-state reroute: %s -> %s (reason: %s)",
                            ms_original, compliant_model, ms_verdict.reason,
                        )
                        body["model"] = compliant_model
                        requested_model = compliant_model
                        isolation_reroute_locked = True
                        isolation_reroute_audit = ms_audit
                        ms_audit["risk_score"] = ms_verdict.risk_score
                        ms_audit["threshold"] = ms_verdict.threshold
                        _emit_telemetry(
                            event_type="model_isolation",
                            model=requested_model,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="reroute",
                            risk_score=ms_verdict.risk_score / 100.0,
                            threat_type="model_isolated",
                            metadata=ms_audit,
                        )
                    else:
                        block_reason = (
                            f"{ms_verdict.reason}; no compliant fallback "
                            f"({ms_audit.get('fallback_reason_code', 'ineligible')})"
                        )
                        METRICS["blocked"] += 1
                        ms_audit["risk_score"] = ms_verdict.risk_score
                        ms_audit["threshold"] = ms_verdict.threshold
                        _emit_telemetry(
                            status_code=503,
                            event_type="model_isolation",
                            model=ms_original,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=ms_verdict.risk_score / 100.0,
                            threat_type="model_isolated",
                            metadata={**ms_audit, "reason": block_reason},
                        )
                        return JSONResponse(
                            status_code=503,
                            content={
                                "error": "service_unavailable",
                                "message": f"Model '{ms_original}' is isolated (no compliant fallback).",
                                "code": "model_isolated",
                                "reason": block_reason,
                            },
                        )
                if ms_verdict.status == "isolated" and ms_verdict.action == "alert":
                    LOG.warning(
                        "Model '%s' isolated with alert-only (risk=%.1f, reason=%s)",
                        requested_model, ms_verdict.risk_score, ms_verdict.reason,
                    )
                    _emit_telemetry(
                        event_type="model_isolation",
                        model=requested_model,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="alert",
                        risk_score=ms_verdict.risk_score / 100.0,
                        threat_type="model_isolated",
                        metadata={
                            "reason": ms_verdict.reason,
                            "risk_score": ms_verdict.risk_score,
                            "original_model": ms_original,
                            "trigger_source": "model_state",
                        },
                    )
                elif ms_verdict.status == "isolated" and ms_verdict.action != "reroute":
                    # NOTE: when action == "reroute", the reroute branch above has
                    # already either applied a compliant fallback (and we must fall
                    # through to serve it) or returned 503 on no-fallback. Without
                    # this guard a SUCCESSFUL reroute fell through here and 503'd —
                    # the fallback was resolved but the request still failed.
                    METRICS["blocked"] += 1
                    _emit_telemetry(
                        status_code=503,
                        event_type="model_isolation",
                        model=ms_original,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=ms_verdict.risk_score / 100.0,
                        threat_type="model_isolated",
                        metadata={
                            "reason": ms_verdict.reason,
                            "risk_score": ms_verdict.risk_score,
                            "threshold": ms_verdict.threshold,
                            "original_model": ms_original,
                            "trigger_source": "model_state",
                        },
                    )
                    return JSONResponse(
                        status_code=503,
                        content={
                            "error": "service_unavailable",
                            "message": f"Model '{ms_original}' is currently isolated (risk score: {ms_verdict.risk_score:.1f}).",
                            "code": "model_isolated",
                            "reason": ms_verdict.reason,
                            "risk_score": ms_verdict.risk_score,
                            "threshold": ms_verdict.threshold,
                        },
                    )

        # ── Per-org RPM + burst rate limiting (from firewall config) ──
        if (
            not firewall_disabled
            and org_config.get("rate_limit_enabled", CONFIG.get("rate_limit_enabled", True))
            and REDIS_CLIENT is not None
        ):
            # Each org enforces its own configured limit against its own Redis
            # counter. Unauthenticated requests (empty org_slug) share a single
            # "global" bucket so the key is never "ratelimit::...".
            rl_scope = org_slug or "global"
            now_ts = int(time.time())
            try:
                burst_limit = org_config.get("burst_limit", CONFIG.get("burst_limit", 150))
                second_bucket = now_ts
                burst_key = f"ratelimit:{rl_scope}:burst:{second_bucket}"
                current_burst = await REDIS_CLIENT.incr(burst_key)
                if current_burst == 1:
                    await REDIS_CLIENT.expire(burst_key, 2)
                if current_burst > burst_limit:
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        _emit_telemetry(
                            status_code=429,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.30,
                            threat_type="rate_limit_burst",
                            metadata={"detail": f"Burst limit exceeded ({current_burst}/{burst_limit} req/s)"},
                        )
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": "rate_limited",
                                "message": f"Burst limit exceeded ({current_burst}/{burst_limit} req/s).",
                                "code": "burst_limit_exceeded",
                            },
                            headers={"Retry-After": "1"},
                        )
                    else:
                        LOG.warning(
                            "MONITOR: burst limit exceeded (%d/%d req/s)",
                            current_burst, burst_limit,
                        )
            except Exception:
                LOG.warning("Burst rate check failed, skipping", exc_info=True)

            rpm_limit = org_config.get("requests_per_minute", CONFIG.get("requests_per_minute", 1000))
            minute_bucket = now_ts // 60
            rpm_key = f"ratelimit:{rl_scope}:{minute_bucket}"
            try:
                current_rpm = await REDIS_CLIENT.incr(rpm_key)
                if current_rpm == 1:
                    await REDIS_CLIENT.expire(rpm_key, 120)
                if current_rpm > rpm_limit:
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        _emit_telemetry(
                            status_code=429,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.30,
                            threat_type="rate_limit_rpm",
                            metadata={"detail": f"RPM limit exceeded ({current_rpm}/{rpm_limit})"},
                        )
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": "rate_limited",
                                "message": f"Rate limit exceeded ({current_rpm}/{rpm_limit} RPM).",
                                "code": "rate_limit_exceeded",
                            },
                            headers={"Retry-After": "60"},
                        )
                    else:
                        LOG.warning(
                            "MONITOR: RPM limit exceeded (%d/%d)",
                            current_rpm, rpm_limit,
                        )
            except Exception:
                LOG.warning("RPM check failed, skipping", exc_info=True)

        messages = body.get("messages") or []
        prompt_for_estimate = _extract_prompt_from_messages(messages)
        # Clamp max_tokens to a sane ceiling BEFORE the rate-limit estimate so an
        # absurd completion budget (e.g. 999...9) can't immediately exhaust the
        # org TPM window. Write the clamped value back so the later enforcement
        # clamp stays consistent. Hard cap (65536) bounds even providerless orgs
        # where max_response_tokens may be unset.
        _mt_pre = body.get("max_tokens")
        if isinstance(_mt_pre, int) and _mt_pre > 0:
            _mt_ceiling = int(org_config.get("max_response_tokens", 4096) or 4096)
            _mt_hard_cap = 65536
            _mt_clamped = min(_mt_pre, _mt_ceiling, _mt_hard_cap)
            if _mt_clamped != _mt_pre:
                body["max_tokens"] = _mt_clamped
        estimated_request_tokens = _estimate_request_tokens(
            prompt_for_estimate,
            int(body.get("max_tokens") or 0),
        )

        # ── Per-org TPM rate limit enforcement (Phase 1 hardening) ──
        # Fail-CLOSED Lua check; org_tpm_limit=0 disables the ceiling.
        if RATE_LIMITER is not None and auth_ctx is not None and auth_ctx.org_slug:
            org_tpm_limit = 0
            if CONFIG_SYNC is not None:
                try:
                    org_cfg = CONFIG_SYNC.get_config(auth_ctx.org_slug) or {}
                    org_tpm_limit = int(org_cfg.get("org_tpm_limit", 0) or 0)
                except Exception:
                    org_tpm_limit = 0
            if org_tpm_limit:
                org_allowed, org_current = await RATE_LIMITER.check_org_rate_limit(
                    auth_ctx.org_slug,
                    org_tpm_limit,
                    estimated_request_tokens,
                )
                if not org_allowed:
                    METRICS["blocked"] += 1
                    _emit_telemetry(
                        status_code=429,
                        event_type="input_blocked",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.30,
                        threat_type="rate_limit_org_tpm",
                        metadata={
                            "detail": f"Org TPM ceiling exceeded ({org_current}/{org_tpm_limit})",
                            "org_slug": auth_ctx.org_slug,
                        },
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "rate_limited",
                            "scope": "org",
                            "message": f"Org TPM ceiling exceeded ({org_current}/{org_tpm_limit}).",
                            "code": "org_rate_limit_exceeded",
                        },
                        headers={"Retry-After": "60"},
                    )

        # ── Per-key TPM rate limit enforcement ──
        if RATE_LIMITER is not None and auth_ctx is not None and rate_limit_tpm:
            rate_allowed, current_usage = await RATE_LIMITER.check_rate_limit(
                auth_ctx.key_hash,
                rate_limit_tpm,
                estimated_request_tokens,
            )
            if not rate_allowed:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=429,
                    event_type="input_blocked",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=0.30,
                    threat_type="rate_limit_tpm",
                    metadata={
                        "detail": (
                            f"Token rate limit exceeded ({current_usage}/{rate_limit_tpm} TPM, "
                            f"est {estimated_request_tokens} tokens this request)"
                        ),
                    },
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": (
                            f"Token rate limit exceeded ({current_usage}/{rate_limit_tpm} TPM, "
                            f"estimated {estimated_request_tokens} tokens for this request)."
                        ),
                        "code": "rate_limit_exceeded",
                        "blocked_by": "rate_limit",
                        "rate_limit": {
                            "scope": "gateway_tpm",
                            "current_tpm": current_usage,
                            "limit_tpm": rate_limit_tpm,
                            "estimated_tokens": estimated_request_tokens,
                        },
                    },
                    headers={"Retry-After": "60"},
                )

        max_ctx = getattr(auth_ctx, "max_context_tokens", 0) if auth_ctx else 0
        if not max_ctx:
            max_ctx = CONFIG.get("default_max_context_tokens", 0)
        if max_ctx > 0:
            messages = minimize_context(messages, max_ctx)
            body["messages"] = messages

        prompt = prompt_for_estimate or _extract_prompt_from_messages(messages)
        # G7: fold top-level tool DEFINITIONS (name + description) into the scanned
        # prompt so an injection smuggled in a tool definition is caught too.
        _tool_defs_text = _extract_tool_definitions_text(body.get("tools"))
        if _tool_defs_text:
            prompt = (prompt + "\n" + _tool_defs_text) if prompt else _tool_defs_text
        _prompt_snippet = prompt[:500] if prompt else ""
        agent_data = _extract_agent_data(body, x_agent_data)

        # ── Input scanning (always runs, even without backend) ──
        effective_prompt = prompt
        redacted_prompt = None
        # Text after deterministic POLICY redaction but BEFORE Tier-2 — i.e. what
        # the input scanner receives. "" when policy did not redact. Used so the
        # operator trace attributes redaction to the policy stage (not Tier-2) and
        # so the redact zeroshield reports policy rules instead of a now-clean
        # Tier-2 verdict.
        policy_redacted_prompt = ""
        # Input policy-engine result (matched rules / redaction). Defaulted so the
        # redact-attribution branches can always read matched rule names even when
        # the policy check was skipped (gated) for this request.
        check_resp = {}
        scan_verdict = None
        hallucination_flagged = False
        output_enforcement = None
        is_rag_request = _detect_rag_request(body, messages)

        # ── Blocked keywords check (from firewall config) ──
        if not firewall_disabled:
            custom_blocked = org_config.get("blocked_keywords", [])
            if custom_blocked and isinstance(custom_blocked, list):
                # Defensive: ``prompt`` should be a str, but coerce so a non-string
                # (e.g. multimodal/content-part prompt) can never AttributeError here.
                prompt_lower = (prompt if isinstance(prompt, str) else str(prompt)).lower()
                matched_kw = [
                    kw for kw in custom_blocked
                    if _blocked_keyword_matches(prompt_lower, kw)
                ]
                if matched_kw:
                    if enforcement_mode == "block":
                        METRICS["blocked"] += 1
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        _emit_telemetry(
                            status_code=403,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.70,
                            threat_type="blocked_keyword",
                            metadata={"detail": f"Blocked keyword(s): {', '.join(matched_kw)}"},
                            prompt_snippet=_prompt_snippet,
                            endpoint_id=endpoint_id,
                        )
                        _audit_fire_and_forget(
                            org_slug=org_slug or "",
                            decision="block",
                            rule_code=(matched_kw[0] if matched_kw else "custom_keyword_block"),
                            metadata={
                                "input_bytes": len((prompt or "").encode("utf-8")),
                                "model_id": body.get("model", ""),
                                "matched_keywords": matched_kw,
                            },
                        )
                        return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                            action="block",
                            reason=f"Blocked keyword(s) detected: {', '.join(matched_kw)}",
                            detection_tier="config",
                            threat_type="blocked_keyword",
                            confidence=1.0,
                            matched_patterns=matched_kw,
                            original_prompt=prompt,
                            processing_time_ms=elapsed_ms,
                        ))
                    else:
                        LOG.warning(
                            "MONITOR: blocked keyword(s) found: %s (user=%s)",
                            matched_kw, user_id,
                        )

        # ── Max response tokens enforcement ──
        max_tokens_config = org_config.get("max_response_tokens", 4096)
        if max_tokens_config and not firewall_disabled:
            # Defensive clamp: only min() when the request value is an int.
            # The boundary validator coerces/pops max_tokens, but if anything
            # non-int slips through (None / float / str) a bare min() raises
            # TypeError -> unhandled 500. Fall back to the configured ceiling.
            _mt = body.get("max_tokens")
            if isinstance(_mt, int):
                body["max_tokens"] = min(_mt, max_tokens_config)
            elif body.get("max_completion_tokens") is not None:
                # SEAM-A: the client sent ONLY max_completion_tokens (the o1/o3/gpt-5
                # field). Injecting max_tokens here too produced a DUAL-field request
                # that reasoning models reject with 400. The completion budget is
                # already validated/clamped above, so leave max_tokens absent.
                _mct = body.get("max_completion_tokens")
                if isinstance(_mct, int):
                    body["max_completion_tokens"] = min(_mct, max_tokens_config)
            else:
                body["max_tokens"] = max_tokens_config

        if firewall_disabled:
            # Firewall is off -- skip all scanning, route directly to LLM
            is_stream = body.get("stream", False)
            if is_stream:
                METRICS["allowed"] += 1
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="allow",
                    rule_code="firewall_disabled_stream",
                    metadata={"stream": True, "model": body.get("model", "")},
                )
                return _launch_chat_stream_response(
                    request=request,
                    body=body,
                    org_config=org_config,
                    org_slug=org_slug,
                    auth_ctx=auth_ctx,
                    redacted_prompt=None,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_hash=auth_ctx.key_hash if auth_ctx else "",
                    rate_limit_tpm=rate_limit_tpm or 0,
                    estimated_tokens=estimated_request_tokens,
                    org_tpm_limit=int(org_config.get("org_tpm_limit", 0) or 0),
                    secure_output_scan=False,
                )
            code, resp = await LLM_ROUTER.acompletion(body, None)
            METRICS["allowed"] += 1
            elapsed_ms = (time.perf_counter() - start) * 1000
            if code == 200 and isinstance(resp, dict):
                resp["zeroshield"] = _build_zeroshield_metadata(
                    action="passthrough",
                    reason="Firewall disabled. No scanning performed.",
                    detection_tier="none",
                    original_prompt=prompt,
                    processing_time_ms=elapsed_ms,
                )
                _body = resp
            else:
                # Never reflect raw LiteLLM exception text to the client (R5).
                _body = _sanitize_llm_error_response(code, resp)
            return JSONResponse(
                status_code=code if code else 502,
                content=_body,
            )

        # ── Intent classification ──
        _request_intent = ""
        try:
            from scanner import classify_intent
            _request_intent = classify_intent(effective_prompt)
        except Exception:
            pass

        # ──────────────────────────────────────────────────────────────────
        # POLICY-FIRST PIPELINE
        # ──────────────────────────────────────────────────────────────────
        # The deterministic policy decision runs BEFORE Tier-1/Tier-2 input
        # scanning. This means:
        #   - action=="block"           → request short-circuits; expensive
        #                                 Tier-2 Bedrock scan is SKIPPED.
        #   - action=="redact"          → effective_prompt is replaced with
        #                                 the redacted text; the scanner sees
        #                                 only the redacted prompt.
        #   - action=="rewrite"         → effective_prompt is wrapped with a
        #                                 policy notice; scanner runs on it.
        #   - action=="model_downgrade" → body["model"] is downgraded; scan
        #                                 still runs on the prompt.
        #   - action=="allow"/"monitor" → fall through to scanning unchanged.
        # ──────────────────────────────────────────────────────────────────
        # Policy enforcement must depend on POLICY AVAILABILITY, not on whether
        # this gateway worker happened to register an AGENT_ID. The local policy
        # cache (POLICY_SYNC) enforces compiled rules without the control plane;
        # only the HTTP fallback path needs AGENT_ID + backend_url. Gating the
        # whole pipeline on AGENT_ID meant a failed/racy per-worker registration
        # silently disabled ALL policy-rule enforcement.
        _policy_cache_ready = POLICY_SYNC is not None and POLICY_SYNC.is_loaded
        if _policy_cache_ready or (AGENT_ID and CONFIG.get("backend_url")):
            # Optional: backend security scan first (high-certainty threats)
            if CONFIG.get("call_security_scan"):
                code, scan_resp = await asyncio.to_thread(_security_scan, prompt, "")
                if code == 200 and isinstance(scan_resp, dict):
                    action = scan_resp.get("recommended_action") or ""
                    if action in ("block_immediately", "block_and_alert"):
                        METRICS["blocked"] += 1
                        elapsed_ms = (time.perf_counter() - start) * 1000
                        return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                            action="block",
                            reason="Request blocked by backend security scan (high-certainty threat detected).",
                            detection_tier="policy",
                            original_prompt=prompt,
                            processing_time_ms=elapsed_ms,
                        ))

            # Policy check (deterministic, includes backend pattern policies)
            _policy_start = time.perf_counter()
            code, check_resp = await asyncio.to_thread(
                _policy_check_cached,
                effective_prompt,
                "",
                user_id,
                endpoint_id,
                agent_data,
                project_id,
                risk_score,
                requested_model,
                org_slug,
                actor=_build_policy_actor(auth_ctx, user_id),
            )
            # M-11: the HTTP-fallback policy path (_policy_check -> _http_request)
            # can return a NON-dict body on backend error/non-JSON (e.g. a raw
            # error string), which would break the many check_resp.get(...) reads
            # below — including the redact-attribution metadata assembly that reads
            # matched_rules/matched_policy_names. Normalize to a dict so all reads
            # stay null-safe (preserves the {} default contract).
            if not isinstance(check_resp, dict):
                check_resp = {}
            stage_metrics["policy_ms"] = round((time.perf_counter() - _policy_start) * 1000, 2)
            if code != 200:
                METRICS["blocked"] += 1
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": "Policy evaluation unavailable. Request denied (fail-closed).",
                        "code": "policy_unavailable",
                    },
                )

            action = check_resp.get("action") or "allow"
            if action == "block":
                has_policy_match = bool(
                    check_resp.get("matched_policies")
                    or check_resp.get("matched_rules")
                    or check_resp.get("matched_policy_names")
                )
                if not has_policy_match:
                    return _ambiguous_policy_block_response(org_slug=org_slug or "")
            if action == "block":
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    chat_outcome = "blocked"
                    try:
                        from .metrics import record_policy_block as _prom_policy_block
                    except ImportError:
                        from metrics import record_policy_block as _prom_policy_block  # type: ignore[no-redef]
                    categories = check_resp.get("matched_policy_categories") or []
                    threat_type = _category_to_threat_type(categories[0]) if categories else "policy_violation"
                    _prom_policy_block(org_slug, threat_type)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    if not check_resp.get("event_id"):
                        _emit_telemetry(
                            status_code=403,
                            event_type="input_blocked",
                            model=body.get("model", ""),
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="block",
                            risk_score=0.85,
                            threat_type=threat_type,
                            metadata={
                                "detail": check_resp.get("message") or "Policy block",
                                "matched_policies": check_resp.get("matched_policies") or [],
                                "matched_rules": check_resp.get("matched_rules") or [],
                                "matched_policy_ids": check_resp.get("matched_policy_ids") or [],
                                "matched_rule_ids": check_resp.get("matched_rule_ids") or [],
                            },
                            prompt_snippet=_prompt_snippet,
                            endpoint_id=endpoint_id,
                        )
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="block",
                        rule_code=((check_resp.get("matched_rules") or ["policy_block"])[0]),
                        metadata={
                            "input_bytes": len((prompt or "").encode("utf-8")),
                            "model_id": body.get("model", ""),
                            "matched_rules": check_resp.get("matched_rules") or [],
                            "matched_policies": check_resp.get("matched_policies") or [],
                        },
                    )
                    _policy_block_zs = _build_zeroshield_metadata(
                        action="block",
                        reason=check_resp.get("message") or "Request blocked by policy engine.",
                        detail=check_resp.get("message") or "Request blocked by policy engine.",
                        detection_tier="policy",
                        threat_type=threat_type,
                        matched_patterns=check_resp.get("matched_rules") or check_resp.get("matched_policies") or [],
                        original_prompt=prompt,
                        processing_time_ms=elapsed_ms,
                    )
                    # Dedicated fields carrying the REAL matched policy/rule names
                    # (distinct from scanner matched_patterns, which are prompt substrings).
                    _policy_block_zs["matched_policy_names"] = (
                        check_resp.get("matched_policy_names")
                        or check_resp.get("matched_policies")
                        or []
                    )
                    _policy_block_zs["matched_rule_names"] = check_resp.get("matched_rules") or []
                    return _build_block_response(403, "content_blocked", _policy_block_zs)
                else:
                    LOG.warning(
                        "MONITOR: policy would block request (message=%s, user=%s)",
                        check_resp.get("message"), user_id,
                    )

            if action == "redact" and check_resp.get("redacted_prompt"):
                _new_redacted = check_resp["redacted_prompt"]
                # F10a (input-side TEL-1 parity): only record a redaction when bytes
                # actually changed. A redact rule can MATCH on its condition while its
                # redaction_config.regex targets a pattern absent from the prompt, so
                # apply_redaction returns the text unchanged. Leaving redacted_prompt
                # None on that no-op makes telemetry + the client envelope honestly
                # read "allow" (matching pipeline_trace) instead of a phantom "redact".
                if _new_redacted != effective_prompt:
                    effective_prompt = _new_redacted
                    redacted_prompt = _new_redacted
                    policy_redacted_prompt = _new_redacted

            # ── Rewrite action: strip harmful pattern, log original ──
            if action == "rewrite":
                original_effective = effective_prompt
                matched_rules = check_resp.get("matched_rules") or []
                rewrite_detail = check_resp.get("message") or "Content policy applied"
                effective_prompt = f"[Content policy applied: harmful content removed] {effective_prompt}"
                redacted_prompt = effective_prompt
                _emit_telemetry(
                        event_type="input_blocked",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="rewrite",
                        risk_score=0.5,
                        threat_type="policy_violation",
                        pipeline_stage="query",
                        intent=_request_intent,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        metadata={"detail": rewrite_detail, "original_prompt": original_effective[:500]},
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="rewrite",
                    rule_code=((matched_rules or ["policy_rewrite"])[0]),
                    metadata={
                        "input_bytes": len((prompt or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "matched_rules": matched_rules,
                    },
                )
                LOG.info("Rewrite action applied (user=%s, rules=%s)", user_id, matched_rules)

            # ── Model downgrade action: switch to cheaper/safer model ──
            if action == "model_downgrade":
                redaction_cfg = check_resp.get("redaction_config") or {}
                downgrade_to = redaction_cfg.get("downgrade_to", org_config.get("litellm_default_model", "gpt-3.5-turbo"))
                original_model = requested_model
                # FIX-1.2a: only mutate + emit when this is a REAL downgrade. When
                # the resolved target equals the requested model (e.g. the policy
                # rule carried no downgrade_to and the default resolved back to the
                # same model — Haiku→Haiku), the previous code still rewrote the
                # model and emitted a 'downgrade' telemetry/audit event, faking a
                # downgrade that never happened. No-op → leave the model untouched
                # and emit nothing.
                if downgrade_to and downgrade_to != original_model:
                    requested_model = downgrade_to
                    body["model"] = downgrade_to
                    _emit_telemetry(
                            event_type="input_blocked",
                            model=original_model,
                            user_id=user_id,
                            project_id=str(project_id or ""),
                            key_prefix=auth_ctx.prefix if auth_ctx else "",
                            action="model_downgrade",
                            risk_score=0.4,
                            threat_type="policy_violation",
                            pipeline_stage="query",
                            intent=_request_intent,
                            latency_ms=(time.perf_counter() - start) * 1000,
                            metadata={"detail": f"Downgraded from {original_model} to {downgrade_to}"},
                            prompt_snippet=_prompt_snippet,
                            endpoint_id=endpoint_id,
                    )
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="downgrade",
                        rule_code=((check_resp.get("matched_rules") or ["policy_downgrade"])[0]),
                        metadata={
                            "input_bytes": len((prompt or "").encode("utf-8")),
                            "model_id": original_model,
                            "downgrade_to": downgrade_to,
                        },
                    )
                    LOG.info("Model downgrade: %s -> %s (user=%s)", original_model, downgrade_to, user_id)
                else:
                    LOG.info(
                        "Model downgrade no-op (target=%s == requested=%s); no mutation/telemetry (user=%s)",
                        downgrade_to, original_model, user_id,
                    )

        if INPUT_SCANNER is not None and org_config.get("input_scan_enabled", True):
            tier2_execution_mode = str(org_config.get("tier2_execution_mode", "sync_pre_llm")).strip().lower()
            tier2_stream_hold_enabled = bool(org_config.get("tier2_stream_hold_enabled", False))
            is_stream_request = bool(body.get("stream", False))
            force_sync_tier2 = tier2_execution_mode == "sync_pre_llm"

            # Async-post mode still needs synchronous Tier-2 for strict blocking
            # semantics when we cannot hold streaming safely.
            if tier2_execution_mode == "async_post_llm" and enforcement_mode == "block":
                if not is_stream_request:
                    force_sync_tier2 = True
                elif tier2_stream_hold_enabled:
                    force_sync_tier2 = True
                else:
                    force_sync_tier2 = True
                    LOG.warning(
                        "tier2_execution_mode=async_post_llm with block+stream without hold; forcing sync tier2"
                    )

            scan_start = time.perf_counter()
            # Phase 0 D-G2-v3: per-org tri-state Tier-2 override. Use .get()
            # without a default so ``None`` (no per-org opinion) survives and
            # is distinguishable from explicit ``False`` (force-disable).
            org_tier2_override = org_config.get("tier2_enabled")
            # Phase 0 D-G3-v3: per-org Tier-2 strictness governs behaviour
            # when the Bedrock circuit breaker is OPEN. Default True (fail
            # closed with HTTP 451) per Adversarial Triage F5.
            org_tier2_strict = bool(org_config.get("tier2_strict", True))
            # Per-org toxicity threshold (passed by value, never stored on the
            # shared scanner singleton, so concurrent orgs cannot race on it).
            org_toxicity_threshold = org_config.get("toxicity_threshold")
            # FIX-1.1a: X-Agent-Data (parsed by _extract_agent_data) is forwarded to
            # the policy check but, on the local-cache fast path, is NEVER threat-
            # scanned — the gateway evaluate() drops it and the INPUT_SCANNER only
            # ever saw ``effective_prompt``. An attacker could smuggle injection
            # payloads through X-Agent-Data and bypass Tier-1/Tier-2 entirely.
            # Fold the agent_data string values into the SCANNER input only (display,
            # redaction, downstream prompt all keep ``effective_prompt`` untouched).
            scan_text = effective_prompt
            if isinstance(agent_data, dict) and agent_data:
                try:
                    # Collect EVERY string anywhere in the agent_data tree, not
                    # just top-level values: a nested dict/list value previously
                    # slipped past this fold, so a string buried one level deep
                    # (e.g. {"ctx": {"note": "<injection>"}}) bypassed Tier-1/2.
                    _agent_str_values = _collect_nested_strings(agent_data)
                    if _agent_str_values:
                        scan_text = (effective_prompt or "") + "\n" + json.dumps(_agent_str_values)
                except (TypeError, ValueError):
                    scan_text = effective_prompt
            elif isinstance(agent_data, str) and agent_data.strip():
                # Non-JSON X-Agent-Data (decoded raw text) — scan it too so a
                # base64'd plain-text injection can't bypass the scanner.
                scan_text = (effective_prompt or "") + "\n" + agent_data
            try:
                if force_sync_tier2:
                    verdict = await INPUT_SCANNER.scan_prompt_with_tier2(
                        scan_text,
                        is_rag=is_rag_request,
                        org_tier2_override=org_tier2_override,
                        org_slug=org_slug or "",
                        org_tier2_strict=org_tier2_strict,
                        toxicity_threshold=org_toxicity_threshold,
                        request_id=_REQUEST_ID.get(""),
                    )
                    if verdict and verdict.tier == "tier_2":
                        stage_metrics["tier2_ms"] = round((time.perf_counter() - scan_start) * 1000, 2)
                    else:
                        stage_metrics["tier1_ms"] = round((time.perf_counter() - scan_start) * 1000, 2)
                else:
                    verdict = await INPUT_SCANNER.scan_prompt(
                        scan_text, is_rag=is_rag_request,
                        toxicity_threshold=org_toxicity_threshold,
                    )
                    stage_metrics["tier1_ms"] = round((time.perf_counter() - scan_start) * 1000, 2)
                    await enqueue_job(
                        job_type="tier2_post_scan",
                        request_id=request.headers.get("X-Request-ID", f"zs-tier2-{_uuid.uuid4().hex[:12]}"),
                        org_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                        payload={
                            "request_id": request.headers.get("X-Request-ID", ""),
                            "organization_id": getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                            "user_id": user_id,
                            "project_id": str(project_id or ""),
                            # FIX-1.1a: scan_text folds in X-Agent-Data so the
                            # async Tier-2 post-scan covers it too (parity with
                            # the sync path above).
                            "prompt": scan_text,
                            "execution_mode": tier2_execution_mode,
                            "is_rag": bool(is_rag_request),
                            "is_stream": bool(is_stream_request),
                        },
                    )
            except Exception as _t2err:
                # G3 breaker OPEN + tier2_strict=True -> Tier2UnavailableStrict.
                # Catch broadly + match by NAME (not class identity): the gateway
                # package loads twice (``ai_mesh_gateway.*`` AND top-level), so
                # there are two distinct ``Tier2UnavailableStrict`` classes;
                # scanner.py raises one while this frame was bound to the other,
                # which made a plain ``except Tier2UnavailableStrict`` miss it and
                # return a raw HTTP 500. Re-raise anything that isn't ours.
                if not _is_tier2_unavailable_strict(_t2err):
                    raise
                # Return HTTP 451 with the standardized degraded envelope and
                # ``Retry-After``.
                METRICS["blocked"] += 1
                retry_after = int(getattr(_t2err, "retry_after_seconds", 30) or 30)
                LOG.warning(
                    "Tier-2 scanner breaker OPEN (strict) — refusing request, retry_after=%ss",
                    retry_after,
                )
                # OPENAI-COMPAT FIX: use 503 (not 451). A transient scanner-breaker
                # outage is RETRYABLE — the stock OpenAI SDK honors Retry-After and
                # auto-retries on 5xx, but treats 451 as a terminal non-retryable
                # error (and has no 451 exception class → generic APIStatusError).
                # Body is the nested OpenAI envelope; ZeroShield diagnostics ride at
                # the top level so they never confuse the SDK.
                try:
                    from .responses_adapters import build_openai_error as _boe
                except ImportError:
                    from responses_adapters import build_openai_error as _boe
                return JSONResponse(
                    status_code=503,
                    content=_boe(
                        503,
                        "Security scanner is temporarily unavailable; please retry shortly.",
                        error_type="service_unavailable_error",
                        code="tier2_unavailable",
                        extra_top_level={"reason": "tier2_unavailable_strict", "retry_after": retry_after},
                    ),
                    headers={"Retry-After": str(retry_after)},
                )

            scan_verdict = verdict

            if _is_tier2_degraded_verdict(verdict):
                # Static-first / fail-open design: a Tier-2 *degraded* verdict
                # (Bedrock unavailable — client_error / parse failure /
                # bedrock_degraded) is NOT a content threat. The Tier-1 static
                # checks already ran and did not block (degraded verdicts only
                # occur after Tier-1 passed in scan_prompt_with_tier2), so we
                # fall back to that clean Tier-1 decision and ALLOW the request
                # instead of hard-blocking it. Hard-blocking here took the data
                # plane fully down whenever Bedrock auth/availability flapped,
                # contradicting the scanner's own MONITOR recommendation.
                LOG.warning(
                    "Tier-2 scanner degraded (reason=%s, detail=%s, user=%s); "
                    "falling back to Tier-1 static decision (fail-open).",
                    getattr(verdict, "reason_code", ""),
                    verdict.detail,
                    user_id,
                )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="monitor",
                    rule_code="tier2_degraded",
                    metadata={
                        "input_bytes": len((prompt or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "reason_code": getattr(verdict, "reason_code", ""),
                    },
                )
                # Surface the INPUT tier-2 dip as a dashboard-visible FLAG event
                # (mirror of M11's output_scan_degraded). Traffic still flows
                # (fail-open by design) but the degradation is no longer only in
                # the audit log — operators can see "tier-2 ran degraded, request
                # passed on the clean Tier-1 decision".
                _emit_telemetry(
                    status_code=200,
                    event_type="input_scan_degraded",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                    action="flag",
                    threat_type="scanner_degraded",
                    pipeline_stage="input_scan",
                    metadata={
                        "detail": "Tier-2 input guard degraded — prompt passed on the clean Tier-1 decision (fail-open)",
                        "reason_code": getattr(verdict, "reason_code", ""),
                        "module": "1.1",
                        "module_id": "1.1",
                    },
                )
                # Fall through to normal processing; Tier-1 was clean.

            try:
                from enforcement import (
                    normalize_action as _normalize_enforcement_action,
                    resolve_enforcement as _resolve_enforcement,
                    should_apply_redaction as _should_apply_redaction,
                    should_hard_block as _should_hard_block,
                )
            except ImportError:
                from .enforcement import (
                    normalize_action as _normalize_enforcement_action,
                    resolve_enforcement as _resolve_enforcement,
                    should_apply_redaction as _should_apply_redaction,
                    should_hard_block as _should_hard_block,
                )

            # B-ENF: prefer Tier-2 recommended_action over verdict.action (scanner
            # maps model "redact" → action="flag"; the recommendation is authoritative).
            _scan_meta = getattr(verdict, "scan_meta", None) or {}
            _guard_rec = (
                (_scan_meta.get("recommended_action") if isinstance(_scan_meta, dict) else None)
                or verdict.action
                or "allow"
            )
            injection_threshold = org_config.get("prompt_injection_threshold", 0.80)
            _INJECTION_THREAT_TYPES = {"prompt_injection", "jailbreak", "goal_hijacking"}
            _is_injection = verdict.threat_type in _INJECTION_THREAT_TYPES
            if _is_injection and _normalize_enforcement_action(_guard_rec) == "block":
                # Injection-type threats: respect scan_block_on_injection + confidence gate
                if (
                    not org_config.get("scan_block_on_injection", True)
                    or verdict.confidence < injection_threshold
                ):
                    _guard_rec = "monitor"
            elif (
                _normalize_enforcement_action(_guard_rec) == "block"
                and _is_redactable_pii_threat(verdict.threat_type)
            ):
                # PII is redacted, not hard-blocked, on the input path.
                _guard_rec = "redact"

            _org_policy_action = None
            if check_resp.get("matched_rules") or check_resp.get("matched_policy_names") or check_resp.get("matched_policies"):
                _org_policy_action = check_resp.get("action") or None

            _resolved_input_action = _resolve_enforcement(
                _guard_rec,
                org_policy_action=_org_policy_action,
                enforcement_mode=enforcement_mode,
                redaction_possible=True,
            )
            _should_block_verdict = _should_hard_block(_resolved_input_action, enforcement_mode)
            if _should_block_verdict:
                LOG.warning(
                    "Input blocked by scanner (type=%s, detail=%s, user=%s)",
                    verdict.threat_type, verdict.detail, user_id,
                )
                _emit_telemetry(
                    status_code=403 if enforcement_mode == "block" else 200,
                    event_type="input_blocked",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block" if enforcement_mode == "block" else "monitor",
                    risk_score=verdict.confidence,
                    threat_type=verdict.threat_type,
                    compliance_tags=org_config.get("compliance_frameworks", []),
                    pipeline_stage="query",
                    intent=_request_intent,
                    metadata={
                        "detail": verdict.detail,
                        "confidence": verdict.confidence,
                        "matched_patterns": verdict.matched_patterns,
                        **_telemetry_owasp_metadata(verdict.threat_type, verdict=verdict),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    tier_label = {"tier_1": "Tier 1 regex", "tier_1_5": "Tier 1.5 fuzzy", "tier_1_6": "Tier 1.6 semantic", "tier_2": "Tier 2 ML"}.get(verdict.tier, verdict.tier)
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="block",
                        rule_code=f"{verdict.tier or 'tier_1'}_{verdict.threat_type}",
                        metadata={
                            "input_bytes": len((prompt or "").encode("utf-8")),
                            "model_id": body.get("model", ""),
                            "score": verdict.confidence,
                            "matched_patterns": verdict.matched_patterns,
                        },
                    )
                    return _build_block_response(
                        403,
                        "content_blocked",
                        _build_zeroshield_metadata(
                            action="block",
                            reason=f"{tier_label} scanner detected {verdict.threat_type}: {verdict.detail}",
                            detection_tier=verdict.tier,
                            threat_type=verdict.threat_type,
                            confidence=verdict.confidence,
                            matched_patterns=verdict.matched_patterns,
                            original_prompt=prompt,
                            processing_time_ms=elapsed_ms,
                            intent=_request_intent,
                        ),
                        stage_metrics=stage_metrics,
                        prompt=prompt,
                        route_metadata=route_metadata,
                        requested_model=body.get("model", ""),
                        scan_verdict=verdict,
                    )
                else:
                    LOG.warning(
                        "MONITOR: would block %s (confidence=%.2f, user=%s)",
                        verdict.threat_type, verdict.confidence, user_id,
                    )

            # pii_detection_enabled (mapped to scan_block_on_pii in the gateway
            # payload) is org-scoped. When an org disables PII detection we skip
            # PII redaction for that org. Secrets/credentials are ALWAYS redacted
            # regardless of this toggle.
            pii_detection_enabled = bool(org_config.get("scan_block_on_pii", True))
            _redact_threat = (
                verdict.threat_type == "secret"
                or (pii_detection_enabled and _is_redactable_pii_threat(verdict.threat_type))
            )

            # B1 (egress = truth, fail-closed honesty): remember the exact bytes that
            # go INTO the deterministic PII redactor so we can byte-verify afterward
            # that the flagged span actually left the wire (mirrors the embeddings
            # byte-verify in _scan_redact_embedding_inputs).
            _text_before_pii_redact = effective_prompt
            _pii_redaction_applied = False

            if _should_apply_redaction(_resolved_input_action, verdict.threat_type) and _redact_threat:
                # C-2: matched_patterns can carry tier-2 guard EVIDENCE fragments
                # (not just pattern keys) that echo scanned identifiers. Scrub
                # before logging so raw PII never persists to the gateway logs.
                try:
                    from output_guard import redact_all as _redact_all
                    _safe_patterns = [_redact_all(str(p)) for p in (verdict.matched_patterns or [])]
                except Exception:
                    _safe_patterns = ["<redacted>"]
                LOG.info(
                    "PII/secret detected, redacting before LLM call (type=%s, patterns=%s, user=%s)",
                    verdict.threat_type, _safe_patterns, user_id,
                )
                # B1 (egress = truth): pass the VERDICT so redact_pii applies its
                # authoritative redaction (redact_all + redact_evidence_digit_spans for
                # Tier-2 evidence digit runs the regexes miss). Without it, redact_pii
                # degrades to plain redact_all (scanner.py:854) and a Tier-2-flagged
                # bare digit span would ride RAW to the provider while the verdict says
                # "redact" — a phantom redaction. Matches the embeddings egress path.
                effective_prompt = INPUT_SCANNER.redact_pii(effective_prompt, verdict=verdict)
                redacted_prompt = effective_prompt
                _pii_redaction_applied = True
            elif (
                verdict.threat_type == "pii"
                and not pii_detection_enabled
                and _normalize_enforcement_action(_guard_rec) in ("block", "redact", "flag")
            ):
                LOG.info(
                    "PII detected but org has PII detection disabled; allowing prompt unredacted (user=%s)",
                    user_id,
                )

            # B1 (egress = truth — fail-closed honesty): the firewall flagged genuine
            # PII/secret for redaction, but if the deterministic redactor
            # (``redact_pii`` + the shared digit backstop the router applies on the
            # wire via ``_redact_text_with_backstop``) could not change the ORIGINAL
            # prompt bytes at all, the value is UNMASKABLE (e.g. a natural-language
            # password/credential or a name/address the regexes miss). Forwarding it
            # raw while telemetry attests "redact" is a phantom redaction — fail
            # closed (block) instead of leaking it upstream. Compare against the
            # original ``prompt``, NOT ``_text_before_pii_redact``: policy-first
            # redaction can already have masked the text before Tier-2 flags it, so
            # a scanner-step no-op on the already-redacted bytes is EXPECTED and
            # must NOT trip this guard (AttackSimulator PII scenario).
            if (
                _pii_redaction_applied
                and (prompt or "").strip()
            ):
                try:
                    from llm_router import _redact_text_with_backstop as _egress_backstop
                except ImportError:  # pragma: no cover - packaging fallback
                    from .llm_router import _redact_text_with_backstop as _egress_backstop
                _redaction_noop = _egress_backstop(prompt, effective_prompt) == prompt
                _resolved_after_noop = _resolve_enforcement(
                    _guard_rec,
                    org_policy_action=_org_policy_action,
                    enforcement_mode=enforcement_mode,
                    redaction_possible=not _redaction_noop,
                )
                if _redaction_noop and _should_hard_block(_resolved_after_noop, enforcement_mode):
                    LOG.warning(
                        "PII/secret flagged but redaction was a no-op (unmaskable); "
                        "failing closed to prevent raw egress (type=%s, user=%s)",
                        verdict.threat_type, user_id,
                    )
                    METRICS["blocked"] += 1
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="block",
                        rule_code=f"{verdict.tier or 'tier_1'}_{verdict.threat_type}_unmaskable",
                        metadata={
                            "input_bytes": len((prompt or "").encode("utf-8")),
                            "model_id": body.get("model", ""),
                            "reason": "redaction no-op on flagged PII/secret",
                        },
                    )
                    _emit_telemetry(
                        status_code=403,
                        event_type="input_blocked",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=verdict.confidence,
                        threat_type=verdict.threat_type,
                        compliance_tags=org_config.get("compliance_frameworks", []),
                        pipeline_stage="query",
                        intent=_request_intent,
                        metadata={
                            "detail": "PII/secret detected but could not be redacted; blocked to prevent raw egress",
                            "confidence": verdict.confidence,
                            **_telemetry_owasp_metadata(verdict.threat_type, verdict=verdict),
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                    return _build_block_response(
                        403,
                        "content_blocked",
                        _build_zeroshield_metadata(
                            action="block",
                            reason=(
                                f"PII/secret detected but could not be redacted "
                                f"({verdict.threat_type}); blocked to prevent raw egress"
                            ),
                            detection_tier=verdict.tier,
                            threat_type=verdict.threat_type,
                            confidence=verdict.confidence,
                            matched_patterns=verdict.matched_patterns,
                            original_prompt=prompt,
                            processing_time_ms=elapsed_ms,
                            intent=_request_intent,
                        ),
                        stage_metrics=stage_metrics,
                        prompt=prompt,
                        route_metadata=route_metadata,
                        requested_model=body.get("model", ""),
                        scan_verdict=verdict,
                    )

        if not AGENT_ID or not CONFIG["backend_url"]:
            # No backend: forward to LLM (input scanning already done above).
            # #27: if the no-provider 422 was deferred so the input scan could
            # run, re-emit it here for clean prompts on the standalone path
            # (which forwards straight to the LLM and never reaches the
            # connected-path _validate_org_inference_model gate).
            if auth_ctx is not None and not inference_models and needs_inference:
                return _build_no_inference_provider_response()
            is_stream = body.get("stream", False)
            if is_stream:
                METRICS["allowed"] += 1
                _tel_action = "redact" if redacted_prompt is not None else "allow"
                _tel_threat = (scan_verdict.threat_type if scan_verdict and redacted_prompt is not None else "")
                _tel_risk = (scan_verdict.confidence if scan_verdict and redacted_prompt is not None else 0.0)
                telemetry_start = time.perf_counter()
                _emit_telemetry(
                    event_type="request",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    risk_score=_tel_risk,
                    action=_tel_action,
                    threat_type=_tel_threat,
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                    metadata={"stage_metrics_ms": stage_metrics},
                )
                stage_metrics["telemetry_enqueue_ms"] = round((time.perf_counter() - telemetry_start) * 1000, 2)
                preflight_block = _stream_preflight_block_if_needed(org_config)
                if preflight_block is not None:
                    return preflight_block
                return _launch_chat_stream_response(
                    request=request,
                    body=body,
                    org_config=org_config,
                    org_slug=org_slug,
                    auth_ctx=auth_ctx,
                    redacted_prompt=redacted_prompt,
                    scan_verdict=scan_verdict,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_hash=auth_ctx.key_hash if auth_ctx else "",
                    rate_limit_tpm=rate_limit_tpm or 0,
                    estimated_tokens=estimated_request_tokens,
                    org_tpm_limit=int(org_config.get("org_tpm_limit", 0) or 0),
                    secure_output_scan=bool(CONFIG.get("output_scan_enabled", True)),
                )
            upstream_start = time.perf_counter()
            code, resp = await LLM_ROUTER.acompletion(body, redacted_prompt)
            stage_metrics["upstream_ms"] = round((time.perf_counter() - upstream_start) * 1000, 2)
            METRICS["allowed"] += 1
            if code == 200:
                elapsed_ms = (time.perf_counter() - start) * 1000
                _tel_action = "redact" if redacted_prompt is not None else "allow"
                _tel_threat = (scan_verdict.threat_type if scan_verdict and redacted_prompt is not None else "")
                _tel_risk = (scan_verdict.confidence if scan_verdict and redacted_prompt is not None else 0.0)
                telemetry_start = time.perf_counter()
                _emit_telemetry(
                    event_type="request",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    latency_ms=elapsed_ms,
                    risk_score=_tel_risk,
                    action=_tel_action,
                    threat_type=_tel_threat,
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                    metadata={"stage_metrics_ms": stage_metrics},
                )
                stage_metrics["telemetry_enqueue_ms"] = round((time.perf_counter() - telemetry_start) * 1000, 2)
                response_headers: dict[str, str] = {}
                if isinstance(resp, dict):
                    if redacted_prompt is not None:
                        # Attribute redaction to the stage that performed it: policy
                        # (deterministic, masks all matched rules) runs first; Tier-2
                        # scans the already-redacted text. Report policy when it did
                        # the masking and Tier-2 added nothing (else "redact" with an
                        # empty/now-clean Tier-2 verdict).
                        _policy_rules = check_resp.get("matched_rules") or []
                        _policy_did = bool(policy_redacted_prompt) and policy_redacted_prompt != prompt
                        _tier2_did = bool(scan_verdict) and redacted_prompt != (policy_redacted_prompt or prompt)
                        _sv_patterns = list(scan_verdict.matched_patterns) if (scan_verdict and _tier2_did) else []
                        if _policy_did and not _tier2_did:
                            _red_tier, _red_threat = "policy", (
                                scan_verdict.threat_type
                                if scan_verdict and scan_verdict.threat_type not in ("none", "", None)
                                else "pii"
                            )
                            _red_patterns = _policy_rules
                            _red_reason = (
                                "Sensitive data redacted by org policy ("
                                + ", ".join(_policy_rules[:5])
                                + ") before forwarding to the LLM."
                                if _policy_rules
                                else "Sensitive data redacted by org policy before forwarding to the LLM."
                            )
                            _red_detail = _red_reason
                            _red_conf = scan_verdict.confidence if scan_verdict else 0.99
                        else:
                            _red_tier = scan_verdict.tier if scan_verdict else "tier_1"
                            _red_threat = scan_verdict.threat_type if scan_verdict else "pii"
                            _red_patterns = _sv_patterns
                            _red_reason = (
                                f"PII detected in prompt ({', '.join(_sv_patterns)}). "
                                "Redacted before forwarding to LLM."
                            )
                            _red_detail = scan_verdict.detail if scan_verdict else ""
                            _red_conf = scan_verdict.confidence if scan_verdict else 0.85
                        resp["zeroshield"] = _build_zeroshield_metadata(
                            action="redact",
                            reason=_red_reason,
                            detection_tier=_red_tier,
                            threat_type=_red_threat,
                            confidence=_red_conf,
                            matched_patterns=_red_patterns,
                            original_prompt=prompt,
                            redacted_prompt=redacted_prompt,
                            detail=_red_detail,
                            processing_time_ms=elapsed_ms,
                        )
                        response_headers["X-ZeroShield-Action"] = "redacted"
                    else:
                        zs_action, zs_reason, zs_threat, zs_conf, zs_patterns, zs_detail = _resolve_success_metadata_from_verdict(scan_verdict)
                        from pipeline_trace import enrich_zeroshield_from_verdict

                        resp["zeroshield"] = enrich_zeroshield_from_verdict(
                            _build_zeroshield_metadata(
                                action=zs_action,
                                reason=zs_reason,
                                detection_tier=scan_verdict.tier if scan_verdict else "none",
                                threat_type=zs_threat,
                                confidence=zs_conf,
                                matched_patterns=zs_patterns,
                                original_prompt=prompt,
                                detail=zs_detail,
                                processing_time_ms=elapsed_ms,
                            ),
                            scan_verdict=scan_verdict,
                            final_action=zs_action,
                        )
                        if zs_action == "flag":
                            response_headers["X-ZeroShield-Action"] = "flag"
                    # Carry the REAL matched policy/rule names from the deterministic
                    # policy engine onto the zeroshield metadata so the pipeline trace
                    # policy stage shows policy names (not scanner prompt substrings).
                    if isinstance(resp.get("zeroshield"), dict):
                        resp["zeroshield"]["matched_policy_names"] = (
                            check_resp.get("matched_policy_names")
                            or check_resp.get("matched_policies")
                            or []
                        )
                        resp["zeroshield"]["matched_rule_names"] = check_resp.get("matched_rules") or []
                # Operator pipeline trace (Module 1.1 simulator) — built BEFORE
                # zeroshield client-redaction. model_input shows the REDACTED
                # prompt actually forwarded to the LLM (so the operator sees what
                # the model received, not the raw input). Without this, the
                # frontend fabricates a trace with a wrong policy stage.
                if isinstance(resp, dict):
                    from pipeline_trace import build_pipeline_trace
                    _zs_full = resp.get("zeroshield") if isinstance(resp.get("zeroshield"), dict) else {}
                    resp["pipeline_trace"] = build_pipeline_trace(
                        prompt=_redact_trace_text(prompt),
                        forwarded_prompt=_redact_trace_text(redacted_prompt or prompt),
                        policy_redacted_prompt=policy_redacted_prompt,
                        policy_redacted_flag=(bool(policy_redacted_prompt) and policy_redacted_prompt != prompt),
                        stage_metrics=stage_metrics,
                        final_action=_zs_full.get("action") or "allow",
                        http_status=200,
                        scan_verdict=scan_verdict,
                        zeroshield=_zs_full,
                        response_text=_redact_trace_text(_extract_response_from_completion(resp)),
                        requested_model=body.get("model", ""),
                    )
                # ── SECURITY FIX: Redact sensitive fields from zeroshield metadata before returning to client ──
                if isinstance(resp.get("zeroshield"), dict):
                    resp["zeroshield"] = _redact_for_client_response(resp["zeroshield"]) or {}
                # ── §1.7 Output guard ── this sync_pre_llm path returns before the
                # main inline output-guard block, so run output scanning here too
                # (credential→block / PII→redact / IP→flag). Without this, the
                # tier-2 sync path ships model responses with zero output scanning.
                _og_block = await _apply_output_guard_nonstream(
                    resp,
                    org_config=org_config,
                    org_slug=org_slug,
                    body=body,
                    user_id=user_id,
                    project_id=project_id,
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    prompt=prompt,
                    start=start,
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                    request=request,
                )
                if _og_block is not None:
                    return _og_block
                return JSONResponse(content=resp, headers=_latin1_safe_headers(response_headers))
            # Never reflect raw LiteLLM exception text to the client (R5).
            return JSONResponse(
                status_code=code if code else 502,
                content=_sanitize_llm_error_response(code, resp),
            )

        # ── Policy check + backend security scan run ABOVE input_scan now ──
        # (See block before `if INPUT_SCANNER is not None ...` earlier in this handler.)
        # Policy decisions deterministically run BEFORE Tier-1/Tier-2 scanning so:
        #   - action == "block" short-circuits the request (Tier-2 Bedrock cost saved)
        #   - action == "redact" feeds the redacted prompt into the scanner
        #   - action == "rewrite" / "model_downgrade" mutate effective_prompt/body
        # before scanning observes them.

        # ── Dynamic model routing for every /v1/chat/completions request ──
        routing_prefs = _extract_chat_routing_preferences(body, org_config, auth_ctx, scan_verdict)
        routing_active = bool(
            inference_models
            and LLM_ROUTER is not None
            and routing_prefs["routing_enabled"]
            and not isolation_reroute_locked
        )
        # ── M1: model isolation — reject a disallowed/unknown CONCRETE model
        # BEFORE routing. The early allowlist checks (~3446/3468) are GATED on
        # `not routing_active`, so with routing on they're skipped; the adjudicator
        # below then OVERWRITES `requested_model` with a compliant fallback, so a
        # request for a model the org does not own (e.g. "gpt-4o", "opus",
        # "unknown-xyz") would otherwise be silently rerouted and returned 200.
        # A routing SENTINEL ("auto"/empty) is the explicit "you pick" contract and
        # still routes; a concrete, non-owned model is rejected 422. This validates
        # the ORIGINAL requested model (before reroute), unlike the downstream
        # _validate_org_inference_model which only sees the already-routed model.
        if auth_ctx is not None and requested_model and not _is_routing_sentinel_model(requested_model):
            _org_identities = _routing_identity_set(inference_models)
            if _org_identities and requested_model not in _org_identities:
                # B1.1-undercount: request passed the gateway but targets a concrete
                # model the org has not connected — count it once (action=block) so it
                # is visible in module 1.1 "Requests inspected".
                _emit_telemetry(
                    status_code=422,
                    event_type="request",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    risk_score=0.0,
                    action="block",
                    threat_type="model_not_configured",
                    endpoint_id=endpoint_id,
                    metadata={"stage_metrics_ms": stage_metrics, "outcome": "model_not_configured"},
                )
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": "model_not_configured",
                        "message": (
                            f"Model '{_safe_model_echo(requested_model)}' is not configured for "
                            "inference in this organization. Connect it under Multi-Model Governance, "
                            "use a connected model, or send 'auto' to route automatically."
                        ),
                        "code": "model_not_configured",
                        "blocked_by": "model_routing",
                        "category": "inference_not_configured",
                    },
                )
        if routing_active:
            # B1 FIX (CRITICAL): exclude runtime-ISOLATED (model_state) and KILL-SWITCHED
            # models from the routing candidate set BEFORE adjudication. The scorer
            # (_score_routing_models) only hard-filters on LLMModelConfig.is_active and
            # never consulted model_state:*/kill_switch:*, so model='auto' / adjudicated
            # routing could SELECT and SERVE an operator-isolated or killed model
            # (isolation was enforced only on the pre-routing *requested* model). Removing
            # them here makes isolation/kill-switch effective on every routing path; if all
            # compliant candidates are down, adjudicate returns None and the existing
            # compliance/availability gate returns 503 (fail-closed). B3: the exclusion is
            # logged so the previously-silent bypass now leaves an audit trail.
            inference_models, _zs_excluded = await _drop_isolated_or_killed_candidates(
                inference_models, org_slug, auth_ctx.prefix if auth_ctx else "",
            )
            selection = await LLM_ROUTER.adjudicate_model_selection(
                routing_models=inference_models,
                request_messages=body.get("messages") or [],
                preferred_model=routing_prefs["preferred_model"],
                request_risk_score=routing_prefs["request_risk_score"],
                required_compliance=routing_prefs["required_compliance"],
                data_sensitivity=routing_prefs["data_sensitivity"],
                estimated_tokens=routing_prefs["estimated_tokens"],
                latency_budget_ms=routing_prefs["latency_budget_ms"],
                weights=routing_prefs["weights"],
                allowed_models=routing_allowed_models,
                token_budget_tpm=routing_prefs["token_budget_tpm"],
                adjudicator_model=os.getenv("BEDROCK_ADJUDICATOR_MODEL", "").strip() or None,
            )
            if selection:
                route_selection = selection
                requested_model = selection.model_name
                body["model"] = requested_model
                route_metadata = _build_routing_metadata(
                    selection,
                    routing_enabled=bool(routing_prefs["routing_enabled"]),
                    routing_override=routing_prefs["routing_override"],
                    org_routing_enabled=bool(routing_prefs["org_routing_enabled"]),
                    latency_budget_ms=routing_prefs["latency_budget_ms"],
                    data_sensitivity=routing_prefs["data_sensitivity"],
                    required_compliance=routing_prefs["required_compliance"],
                    weights=routing_prefs["weights"],
                    request_risk_score=routing_prefs["request_risk_score"],
                    estimated_tokens=routing_prefs["estimated_tokens"],
                    token_budget_tpm=routing_prefs["token_budget_tpm"],
                )
                LOG.info(
                    "Chat routing selected model: %s (requested=%s, source=%s, reason=%s)",
                    selection.model_name,
                    selection.requested_model or "auto",
                    selection.decision_source,
                    selection.reason,
                )
                if TELEMETRY is not None:
                    _emit_telemetry(
                        event_type="model_routed",
                        model=selection.model_name,
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        # F10b: a plain auto-mode selection is NOT a reroute — only
                        # a client-PINNED model that was changed is. Mirror the trace
                        # builder / pipeline_trace predicate (exclude the "auto"
                        # sentinel) so telemetry and the trace agree.
                        action="reroute" if (selection.requested_model not in ("", "auto") and selection.model_name != selection.requested_model) else "confirm",
                        risk_score=routing_prefs["request_risk_score"],
                        threat_type="none",
                        pipeline_stage="routing",
                        intent=_request_intent,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        metadata=route_metadata,
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
            elif _routing_compliance_required(routing_prefs):
                # FIX-1.5a (CRITICAL fail-open → fail-closed): routing is active and
                # the request demands a compliance tag / non-public sensitivity, but
                # the adjudicator found NO eligible compliant model (selection is
                # None). The old code silently fell through and ran the request on
                # the client's ORIGINAL (non-compliant) model. Block instead. The
                # governance trace is preserved (route_metadata carries the reason).
                _compliance_reason = _compliance_unsatisfiable_reason(routing_prefs)
                route_metadata = {
                    "original_model": _safe_model_echo(body.get("model")) or "auto",
                    "selected_model": "",
                    "routed_model": "",
                    "routing_enabled": bool(routing_prefs["routing_enabled"]),
                    "routing_override": routing_prefs["routing_override"],
                    "org_routing_enabled": bool(routing_prefs["org_routing_enabled"]),
                    "decision_source": "compliance_block",
                    "routing_reason": _compliance_reason,
                    "reroute_reason": _compliance_reason,
                    "policy_summary": "Request blocked: no model satisfies the required compliance/sensitivity.",
                    "compliance_requirements": routing_prefs["required_compliance"],
                    "data_sensitivity": routing_prefs["data_sensitivity"],
                    "request_risk_score": routing_prefs["request_risk_score"],
                    "estimated_tokens": routing_prefs["estimated_tokens"],
                    "token_budget_tpm": routing_prefs["token_budget_tpm"],
                    "latency_budget_ms": routing_prefs["latency_budget_ms"],
                    "weights": routing_prefs["weights"],
                }
                METRICS["blocked"] += 1
                if TELEMETRY is not None:
                    _emit_telemetry(
                        event_type="model_routed",
                        model=_safe_model_echo(body.get("model")) or "auto",
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=routing_prefs["request_risk_score"],
                        threat_type="compliance_routing",
                        pipeline_stage="routing",
                        intent=_request_intent,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        metadata=route_metadata,
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                LOG.warning(
                    "Compliance routing unsatisfiable; blocking request (reason=%s, user=%s)",
                    _compliance_reason, user_id,
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "compliance_routing_unsatisfiable",
                        "blocked_by": "compliance_routing",
                        "message": "No model satisfies the required compliance/sensitivity for this request.",
                        "code": "compliance_routing_unsatisfiable",
                        "zeroshield": {"routing": route_metadata},
                    },
                )
        elif isolation_reroute_audit is not None:
            route_metadata = _build_isolation_reroute_metadata(
                isolation_reroute_audit,
                routing_prefs,
            )
        elif route_metadata is None:
            # No server-side routing resolved a canonical model; echo only a
            # sanitized form of the client model (never the raw string) (R4).
            _safe_req_model = _safe_model_echo(body.get("model")) or "auto"
            route_metadata = {
                "original_model": _safe_req_model,
                "selected_model": _safe_req_model,
                "routed_model": _safe_req_model,
                "routing_enabled": bool(routing_prefs["routing_enabled"]),
                "routing_override": routing_prefs["routing_override"],
                "org_routing_enabled": bool(routing_prefs["org_routing_enabled"]),
                "decision_source": "routing_disabled" if not routing_prefs["routing_enabled"] else "no_routing_models",
                "routing_reason": (
                    "Dynamic routing disabled by governance setting"
                    if not routing_prefs["routing_enabled"]
                    else "No eligible routing models configured; preferred model used"
                ),
                "policy_summary": (
                    "Routing bypassed by explicit disable control"
                    if not routing_prefs["routing_enabled"]
                    else "Routing skipped because model catalog is empty"
                ),
                "compliance_requirements": routing_prefs["required_compliance"],
                "data_sensitivity": routing_prefs["data_sensitivity"],
                "request_risk_score": routing_prefs["request_risk_score"],
                "estimated_tokens": routing_prefs["estimated_tokens"],
                "token_budget_tpm": routing_prefs["token_budget_tpm"],
                "latency_budget_ms": routing_prefs["latency_budget_ms"],
                "weights": routing_prefs["weights"],
            }

        if _is_routing_sentinel_model(requested_model):
            resolved = _resolve_routing_hint_model(
                requested_model, org_config, inference_models
            )
            if resolved and not _is_routing_sentinel_model(resolved):
                requested_model = resolved
                body["model"] = resolved

        routing_probe_only = not needs_inference
        if routing_probe_only and _is_routing_sentinel_model(requested_model):
            if not inference_models:
                # B1.1-undercount: this request PASSED the gateway (auth + input
                # scan) but is rejected because no inference provider is connected.
                # Emit one canonical ingress `request` event so it is still counted
                # in module 1.1 "Requests inspected" (action=block → "stopped before
                # downstream completion"). request_id is auto-injected from the
                # _REQUEST_ID ContextVar by _emit_telemetry.
                _emit_telemetry(
                    status_code=422,
                    event_type="request",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    risk_score=0.0,
                    action="block",
                    threat_type="no_inference_provider",
                    endpoint_id=endpoint_id,
                    metadata={"stage_metrics_ms": stage_metrics, "outcome": "no_inference_provider"},
                )
                return _build_no_inference_provider_response()
            if route_selection is None:
                _emit_telemetry(
                    status_code=422,
                    event_type="request",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    risk_score=0.0,
                    action="block",
                    threat_type="routing_target_unresolved",
                    endpoint_id=endpoint_id,
                    metadata={"stage_metrics_ms": stage_metrics, "outcome": "routing_target_unresolved"},
                )
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "routing_target_unresolved",
                        "message": (
                            "Could not resolve a routing target. Connect an inference model "
                            "under Model Connection (with API key), enable Dynamic Routing, and "
                            "set Default Fallback Model in Routing Governance."
                        ),
                        "code": "routing_target_unresolved",
                        "blocked_by": "model_routing",
                        "category": "inference_not_configured",
                        "zeroshield": {"routing": route_metadata or {}},
                    },
                )

        # ── FIX-1.5a (fail-closed, routing DISABLED) ──
        # When dynamic routing is OFF the adjudicator never runs, so a request that
        # demands a compliance tag / non-public sensitivity would otherwise execute
        # on the client's chosen model with zero compliance enforcement. Reject the
        # final concrete model if it does NOT pass the same hard filters the router
        # would have applied (reuse routing_isolation.model_passes_hard_filters).
        #
        # NOTE: this gate must NOT be conditioned on needs_inference/routing_probe_only.
        # Chat inference runs downstream regardless of whether max_tokens/stream was
        # sent, so gating on needs_inference let a caller bypass the compliance gate
        # entirely by omitting max_tokens while still being inferred on a non-compliant
        # model (Critical fail-open). The tenant-ownership gate below was already
        # un-gated from needs_inference for this exact reason; sentinel/probe models
        # are still excluded via _is_routing_sentinel_model.
        if (
            not routing_active
            and requested_model
            and not _is_routing_sentinel_model(requested_model)
            and _routing_compliance_required(routing_prefs)
        ):
            try:
                from routing_isolation import model_passes_hard_filters as _model_passes_hard_filters
            except ImportError:
                from .routing_isolation import model_passes_hard_filters as _model_passes_hard_filters
            _final_entry = _find_routing_model_entry(inference_models, requested_model)
            _passes = bool(_final_entry) and _model_passes_hard_filters(
                _final_entry,
                data_sensitivity=routing_prefs["data_sensitivity"],
                required_compliance=routing_prefs["required_compliance"],
            )
            if not _passes:
                _compliance_reason = _compliance_unsatisfiable_reason(routing_prefs)
                route_metadata = {
                    "original_model": _safe_model_echo(body.get("model")) or "auto",
                    "selected_model": "",
                    "routed_model": "",
                    "routing_enabled": bool(routing_prefs["routing_enabled"]),
                    "routing_override": routing_prefs["routing_override"],
                    "org_routing_enabled": bool(routing_prefs["org_routing_enabled"]),
                    "decision_source": "compliance_block",
                    "routing_reason": _compliance_reason,
                    "reroute_reason": _compliance_reason,
                    "policy_summary": "Request blocked: chosen model fails the required compliance/sensitivity (routing disabled).",
                    "compliance_requirements": routing_prefs["required_compliance"],
                    "data_sensitivity": routing_prefs["data_sensitivity"],
                    "request_risk_score": routing_prefs["request_risk_score"],
                    "estimated_tokens": routing_prefs["estimated_tokens"],
                    "token_budget_tpm": routing_prefs["token_budget_tpm"],
                    "latency_budget_ms": routing_prefs["latency_budget_ms"],
                    "weights": routing_prefs["weights"],
                }
                METRICS["blocked"] += 1
                if TELEMETRY is not None:
                    _emit_telemetry(
                        event_type="model_routed",
                        model=_safe_model_echo(body.get("model")) or "auto",
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=routing_prefs["request_risk_score"],
                        threat_type="compliance_routing",
                        pipeline_stage="routing",
                        intent=_request_intent,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        metadata=route_metadata,
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                LOG.warning(
                    "Compliance gate (routing disabled): chosen model fails hard filters; blocking (reason=%s, user=%s)",
                    _compliance_reason, user_id,
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "compliance_routing_unsatisfiable",
                        "blocked_by": "compliance_routing",
                        "message": "No model satisfies the required compliance/sensitivity for this request.",
                        "code": "compliance_routing_unsatisfiable",
                        "zeroshield": {"routing": route_metadata},
                    },
                )

        # ── Defense-in-depth: the FINAL routed model must never be a reserved
        # platform/guard model (routing/fallback should already exclude it). Block
        # generically — do NOT fall through to the allowlist 403s below, which name
        # the model and would leak the internal guard model name to the client. ──
        try:
            from platform_models import is_platform_model_name as _is_platform_model_final
        except ImportError:
            from .platform_models import is_platform_model_name as _is_platform_model_final
        # B9: these final model re-checks must NOT be gated on routing_probe_only
        # (needs_inference). Chat inference runs regardless of max_tokens/stream
        # (per the #27 fix), so a concrete platform/guard or non-allowlisted model
        # — including a REROUTE target — would otherwise be served on a request
        # that merely omitted max_tokens. Mirror the FIX-1.5a/tenant-ownership
        # un-gating; the _is_routing_sentinel_model carve-out still skips 'auto'.
        if (
            requested_model
            and not _is_routing_sentinel_model(requested_model)
            and _is_platform_model_final(requested_model)
        ):
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "The requested model is not available for inference.",
                    "code": "model_not_allowed",
                    "zeroshield": {"routing": route_metadata or {}},
                },
            )

        # ── Final allowlist enforcement against the routed model ──
        if (
            allowed_models
            and requested_model
            and not _is_routing_sentinel_model(requested_model)
            and requested_model not in allowed_models
        ):
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Final routed model '{_safe_model_echo(requested_model)}' is not in your allowlist.",
                    "code": "model_not_allowed",
                    "zeroshield": {
                        "routing": route_metadata or {},
                    },
                },
            )

        if not firewall_disabled and org_config.get("model_isolation_enabled", False):
            global_allowed = global_allowed_models
            if (
                global_allowed
                and requested_model
                and not _is_routing_sentinel_model(requested_model)
                and requested_model not in global_allowed
            ):
                if enforcement_mode == "block":
                    METRICS["blocked"] += 1
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": "forbidden",
                            "message": f"Final routed model '{_safe_model_echo(requested_model)}' is not in the global allowlist.",
                            "code": "model_not_allowed",
                            "zeroshield": {
                                "routing": route_metadata or {},
                            },
                        },
                    )
                LOG.warning("MONITOR: routed model '%s' not in global allowlist", requested_model)

        # ── Per-model rate limiting ──
        if RATE_LIMITER is not None and CONFIG_SYNC is not None:
            routing_models = CONFIG_SYNC.get_model_routing(org_slug)
            model_cfg = next((m for m in routing_models if m.get("model_name") == requested_model), None) if routing_models else None
            if model_cfg and model_cfg.get("rate_limit_rpm", 0) > 0:
                model_allowed, model_count = await RATE_LIMITER.check_model_rate_limit(
                    requested_model, model_cfg["rate_limit_rpm"], org_slug=org_slug
                )
                try:
                    from .metrics import record_rate_limit as _prom_rl
                except ImportError:
                    from metrics import record_rate_limit as _prom_rl  # type: ignore[no-redef]
                _prom_rl(org_slug, requested_model, model_allowed)
                if not model_allowed:
                    METRICS["blocked"] += 1
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "rate_limit_exceeded",
                            "message": f"Per-model rate limit exceeded for {requested_model}.",
                            "code": "model_rate_limit",
                        },
                        headers={"Retry-After": "60"},
                    )

        # ── Optional: backend deep scan (Tier 2, async parallel) ──
        deep_scan_task = None

        def _cancel_deep_scan() -> None:
            nonlocal deep_scan_task
            if deep_scan_task is not None and not deep_scan_task.done():
                deep_scan_task.cancel()
                deep_scan_task = None

        if org_config.get("deep_scan_enabled") and org_config.get("input_scan_enabled", True) and AGENT_ID and CONFIG["backend_url"]:
            deep_scan_task = asyncio.create_task(
                asyncio.to_thread(_security_scan, effective_prompt, "")
            )

        is_stream = body.get("stream", False)

        # Tenant boundary: a chat completion ALWAYS performs inference, so the
        # org-model-ownership gate must run for every authenticated request that
        # reaches here (i.e. passed input scanning) — NOT only when
        # ``needs_inference`` (stream | max_tokens>0) is set. Gating on
        # needs_inference let a request shaped {"model":"X","messages":[...]}
        # (no max_tokens/stream → needs_inference False) skip validation, so a
        # tenant whose org owns NO models could invoke ANOTHER org's model via
        # the global router (cross-tenant credential/compute abuse). This runs
        # AFTER input scanning, so a provider-less org's malicious prompt is
        # still scanned+blocked first; only a CLEAN prompt gets the 422 (#27).
        if auth_ctx is not None:
            inference_err = _validate_org_inference_model(
                requested_model=requested_model,
                body=body,
                inference_models=inference_models,
            )
            if inference_err is not None:
                _cancel_deep_scan()
                return inference_err
            body["_inference_allowlist"] = sorted(_routing_identity_set(inference_models))
            if route_selection and route_selection.fallback_chain:
                body["_compliant_fallback_chain"] = list(route_selection.fallback_chain)
            elif CONFIG_SYNC is not None:
                try:
                    from routing_isolation import fallback_profile_key
                except ImportError:
                    from .routing_isolation import fallback_profile_key
                fb_payload = CONFIG_SYNC.get_fallback_chains(org_slug)
                profile_key = fallback_profile_key(
                    routing_prefs.get("data_sensitivity", "public"),
                    routing_prefs.get("required_compliance"),
                )
                profile_chain = (fb_payload.get("chains") or {}).get(profile_key, [])
                per_primary = (fb_payload.get("per_primary") or {}).get(requested_model)
                body["_compliant_fallback_chain"] = per_primary or [
                    m for m in profile_chain if m != requested_model
                ]

        # ── Circuit breaker check ──
        if CIRCUIT_BREAKER is not None:
            cb_status = await CIRCUIT_BREAKER.check(requested_model)
            if cb_status.should_block:
                METRICS["blocked"] += 1
                _cancel_deep_scan()
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "service_unavailable",
                        "message": f"Circuit breaker OPEN for model '{requested_model}'. Try again later.",
                        "code": "circuit_breaker_open",
                    },
                    headers={"Retry-After": str(CONFIG.get("circuit_breaker_cooldown_seconds", 120))},
                )

        if is_stream:
            _cancel_deep_scan()
            preflight_block = _stream_preflight_block_if_needed(org_config)
            if preflight_block is not None:
                return preflight_block
            if route_selection is not None:
                requested = getattr(route_selection, "requested_model", None) or "auto"
                routed = getattr(route_selection, "model_name", "") or ""
                rerouted = bool(requested and requested != "auto" and routed and routed != requested)
                if rerouted:
                    _audit_fire_and_forget(
                        org_slug=org_slug or "",
                        decision="downgrade",
                        rule_code="model_routed_stream",
                        metadata={
                            "stream": True,
                            "selected_model": routed,
                            "requested_model": requested,
                            "decision_source": getattr(route_selection, "decision_source", ""),
                        },
                    )
            METRICS["allowed"] += 1
            chat_outcome = "stream"
            return _launch_chat_stream_response(
                request=request,
                body=body,
                org_config=org_config,
                org_slug=org_slug,
                auth_ctx=auth_ctx,
                redacted_prompt=redacted_prompt,
                route_selection=route_selection,
                scan_verdict=scan_verdict,
                user_id=user_id,
                project_id=str(project_id or ""),
                key_hash=auth_ctx.key_hash if auth_ctx else "",
                rate_limit_tpm=rate_limit_tpm or 0,
                estimated_tokens=estimated_request_tokens,
                org_tpm_limit=int(org_config.get("org_tpm_limit", 0) or 0),
                secure_output_scan=bool(CONFIG.get("output_scan_enabled", True)),
            )
        upstream_start = time.perf_counter()
        code, llm_resp = await LLM_ROUTER.acompletion(body, redacted_prompt)
        stage_metrics["upstream_ms"] = round((time.perf_counter() - upstream_start) * 1000, 2)
        if code != 200:
            # Record circuit breaker error
            if CIRCUIT_BREAKER is not None and code >= 500:
                await CIRCUIT_BREAKER.record_error(requested_model, org_slug=org_slug or "default")
            # Record risk event for model errors.
            # NF-2: an upstream 429 (provider rate-limit) is a TRANSIENT capacity
            # signal, NOT a model-quality/adversarial risk. Counting it toward the
            # composite risk score auto-isolated otherwise-healthy BYOK models
            # (e.g. a free-tier model that merely hit its upstream RPM cap) and
            # served a misleading 503 model_isolated. Exclude 429 (and 408
            # request-timeout, likewise transient) from risk accumulation +
            # auto-isolation; genuine upstream failures (5xx) and other 4xx still
            # feed the risk engine.
            if REDIS_CLIENT is not None and code >= 400 and code not in (408, 429):
                from model_state import record_risk_event, check_auto_isolate
                risk_val = 0.8 if code >= 500 else 0.4
                composite = await record_risk_event(REDIS_CLIENT, requested_model, org_slug or "default", risk_val, "circuit_breaker")
                # Check auto-isolation threshold
                ms_key = f"model_state:{org_slug or 'default'}:{requested_model}"
                ms_raw = await REDIS_CLIENT.get(ms_key)
                ms_threshold = 80.0
                ms_action = "block"
                if ms_raw:
                    try:
                        ms_data = json.loads(ms_raw if isinstance(ms_raw, str) else ms_raw.decode())
                        ms_threshold = float(ms_data.get("threshold", 80.0))
                        ms_action = ms_data.get("action", "block")
                    except (json.JSONDecodeError, TypeError):
                        pass
                await check_auto_isolate(REDIS_CLIENT, requested_model, org_slug or "default", composite, ms_threshold, ms_action)
            # B1.1-undercount: the request PASSED the gateway but upstream inference
            # failed (model unavailable / 5xx / provider error). Previously NO
            # `request` event was emitted on this path, so the request was invisible
            # to module 1.1 "Requests inspected" (and, with routing on, only the
            # earlier model_routed event existed → it was mis-counted as "allowed").
            # Emit one canonical ingress `request` event (action=block → "stopped
            # before downstream completion") so it is counted exactly once and
            # classified honestly. request_id is auto-injected by _emit_telemetry.
            _emit_telemetry(
                status_code=code if code else 502,
                event_type="request",
                model=body.get("model", ""),
                user_id=user_id,
                project_id=str(project_id or ""),
                key_prefix=auth_ctx.prefix if auth_ctx else "",
                latency_ms=(time.perf_counter() - start) * 1000,
                risk_score=0.0,
                action="block",
                threat_type="upstream_inference_error",
                endpoint_id=endpoint_id,
                metadata={"stage_metrics_ms": stage_metrics, "outcome": "upstream_inference_error", "upstream_status": code},
            )
            # Never reflect raw LiteLLM exception text (fallback topology +
            # OpenRouter user_id) to the client (R5).
            return JSONResponse(
                status_code=code if code else 502,
                content=_sanitize_llm_error_response(code, llm_resp),
            )

        # Record circuit breaker success
        if CIRCUIT_BREAKER is not None:
            await CIRCUIT_BREAKER.record_success(requested_model, org_slug=org_slug or "default")

        # Optional: post-response policy check
        response_text = _extract_response_from_completion(llm_resp)
        # F4: fold secondary channels (reasoning_content, tool_calls) into the
        # output-guard scan input so a secret/PII present only there triggers a
        # verdict (content-only scanning was an asymmetric non-stream bypass).
        _og_scan_text = _extract_scannable_output_text(llm_resp)

        # ── Output guard (non-streaming) ──
        # Respect per-org master toggle (response_filtering_enabled → output_scan_enabled)
        # in addition to the global GATEWAY_OUTPUT_GUARD_ENABLED env default.
        output_verdict = None
        _output_scan_degraded = False  # M11: set True if the tier-2 output guard could not scan
        _output_guard_active = (
            OUTPUT_GUARD is not None
            and _og_scan_text
            and CONFIG.get("output_guard_enabled", True)
            and org_config.get("output_scan_enabled", CONFIG.get("output_scan_enabled", True))
        )
        if _output_guard_active:
            # Hallucination grounding must score the answer against ACTUAL
            # retrieved RAG context — never against the user's own prompt.
            # Previously context_chunks was seeded from the request's own
            # system/user messages, so every benign non-RAG answer scored
            # grounding≈0 (the answer necessarily introduces new tokens absent
            # from the question) and was rewritten into a "cannot verify"
            # refusal. Populate context ONLY from real RAG retrieval; with no
            # retrieval the list stays empty and score_hallucination() takes
            # its safe no-context branch (grounding_score=1.0).
            context_chunks: list[str] = []
            # RAG context binding: enrich grounding with retrieved documents
            rag_context_id = request.headers.get("X-ZeroShield-RAG-Context-ID", "")
            if rag_context_id and REDIS_CLIENT is not None:
                try:
                    import json as _json_ctx
                    raw = await REDIS_CLIENT.get(rag_context_id)
                    if raw:
                        rag_chunks = _json_ctx.loads(raw if isinstance(raw, str) else raw.decode())
                        context_chunks.extend(rag_chunks)
                except Exception:
                    pass  # fail-open: context binding is best-effort
            output_verdict = await OUTPUT_GUARD.inspect(
                _og_scan_text,
                context_chunks=context_chunks,
                org_config=(CONFIG_SYNC.get_config(org_slug) if (CONFIG_SYNC is not None and org_slug) else None),
                org_slug=org_slug or "",
            )
            # M11: tier-2 OUTPUT guard outage → the response was passed UNSCANNED
            # (fail-open by design). Make it VISIBLE: emit an operator telemetry
            # signal here and surface output_scan_degraded=true in the client
            # zeroshield metadata below, so a silent guard outage is observable.
            _output_scan_degraded = bool(getattr(output_verdict, "scan_degraded", False))
            if _output_scan_degraded:
                _emit_telemetry(
                    status_code=200,
                    event_type="output_scan_degraded",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                    action="allow",
                    threat_type="scanner_degraded",
                    pipeline_stage="generator",
                    metadata={"detail": "Tier-2 output guard model unavailable — output passed UNSCANNED", "module": "1.7", "module_id": "1.7"},
                )
            # Capture raw output before any redaction for pipeline visibility
            _raw_model_output = response_text[:500] if response_text else ""
            # §1.7 incident-logging control: when output_incident_logging_enabled is
            # false, non-blocking output-guard actions (redact/flag/rewrite) skip
            # telemetry + audit. Hard blocks always log (and the control-plane
            # compliance floor forces this on under strict frameworks).
            _output_incident_logging = bool(org_config.get("output_incident_logging_enabled", True))

            def _emit_output_incident_telemetry(**_tel_kwargs):
                if _output_incident_logging:
                    _emit_telemetry(**_tel_kwargs)

            def _audit_output_incident(**_audit_kwargs):
                if _output_incident_logging:
                    _audit_fire_and_forget(**_audit_kwargs)

            if output_verdict.action == "block":
                METRICS["blocked"] += 1
                # Record risk event for output guard block
                if REDIS_CLIENT is not None:
                    from model_state import record_risk_event, check_auto_isolate
                    composite = await record_risk_event(REDIS_CLIENT, body.get("model", ""), org_slug or "default", getattr(output_verdict, 'confidence', 0.90), "output_guard")
                    ms_key = f"model_state:{org_slug or 'default'}:{body.get('model', '')}"
                    ms_raw = await REDIS_CLIENT.get(ms_key)
                    ms_threshold = 80.0
                    if ms_raw:
                        try:
                            ms_data = json.loads(ms_raw if isinstance(ms_raw, str) else ms_raw.decode())
                            ms_threshold = float(ms_data.get("threshold", 80.0))
                        except (json.JSONDecodeError, TypeError):
                            pass
                    await check_auto_isolate(REDIS_CLIENT, body.get("model", ""), org_slug or "default", composite, ms_threshold)
                _emit_telemetry(
                    status_code=403,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="block",
                    risk_score=getattr(output_verdict, 'confidence', 0.90),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={
                        "detail": output_verdict.detail,
                        "response_snippet": _raw_model_output,
                        "raw_output": _raw_model_output,
                        "sanitized_output": "[BLOCKED]",
                        "guardrail_reasoning": output_verdict.detail,
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        **_telemetry_owasp_metadata(output_verdict.threat_type),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="block",
                    rule_code=f"output_guard_{output_verdict.threat_type}",
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "score": getattr(output_verdict, "confidence", 0.9),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                    },
                )
                return _build_block_response(
                    403,
                    "output_blocked",
                    _build_zeroshield_metadata(
                        action="block",
                        reason=f"Output guard detected {output_verdict.threat_type} in LLM response.",
                        detection_tier="output_guard",
                        threat_type=output_verdict.threat_type,
                        confidence=getattr(output_verdict, "confidence", 0.9),
                        matched_patterns=getattr(output_verdict, "matched_patterns", []),
                        compliance_tags=getattr(output_verdict, "compliance_tags", []),
                        original_prompt=prompt,
                        detail=output_verdict.detail,
                        processing_time_ms=elapsed_ms,
                        security_incident=True,
                    ),
                    stage_metrics=stage_metrics,
                    prompt=prompt,
                    route_metadata=route_metadata,
                    requested_model=body.get("model", ""),
                    scan_verdict=scan_verdict,
                    output_scan_verdict=output_verdict,
                )
            if output_verdict.action == "redact":
                LOG.info("Output redaction triggered (type=%s, user=%s)", output_verdict.threat_type, user_id)
                redacted_response = _sanitize_output_for_verdict(response_text, output_verdict)
                # Telemetry/enforcement honesty: if the regex redactor produced no
                # change (a semantic tier-2 verdict with no matching static
                # pattern), the output is delivered verbatim — report "flag", not
                # a phantom "redact", on both the client enforcement envelope and
                # the 1.7 telemetry stream.
                _redact_changed = redacted_response != response_text
                _oact = "redact" if _redact_changed else "flag"
                _set_completion_response_text(llm_resp, redacted_response)
                response_text = redacted_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": _oact,
                        "reason": (
                            f"Output guard redacted {output_verdict.threat_type or 'unsafe'} content before delivery."
                            if _redact_changed
                            else f"Output guard flagged {output_verdict.threat_type or 'unsafe'} content (no maskable pattern matched; delivered unchanged)."
                        ),
                        "detail": output_verdict.detail,
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.70),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "redacted_response": redacted_response,
                    },
                )
                _emit_output_incident_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action=_oact,
                    risk_score=getattr(output_verdict, 'confidence', 0.70),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={
                        **_output_guard_telemetry_meta(
                            output_verdict,
                            raw_output=_raw_model_output,
                            sanitized_output=response_text[:500] if response_text else "",
                        ),
                        **_telemetry_owasp_metadata(output_verdict.threat_type),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                _audit_output_incident(
                    org_slug=org_slug or "",
                    # H-01 FIX: the executed action on this path is redact (or flag
                    # when nothing maskable matched, per _oact) — NOT rewrite. The
                    # decision was hardcoded "rewrite", mislabeling every output-guard
                    # redact in the audit trail and contradicting both the telemetry
                    # action (_oact) and the rule_code (..._redact_...). Use the honest
                    # executed action.
                    decision=_oact,
                    rule_code=f"output_guard_redact_{output_verdict.threat_type}",
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "score": getattr(output_verdict, "confidence", 0.7),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                    },
                )
            if output_verdict.action == "rewrite":
                LOG.info("Output rewrite triggered (type=%s, user=%s)", output_verdict.threat_type, user_id)
                rewritten_response, _rw_reinferred = await _rewrite_output_response_text_via_router(
                    output_verdict.threat_type, output_verdict.detail, response_text, body
                )
                _set_completion_response_text(llm_resp, rewritten_response)
                response_text = rewritten_response
                # H-07 FIX: re-validate the REWRITTEN output ONCE. A rewrite is a
                # fresh model inference that can re-introduce — or NEWLY leak — an
                # internal IP / hostname / file path / secret the original detector
                # never saw. The rewriter's internal redact_all only covers
                # PII/secret/credential (NOT ip_leakage/policy/hallucination) and
                # inspect() was never re-run, so a poisoned rewrite shipped unscanned.
                # Re-inspect once; if the rewrite is STILL unsafe, do NOT ship it —
                # fall back to the static canned safe message (guaranteed leak-free).
                # Single pass (never a 2nd rewrite) => no inference loop; fail-closed.
                try:
                    _rw_recheck = await OUTPUT_GUARD.inspect(
                        rewritten_response,
                        context_chunks=context_chunks,
                        org_config=org_config,
                        org_slug=org_slug or "",
                    )
                    _rw_unsafe = _rw_recheck is not None and str(
                        getattr(_rw_recheck, "action", "allow")
                    ) in ("block", "redact", "rewrite")
                except Exception:  # noqa: BLE001 - never crash the response; fail CLOSED
                    _rw_unsafe = True
                if _rw_unsafe:
                    LOG.warning(
                        "Output rewrite re-validation FAILED (rewrite still unsafe: type=%s) — "
                        "replacing with static canned safe message",
                        output_verdict.threat_type,
                    )
                    rewritten_response = _rewrite_output_response_text(
                        output_verdict.threat_type, output_verdict.detail
                    )
                    _set_completion_response_text(llm_resp, rewritten_response)
                    response_text = rewritten_response
                    _rw_reinferred = False
                # R3: surface whether the rewrite was a genuine model re-inference
                # or a degraded static canned replacement.
                _rw_degraded = not _rw_reinferred
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "rewrite",
                        "reason": f"Output guard rewrote {output_verdict.threat_type or 'unsafe'} content before delivery.",
                        "detail": output_verdict.detail,
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.65),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "rewritten_response": rewritten_response,
                        "rewrite_reinferred": _rw_reinferred,
                        "rewrite_degraded": _rw_degraded,
                    },
                )
                _emit_output_incident_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="rewrite",
                    risk_score=getattr(output_verdict, 'confidence', 0.65),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={
                        "detail": output_verdict.detail,
                        "response_snippet": _raw_model_output,
                        "raw_output": _raw_model_output,
                        "sanitized_output": response_text[:500] if response_text else "",
                        "guardrail_reasoning": output_verdict.detail,
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "rewrite_reinferred": _rw_reinferred,
                        "rewrite_degraded": _rw_degraded,
                        **_telemetry_owasp_metadata(output_verdict.threat_type),
                    },
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                _audit_output_incident(
                    org_slug=org_slug or "",
                    decision="rewrite",
                    rule_code=f"output_guard_rewrite_{output_verdict.threat_type}",
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "score": getattr(output_verdict, "confidence", 0.65),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                    },
                )
            if output_verdict.action == "flag":
                if output_verdict.threat_type == "hallucination":
                    hallucination_flagged = True
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "flag",
                        "reason": f"Output guard flagged {output_verdict.threat_type or 'unsafe'} content for review.",
                        "detail": output_verdict.detail,
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.60),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "review_required": True,
                        "factuality_warning": output_verdict.threat_type == "hallucination",
                    },
                )
                _emit_output_incident_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="flag",
                    risk_score=getattr(output_verdict, 'confidence', 0.60),
                    threat_type=output_verdict.threat_type,
                    compliance_tags=output_verdict.compliance_tags,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    metadata={"detail": output_verdict.detail, "response_snippet": _raw_model_output, "raw_output": _raw_model_output, "sanitized_output": response_text[:500] if response_text else "", "guardrail_reasoning": output_verdict.detail, "matched_patterns": getattr(output_verdict, 'matched_patterns', [])},
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )
                # FLAG-AUDIT FIX: block / redact / rewrite each write an
                # _audit_output_incident row, but the flag path only emitted
                # telemetry — so a flagged (review_required) output left NO entry
                # in the Security Incident Log even when incident logging is on,
                # making the most-actionable "needs human review" outputs invisible
                # to the audit/incident surface. Write the incident here too (the
                # wrapper is itself gated on output_incident_logging_enabled).
                _audit_output_incident(
                    org_slug=org_slug or "",
                    decision="flag",
                    rule_code=f"output_guard_flag_{output_verdict.threat_type}",
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "score": getattr(output_verdict, "confidence", 0.60),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "review_required": True,
                    },
                )
        elif INPUT_SCANNER is not None and response_text and org_config.get("output_scan_enabled", True):
            output_verdict = await INPUT_SCANNER.scan_output(response_text)
            if output_verdict.action == "flag" and output_verdict.threat_type in ("pii", "secret"):
                LOG.info("PII/secret detected in LLM response, redacting (user=%s)", user_id)
                redacted_response = INPUT_SCANNER.redact_pii(response_text)
                _set_completion_response_text(llm_resp, redacted_response)
                response_text = redacted_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "redact",
                        "reason": f"Output scanner redacted {output_verdict.threat_type} content before delivery.",
                        "detail": f"Output redacted: {output_verdict.detail}",
                        "detection_tier": "output_guard",
                        "threat_type": output_verdict.threat_type,
                        "confidence": getattr(output_verdict, "confidence", 0.60),
                        "matched_patterns": getattr(output_verdict, "matched_patterns", []),
                        "compliance_tags": getattr(output_verdict, "compliance_tags", []),
                        "redacted_response": redacted_response,
                    },
                )
                _emit_telemetry(
                    status_code=200,
                    event_type="output_guard",
                    model=body.get("model", ""),
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    action="redact",
                    risk_score=getattr(output_verdict, 'confidence', 0.60),
                    threat_type=output_verdict.threat_type,
                    pipeline_stage="generator",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    compliance_tags=getattr(output_verdict, 'compliance_tags', []),
                    metadata={"detail": f"Output redacted: {output_verdict.detail}", "response_snippet": response_text[:500] if response_text else "", "matched_patterns": getattr(output_verdict, 'matched_patterns', [])},
                    prompt_snippet=_prompt_snippet,
                    endpoint_id=endpoint_id,
                )

        # ── Await deep scan result if running ──
        if deep_scan_task is not None:
            try:
                scan_code, scan_resp = await deep_scan_task
                if scan_code == 200 and isinstance(scan_resp, dict):
                    scan_action = scan_resp.get("recommended_action") or ""
                    if scan_action in ("block_immediately", "block_and_alert"):
                        # Check if the deep scan threats are injection-related
                        _deep_threats = scan_resp.get("threats_detected") or []
                        _deep_injection_types = {"prompt_injection", "jailbreak", "goal_hijacking", "tool_overreach"}
                        _deep_has_injection = any(
                            t.get("type") in _deep_injection_types
                            for t in _deep_threats if isinstance(t, dict)
                        )
                        # Respect scan_block_on_injection for injection-type deep scan blocks
                        if _deep_has_injection and not org_config.get("scan_block_on_injection", True):
                            LOG.info(
                                "Backend deep scan detected injection but scan_block_on_injection=False (user=%s)",
                                user_id,
                            )
                        elif enforcement_mode != "block":
                            LOG.warning(
                                "MONITOR: Backend deep scan would block (action=%s, user=%s)",
                                scan_action, user_id,
                            )
                        else:
                            LOG.warning("Backend deep scan blocked response (action=%s, user=%s)", scan_action, user_id)
                            METRICS["blocked"] += 1
                            elapsed_ms = (time.perf_counter() - start) * 1000
                            return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                                action="block",
                                reason="Response blocked by backend deep security scan.",
                                detection_tier="policy",
                                original_prompt=prompt,
                                processing_time_ms=elapsed_ms,
                                security_incident=True,
                            ))
            except Exception:
                LOG.exception("Backend deep scan failed, continuing with local scan results")

        if response_text and (_policy_cache_ready or AGENT_ID) and org_config.get("output_policy_enabled", True):
            _, resp_check = await asyncio.to_thread(
                _policy_check_cached,
                effective_prompt,
                response_text,
                user_id,
                endpoint_id,
                agent_data,
                project_id,
                risk_score,
                requested_model,
                org_slug,
                actor=_build_policy_actor(auth_ctx, user_id),
            )
            resp_action = _normalize_output_action(resp_check.get("action") if resp_check else "allow")
            # §1.7 output policy control: the operator-configured output_policy_action
            # is authoritative for the output path. When the policy engine reports a
            # violation, route it through the configured action (block/redact/rewrite/
            # flag/allow). action="allow" lets operators monitor without enforcing.
            # Monitor normalizes to flag — do not escalate flag/monitor to block.
            if resp_check and resp_action not in ("allow", "flag"):
                resp_action = _normalize_output_action(org_config.get("output_policy_action", "block"))
            if resp_check and resp_action == "block":
                METRICS["blocked"] += 1
                elapsed_ms = (time.perf_counter() - start) * 1000
                resp_categories = resp_check.get("matched_policy_categories") or []
                resp_threat_type = _category_to_threat_type(resp_categories[0]) if resp_categories else "policy_violation"
                if TELEMETRY is not None and not resp_check.get("event_id"):
                    _emit_telemetry(
                        status_code=200,
                        event_type="output_scan",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="block",
                        risk_score=0.85,
                        threat_type=resp_threat_type,
                        metadata={
                            "detail": resp_check.get("message") or "Post-LLM policy block",
                            "matched_policies": resp_check.get("matched_policies") or [],
                            "matched_rules": resp_check.get("matched_rules") or [],
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="block",
                    rule_code=((resp_check.get("matched_rules") or ["policy_response_block"])[0]),
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "matched_rules": resp_check.get("matched_rules") or [],
                        "matched_policies": resp_check.get("matched_policies") or [],
                    },
                )
                return _build_block_response(403, "content_blocked", _build_zeroshield_metadata(
                    action="block",
                    reason=resp_check.get("message") or "Response blocked by post-LLM policy check.",
                    detail=resp_check.get("message") or "Response blocked by post-LLM policy check.",
                    detection_tier="policy",
                    threat_type=resp_threat_type,
                    matched_patterns=resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                    original_prompt=prompt,
                    processing_time_ms=elapsed_ms,
                    security_incident=True,
                ))
            if resp_check and resp_action == "redact" and resp_check.get("redacted_response"):
                redacted_response = resp_check["redacted_response"]
                _set_completion_response_text(llm_resp, redacted_response)
                response_text = redacted_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "redact",
                        "reason": resp_check.get("message") or "Response redacted by post-LLM policy check.",
                        "detail": resp_check.get("message") or "Response redacted by post-LLM policy check.",
                        "detection_tier": "policy",
                        "threat_type": "policy_violation",
                        "confidence": 0.8,
                        "matched_patterns": resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                        "redacted_response": redacted_response,
                    },
                )
            elif resp_check and resp_action == "rewrite":
                resp_categories = resp_check.get("matched_policy_categories") or []
                resp_threat_type = _category_to_threat_type(resp_categories[0]) if resp_categories else "policy_violation"
                rewritten_response, _rw_reinferred = await _rewrite_output_response_text_via_router(
                    resp_threat_type, resp_check.get("message"), response_text, body
                )
                _set_completion_response_text(llm_resp, rewritten_response)
                response_text = rewritten_response
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "rewrite",
                        "reason": resp_check.get("message") or "Response rewritten by post-LLM policy check.",
                        "detail": resp_check.get("message") or "Response rewritten by post-LLM policy check.",
                        "detection_tier": "policy",
                        "threat_type": resp_threat_type,
                        "confidence": 0.65,
                        "matched_patterns": resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                        "rewritten_response": rewritten_response,
                        "rewrite_reinferred": _rw_reinferred,
                        "rewrite_degraded": not _rw_reinferred,
                    },
                )
                if TELEMETRY is not None and not resp_check.get("event_id"):
                    _emit_telemetry(
                        status_code=200,
                        event_type="output_scan",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="rewrite",
                        risk_score=0.65,
                        threat_type=resp_threat_type,
                        metadata={
                            "detail": resp_check.get("message") or "Post-LLM policy rewrite",
                            "matched_policies": resp_check.get("matched_policies") or [],
                            "matched_rules": resp_check.get("matched_rules") or [],
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )
                _audit_fire_and_forget(
                    org_slug=org_slug or "",
                    decision="rewrite",
                    rule_code=((resp_check.get("matched_rules") or ["policy_response_rewrite"])[0]),
                    metadata={
                        "input_bytes": len((response_text or "").encode("utf-8")),
                        "model_id": body.get("model", ""),
                        "matched_rules": resp_check.get("matched_rules") or [],
                        "matched_policies": resp_check.get("matched_policies") or [],
                    },
                )
            elif resp_check and resp_action == "flag":
                resp_categories = resp_check.get("matched_policy_categories") or []
                resp_threat_type = _category_to_threat_type(resp_categories[0]) if resp_categories else "policy_violation"
                output_enforcement = _merge_output_enforcement_state(
                    output_enforcement,
                    {
                        "action": "flag",
                        "reason": resp_check.get("message") or "Response flagged by post-LLM policy check.",
                        "detail": resp_check.get("message") or "Response flagged by post-LLM policy check.",
                        "detection_tier": "policy",
                        "threat_type": resp_threat_type,
                        "confidence": 0.55,
                        "matched_patterns": resp_check.get("matched_rules") or resp_check.get("matched_policies") or [],
                        "review_required": True,
                    },
                )
                if TELEMETRY is not None and not resp_check.get("event_id"):
                    _emit_telemetry(
                        status_code=200,
                        event_type="output_scan",
                        model=body.get("model", ""),
                        user_id=user_id,
                        project_id=str(project_id or ""),
                        key_prefix=auth_ctx.prefix if auth_ctx else "",
                        action="flag",
                        risk_score=0.55,
                        threat_type=resp_threat_type,
                        metadata={
                            "detail": resp_check.get("message") or "Post-LLM policy flag",
                            "matched_policies": resp_check.get("matched_policies") or [],
                            "matched_rules": resp_check.get("matched_rules") or [],
                        },
                        prompt_snippet=_prompt_snippet,
                        endpoint_id=endpoint_id,
                    )

        # Record token usage for rate limiting and observability
        usage = LLM_ROUTER.extract_usage(llm_resp)
        if usage:
            LOG.info(
                "Token usage: %s (model=%s, user=%s, project=%s)",
                usage, body.get("model"), user_id, project_id,
            )
            # R#10: record_usage reconciles the TPM bucket by the delta
            # (actual - estimated). Two bugs caused the counter to drift
            # NEGATIVE on oversized bodies:
            #   1. estimated_tokens was OMITTED, so it defaulted to ~20 instead
            #      of the value actually pre-charged by check_rate_limit. Pass
            #      the real estimate so the delta reconciles correctly.
            #   2. the reconcile ran even when NO pre-charge happened (per-key
            #      check_rate_limit only runs when rate_limit_tpm is set); a
            #      negative delta then INCRBY'd an un-charged bucket below 0.
            #      Only reconcile a bucket we actually pre-charged.
            if RATE_LIMITER is not None and auth_ctx is not None:
                _actual_tokens = max(0, int(usage.get("total_tokens", 0) or 0))
                _pre_charged = max(0, int(estimated_request_tokens or 0))
                if rate_limit_tpm:
                    await RATE_LIMITER.record_usage(
                        auth_ctx.key_hash,
                        _actual_tokens,
                        estimated_tokens=_pre_charged,
                    )
                # G11: reconcile the ORG TPM bucket too. The per-key reconcile above
                # was wired but record_org_usage was only called on the STREAM path,
                # so the non-stream org window kept the prompt-only pre-charge and
                # never corrected to actual completion tokens — letting orgs exceed
                # a configured org_tpm_limit. Mirror stream_orchestration; only
                # reconcile a bucket we actually pre-charged (org_tpm_limit > 0).
                _org_tpm = int(org_config.get("org_tpm_limit", 0) or 0)
                if _org_tpm > 0 and org_slug:
                    try:
                        await RATE_LIMITER.record_org_usage(org_slug, _actual_tokens, _pre_charged)
                    except Exception as exc:  # noqa: BLE001 - best-effort, fail-open
                        LOG.warning("Non-stream org TPM finalize failed: %s", exc)

        METRICS["allowed"] += 1
        elapsed_ms = (time.perf_counter() - start) * 1000
        _tel_action = "redact" if redacted_prompt is not None else "allow"
        _tel_threat = (scan_verdict.threat_type if scan_verdict and redacted_prompt is not None else "")
        _tel_risk = (scan_verdict.confidence if scan_verdict and redacted_prompt is not None else 0.0)
        # Request telemetry is emitted AFTER pipeline_trace is built (below) so Scan
        # Detail / Activity Preview receive the full 9-stage trace + I/O, not just
        # stage_metrics_ms.
        if tier2_execution_mode == "async_post_llm":
            await enqueue_job(
                job_type="chat_postprocess",
                request_id=request.headers.get("X-Request-ID", f"zs-post-{_uuid.uuid4().hex[:12]}"),
                org_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                payload={
                    "request_id": request.headers.get("X-Request-ID", ""),
                    "organization_id": getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                    "user_id": user_id,
                    "project_id": str(project_id or ""),
                    "model": body.get("model", ""),
                    "action": _tel_action,
                    "threat_type": _tel_threat,
                    "response_snippet": (response_text or "")[:500],
                },
            )
        response_headers_final: dict[str, str] = {}
        elapsed_ms = (time.perf_counter() - start) * 1000
        if isinstance(llm_resp, dict):
            if output_enforcement is not None:
                llm_resp["zeroshield"] = _build_zeroshield_metadata(
                    action=output_enforcement.get("action", "allow"),
                    reason=output_enforcement.get("reason") or "Output checks passed.",
                    detection_tier=output_enforcement.get("detection_tier", "output_guard"),
                    threat_type=output_enforcement.get("threat_type", "none"),
                    confidence=float(output_enforcement.get("confidence") or 0.0),
                    matched_patterns=output_enforcement.get("matched_patterns") or [],
                    compliance_tags=output_enforcement.get("compliance_tags"),
                    original_prompt=prompt,
                    redacted_prompt=redacted_prompt,
                    redacted_response=output_enforcement.get("redacted_response"),
                    rewritten_response=output_enforcement.get("rewritten_response"),
                    detail=output_enforcement.get("detail"),
                    processing_time_ms=elapsed_ms,
                    review_required=bool(output_enforcement.get("review_required")),
                    security_incident=bool(output_enforcement.get("security_incident")),
                    factuality_warning=bool(output_enforcement.get("factuality_warning")) or hallucination_flagged,
                    routing=route_metadata,
                )
                response_headers_final["X-ZeroShield-Action"] = output_enforcement.get("action", "allow")
                if output_enforcement.get("matched_patterns"):
                    response_headers_final["X-ZeroShield-Matched-Patterns"] = ",".join(output_enforcement.get("matched_patterns") or [])
                if output_enforcement.get("action") == "redact" and output_enforcement.get("redacted_response") is not None:
                    response_headers_final["X-ZeroShield-Redacted-Types"] = ",".join(output_enforcement.get("matched_patterns") or [])
                if output_enforcement.get("review_required"):
                    response_headers_final["X-ZeroShield-Review-Required"] = "true"
            elif redacted_prompt is not None:
                # Attribute the redaction to the stage that actually performed it.
                # Policy redaction runs FIRST (deterministic, masks all matched
                # rule patterns); Tier-2 then scans the already-redacted text. If
                # policy did the masking and Tier-2 added nothing, report policy
                # (rules + detection_tier=policy) instead of a now-clean Tier-2
                # verdict (which would yield "redact" with empty matched_patterns).
                _policy_rules = check_resp.get("matched_rules") or []
                _policy_did = bool(policy_redacted_prompt) and policy_redacted_prompt != prompt
                _tier2_did = bool(scan_verdict) and redacted_prompt != (policy_redacted_prompt or prompt)
                _sv_patterns = list(scan_verdict.matched_patterns) if (scan_verdict and _tier2_did) else []
                if _policy_did and not _tier2_did:
                    _red_tier = "policy"
                    _red_threat = (
                        scan_verdict.threat_type
                        if scan_verdict and scan_verdict.threat_type not in ("none", "", None)
                        else "pii"
                    )
                    _red_patterns = _policy_rules
                    _red_reason = (
                        "Sensitive data redacted by org policy ("
                        + ", ".join(_policy_rules[:5])
                        + ") before forwarding to the LLM."
                        if _policy_rules
                        else "Sensitive data redacted by org policy before forwarding to the LLM."
                    )
                    _red_detail = _red_reason
                    _red_conf = scan_verdict.confidence if scan_verdict else 0.99
                else:
                    _red_tier = scan_verdict.tier if scan_verdict else "tier_1"
                    _red_threat = scan_verdict.threat_type if scan_verdict else "pii"
                    _red_patterns = _sv_patterns
                    _red_reason = (
                        f"PII detected in prompt ({', '.join(_sv_patterns)}). "
                        "Redacted before forwarding to LLM."
                    )
                    _red_detail = scan_verdict.detail if scan_verdict else ""
                    _red_conf = scan_verdict.confidence if scan_verdict else 0.85
                llm_resp["zeroshield"] = _build_zeroshield_metadata(
                    action="redact",
                    reason=_red_reason,
                    detection_tier=_red_tier,
                    threat_type=_red_threat,
                    confidence=_red_conf,
                    matched_patterns=_red_patterns,
                    original_prompt=prompt,
                    redacted_prompt=redacted_prompt,
                    detail=_red_detail,
                    processing_time_ms=elapsed_ms,
                    routing=route_metadata,
                )
                response_headers_final["X-ZeroShield-Action"] = "redact"
                response_headers_final["X-ZeroShield-Redacted-Types"] = ",".join(_red_patterns)
            else:
                zs_action, zs_reason, zs_threat, zs_conf, zs_patterns, zs_detail = _resolve_success_metadata_from_verdict(scan_verdict)
                from pipeline_trace import enrich_zeroshield_from_verdict

                llm_resp["zeroshield"] = enrich_zeroshield_from_verdict(
                    _build_zeroshield_metadata(
                        action=zs_action,
                        reason=zs_reason,
                        detection_tier=scan_verdict.tier if scan_verdict else "none",
                        threat_type=zs_threat,
                        confidence=zs_conf,
                        matched_patterns=zs_patterns,
                        original_prompt=prompt,
                        detail=zs_detail,
                        processing_time_ms=elapsed_ms,
                        factuality_warning=hallucination_flagged,
                        routing=route_metadata,
                    ),
                    scan_verdict=scan_verdict,
                    final_action=zs_action,
                )
                if zs_action == "flag":
                    response_headers_final["X-ZeroShield-Action"] = "flag"
            if hallucination_flagged:
                response_headers_final["X-ZeroShield-Factuality-Warning"] = "true"
                if isinstance(llm_resp.get("zeroshield"), dict):
                    llm_resp["zeroshield"]["factuality_warning"] = True
            # Carry the REAL matched policy/rule names from the deterministic policy
            # engine onto the allow/monitor zeroshield metadata so the pipeline trace
            # policy stage shows policy names (not scanner prompt substrings).
            if isinstance(llm_resp.get("zeroshield"), dict):
                llm_resp["zeroshield"]["matched_policy_names"] = (
                    check_resp.get("matched_policy_names")
                    or check_resp.get("matched_policies")
                    or []
                )
                llm_resp["zeroshield"]["matched_rule_names"] = check_resp.get("matched_rules") or []
            if isinstance(llm_resp.get("zeroshield"), dict) and isinstance(route_metadata, dict):
                llm_resp["zeroshield"]["routing_enabled"] = route_metadata.get("routing_enabled", True)
                llm_resp["zeroshield"]["routing_override"] = route_metadata.get("routing_override")
                llm_resp["zeroshield"]["routing_score"] = route_metadata.get("routing_score")
                llm_resp["zeroshield"]["decision_factors"] = route_metadata.get("decision_factors", [])
                llm_resp["zeroshield"]["weights"] = route_metadata.get("weights", {})
                llm_resp["zeroshield"]["fallback_chain"] = route_metadata.get("fallback_chain", [])
                llm_resp["zeroshield"]["candidate_count"] = route_metadata.get("candidate_count")
                llm_resp["zeroshield"]["data_sensitivity"] = route_metadata.get("data_sensitivity")
                llm_resp["zeroshield"]["compliance_requirements"] = route_metadata.get("compliance_requirements", [])
                llm_resp["zeroshield"]["request_risk_score"] = route_metadata.get("request_risk_score")
                llm_resp["zeroshield"]["estimated_tokens"] = route_metadata.get("estimated_tokens")
                llm_resp["zeroshield"]["token_budget_tpm"] = route_metadata.get("token_budget_tpm")
                llm_resp["zeroshield"]["latency_budget_ms"] = route_metadata.get("latency_budget_ms")
            # M11: surface a tier-2 OUTPUT guard outage to the API caller. When
            # true the response was delivered WITHOUT a confident output scan
            # (fail-open), so the client knows the output was not guarded.
            if isinstance(llm_resp.get("zeroshield"), dict) and _output_scan_degraded:
                llm_resp["zeroshield"]["output_scan_degraded"] = True
            if route_selection is not None:
                response_headers_final["X-ZeroShield-Original-Model"] = route_selection.requested_model or "auto"
                response_headers_final["X-ZeroShield-Routed-Model"] = route_selection.model_name
                response_headers_final["X-ZeroShield-Routing-Source"] = route_selection.decision_source
                response_headers_final["X-ZeroShield-Rerouted"] = "true" if route_selection.requested_model and route_selection.requested_model != "auto" and route_selection.model_name != route_selection.requested_model else "false"
                response_headers_final["X-ZeroShield-Routing-Reason"] = route_selection.reason[:180]
                if route_selection.policy_summary and bool(
                    org_config.get(
                        "stream_emit_debug_headers",
                        CONFIG.get("stream_emit_debug_headers", False),
                    )
                ):
                    response_headers_final["X-ZeroShield-Routing-Policy-Summary"] = (
                        route_selection.policy_summary[:180]
                    )
                if isinstance(llm_resp.get("zeroshield"), dict):
                    llm_resp["zeroshield"]["selected_model"] = route_selection.model_name
                    llm_resp["zeroshield"]["original_model"] = route_selection.requested_model or "auto"
                    llm_resp["zeroshield"]["rerouted"] = bool(route_selection.requested_model and route_selection.requested_model != "auto" and route_selection.model_name != route_selection.requested_model)
                    llm_resp["zeroshield"]["routing_reason"] = route_selection.reason
                    llm_resp["zeroshield"]["decision_source"] = route_selection.decision_source
                    llm_resp["zeroshield"]["policy_summary"] = route_selection.policy_summary
            elif isinstance(route_metadata, dict) and route_metadata.get("rerouted"):
                _iso_original = route_metadata.get("original_model") or "auto"
                _iso_selected = route_metadata.get("selected_model") or body.get("model") or "auto"
                response_headers_final["X-ZeroShield-Original-Model"] = _iso_original
                response_headers_final["X-ZeroShield-Routed-Model"] = _iso_selected
                response_headers_final["X-ZeroShield-Routing-Source"] = route_metadata.get("decision_source") or ""
                response_headers_final["X-ZeroShield-Rerouted"] = "true"
                _iso_reason = str(route_metadata.get("routing_reason") or "")
                if _iso_reason:
                    response_headers_final["X-ZeroShield-Routing-Reason"] = _iso_reason[:180]
                if isinstance(llm_resp.get("zeroshield"), dict):
                    llm_resp["zeroshield"]["selected_model"] = _iso_selected
                    llm_resp["zeroshield"]["original_model"] = _iso_original
                    llm_resp["zeroshield"]["rerouted"] = True
                    llm_resp["zeroshield"]["routing_reason"] = route_metadata.get("routing_reason")
                    llm_resp["zeroshield"]["decision_source"] = route_metadata.get("decision_source")
                    llm_resp["zeroshield"]["policy_summary"] = route_metadata.get("policy_summary")
        
        # Operator pipeline trace (Module 1.1 simulator) — built before zeroshield redaction.
        if isinstance(llm_resp, dict):
            from pipeline_trace import build_pipeline_trace

            _zs_full = llm_resp.get("zeroshield") if isinstance(llm_resp.get("zeroshield"), dict) else {}
            _final = _zs_full.get("action") or "allow"
            llm_resp["pipeline_trace"] = build_pipeline_trace(
                prompt=_redact_trace_text(prompt),
                forwarded_prompt=_redact_trace_text(redacted_prompt or prompt),
                policy_redacted_prompt=policy_redacted_prompt,
                policy_redacted_flag=(bool(policy_redacted_prompt) and policy_redacted_prompt != prompt),
                stage_metrics=stage_metrics,
                final_action=_final,
                blocked_stage="",
                http_status=200,
                scan_verdict=scan_verdict,
                route_metadata=route_metadata,
                zeroshield=_zs_full,
                response_text=_redact_trace_text(response_text or ""),
                requested_model=(
                    (route_metadata or {}).get("original_model")
                    or body.get("model", "")
                ),
                output_scan_verdict=output_verdict,
            )

        # Emit request telemetry with full pipeline_trace + I/O for Activity Preview.
        telemetry_start = time.perf_counter()
        _pt_for_tel = llm_resp.get("pipeline_trace") if isinstance(llm_resp, dict) else None
        _rid_tel = _REQUEST_ID.get("") or request.headers.get("X-Request-ID", "")
        _tel_md: dict[str, Any] = {"stage_metrics_ms": stage_metrics}
        if _pt_for_tel:
            _tel_md["pipeline_trace"] = _pt_for_tel
        if _rid_tel:
            _tel_md["request_id"] = _rid_tel
            _tel_md["incident_id"] = _rid_tel
        if _prompt_snippet:
            _tel_md["prompt_snippet"] = _prompt_snippet[:2000]
            if isinstance(_pt_for_tel, dict):
                _tel_md["prompt_submitted"] = (
                    _pt_for_tel.get("prompt_submitted")
                    or _pt_for_tel.get("forwarded_prompt")
                    or _prompt_snippet[:2000]
                )
            else:
                _tel_md["prompt_submitted"] = _prompt_snippet[:2000]
        if response_text:
            _tel_md["response_snippet"] = (response_text or "")[:2000]
            _tel_md["sanitized_output"] = (response_text or "")[:2000]
        _emit_telemetry(
            event_type="request",
            model=body.get("model", ""),
            user_id=user_id,
            project_id=str(project_id or ""),
            key_prefix=auth_ctx.prefix if auth_ctx else "",
            prompt_snippet=_prompt_snippet,
            endpoint_id=endpoint_id,
            latency_ms=(time.perf_counter() - start) * 1000,
            risk_score=_tel_risk,
            action=_tel_action,
            threat_type=_tel_threat,
            tokens_used=usage or {},
            metadata=_tel_md,
        )
        stage_metrics["telemetry_enqueue_ms"] = round((time.perf_counter() - telemetry_start) * 1000, 2)

        # ── SECURITY FIX: Redact sensitive fields from zeroshield metadata before returning to client ──
        # The zeroshield object contains internal security details that MUST NOT be exposed to clients.
        if isinstance(llm_resp.get("zeroshield"), dict):
            llm_resp["zeroshield"] = _redact_for_client_response(llm_resp["zeroshield"]) or {}

        # MODEL-ID LEAK FIX: litellm sets the top-level `model` to the RAW upstream
        # provider id (e.g. "anthropic/claude-3-5-haiku") — this leaks the platform's
        # BYOK/provider topology AND breaks OpenAI-SDK parity (a stock client expects
        # its requested model echoed back). Rewrite to the client-facing name: the
        # original requested model (or the org-facing routed alias), NEVER the raw
        # upstream id. Same resolution the pipeline_trace already uses above.
        if isinstance(llm_resp, dict):
            _client_facing_model = (route_metadata or {}).get("original_model") or body.get("model") or ""
            if _client_facing_model:
                llm_resp["model"] = _client_facing_model
            # TOPOLOGY LEAK FIX (caught live in the simulator response during Phase-7
            # frontend validation): strip litellm/OpenRouter passthrough fields that
            # leak the upstream PROVIDER (e.g. "Amazon Bedrock") and the platform's
            # COST BASIS / margin. None are part of the OpenAI response schema and
            # none are consumed by any client (verified) — so removing them improves
            # both OpenAI parity and topology hygiene.
            llm_resp.pop("provider", None)
            _usage = llm_resp.get("usage")
            if isinstance(_usage, dict):
                for _k in ("cost", "cost_details", "is_byok"):
                    _usage.pop(_k, None)
            # OAS-LEAK (class): comprehensively strip upstream-internal passthrough that
            # leaks the provider family/topology — the raw upstream generation id, the
            # OpenRouter/Perplexity `citations`, and provider_specific_fields (carrying
            # reasoning_details[].format + native_finish_reason) at choice + message level.
            _scrub_upstream_passthrough(llm_resp, _rid_tel)

        return JSONResponse(content=llm_resp, headers=_latin1_safe_headers(response_headers_final))

    except Exception as exc:
        LOG.exception("Unhandled error in proxy_chat")
        chat_outcome = "error"
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An internal gateway error occurred.",
                "code": "gateway_internal_error",
            },
        )

    finally:
        METRICS["active_connections"] -= 1
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000
        try:
            from .metrics import record_chat_completion as _prom_chat_completion
        except ImportError:
            from metrics import record_chat_completion as _prom_chat_completion  # type: ignore[no-redef]
        try:
            _prom_chat_completion(
                org_slug,
                chat_outcome,
                time.perf_counter() - start,
                stage_metrics,
            )
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# OpenAI Responses API (POST /v1/responses, client.responses.create)
#
# Implemented as a FORMAT ADAPTER over the proven chat-completions pipeline: a
# Responses request is translated to a chat request, run through the UNCHANGED
# ``proxy_chat`` enforcement chain (auth -> rate-limit -> kill-switch -> policy
# -> Tier-1/Tier-2 scan -> output-guard -> redaction -> telemetry), then the
# chat result is translated back into a Responses object. The firewall is
# therefore INHERITED, never forked (master plan §3 — the keystone decision).
# ════════════════════════════════════════════════════════════════════════════
from starlette.requests import Request as _StarletteRequest  # noqa: E402
try:
    from .responses_adapters import (  # noqa: E402
        generate_openai_id as _gen_oai_id,
        build_openai_error as _build_oai_error,
        coerce_chat_error_to_openai as _coerce_chat_error,
        responses_to_chat as _responses_to_chat,
        chat_completion_to_responses as _chat_to_responses,
        extract_assistant_messages_for_replay as _replay_msgs,
        find_unsupported_input_part as _find_unsupported_input_part,
    )
    from .responses_store import ResponseStore as _ResponseStore  # noqa: E402
except ImportError:
    from responses_adapters import (  # noqa: E402
        generate_openai_id as _gen_oai_id,
        build_openai_error as _build_oai_error,
        coerce_chat_error_to_openai as _coerce_chat_error,
        responses_to_chat as _responses_to_chat,
        chat_completion_to_responses as _chat_to_responses,
        extract_assistant_messages_for_replay as _replay_msgs,
        find_unsupported_input_part as _find_unsupported_input_part,
    )
    from responses_store import ResponseStore as _ResponseStore  # noqa: E402


async def _dispatch_chat_internally(request, chat_body: dict, x_user_id, x_endpoint_id, x_agent_data):
    """Run a translated chat request through the unchanged ``proxy_chat`` handler
    by synthesizing a Request that carries the new body but the ORIGINAL request's
    scope/state (so middleware-set ``auth_context`` and headers flow through)."""
    payload = json.dumps(chat_body).encode("utf-8")
    scope = dict(request.scope)
    scope["path"] = "/v1/chat/completions"
    scope["raw_path"] = b"/v1/chat/completions"
    scope.setdefault("state", request.scope.get("state", {}))

    async def _receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    internal_req = _StarletteRequest(scope, _receive)
    return await proxy_chat(internal_req, x_user_id=x_user_id,
                            x_endpoint_id=x_endpoint_id, x_agent_data=x_agent_data)


def _responses_sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _translate_chat_stream_to_responses(chat_stream, response_id: str, model: str,
                                              created_at: int, store_ctx: dict):
    """Translate the chat-completions SSE stream into Responses typed events.
    Emits response.created -> output_item.added -> output_text.delta* ->
    output_text.done -> output_item.done -> response.completed, plus the terminal
    ``[DONE]``. Mid-stream chat error frames (e.g. output guard ``output_blocked``)
    emit ``response.failed`` instead of a false ``response.completed``. The chat
    terminal zeroshield trace frame (M-51) is folded into the final object."""
    item_id = _gen_oai_id("message")
    seq = 0
    accumulated = []
    # Streamed function/tool calls: accumulate per upstream delta.tool_calls index.
    # Each entry tracks the Responses output item plus its incremental arguments so the
    # call surfaces as a typed function_call item (parity with the non-stream path).
    tool_calls: dict[int, dict] = {}
    tool_order: list[int] = []
    final_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    zeroshield = None
    finish_reason = None
    stream_error = None
    base_resp = {
        "id": response_id, "object": "response", "created_at": created_at,
        "model": model, "status": "in_progress", "output": [],
    }
    yield _responses_sse("response.created", {"type": "response.created", "response": dict(base_resp, status="in_progress")})
    yield _responses_sse("response.in_progress", {"type": "response.in_progress", "response": dict(base_resp, status="in_progress")})
    yield _responses_sse("response.output_item.added", {
        "type": "response.output_item.added", "output_index": 0,
        "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
    started_text = False

    buf = ""
    async for raw in chat_stream:
        chunk = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        buf += chunk
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            line = frame.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                continue
            try:
                obj = json.loads(data_str)
            except (TypeError, ValueError):
                continue
            if isinstance(obj.get("zeroshield"), dict):
                zeroshield = obj["zeroshield"]
                # PHASE-6 fix (D5-RESP-STREAM-UPSTREAM-ERROR): the chat choke point
                # signals an UPSTREAM mid-stream failure via a terminal trace frame
                # whose zeroshield.action == 'error' (choices:[] and NO top-level
                # "error" key). Treat it as a stream error so we emit response.failed
                # instead of a false response.completed.
                if stream_error is None and str(zeroshield.get("action") or "").lower() == "error":
                    stream_error = {
                        "message": str(zeroshield.get("error") or zeroshield.get("reason")
                                       or "The upstream model stream failed."),
                        "code": "server_error", "type": "server_error",
                    }
            err_obj = obj.get("error")
            if isinstance(err_obj, dict) and err_obj:
                stream_error = err_obj
                err_kind = str(err_obj.get("code") or err_obj.get("type") or "")
                if err_kind == "output_blocked":
                    # Fail-closed: never complete successfully with blocked content.
                    accumulated.clear()
                    started_text = False
                continue
            if obj.get("usage"):
                u = obj["usage"]
                final_usage = {"input_tokens": u.get("prompt_tokens", 0),
                               "output_tokens": u.get("completion_tokens", 0),
                               "total_tokens": u.get("total_tokens", 0)}
            for ch in (obj.get("choices") or []):
                delta = ch.get("delta") or {}
                if ch.get("finish_reason"):
                    finish_reason = ch["finish_reason"]
                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    if not started_text:
                        yield _responses_sse("response.content_part.added", {
                            "type": "response.content_part.added", "item_id": item_id,
                            "output_index": 0, "content_index": 0,
                            "part": {"type": "output_text", "text": "", "annotations": []}})
                        started_text = True
                    accumulated.append(piece)
                    seq += 1
                    yield _responses_sse("response.output_text.delta", {
                        "type": "response.output_text.delta", "item_id": item_id,
                        "output_index": 0, "content_index": 0, "delta": piece, "sequence_number": seq})
                # Streamed function/tool calls: accumulate arguments per index and
                # surface each as a typed function_call output item (parity w/ non-stream).
                for tc in (delta.get("tool_calls") or []):
                    if not isinstance(tc, dict):
                        continue
                    idx = tc.get("index", 0)
                    fn = tc.get("function") or {}
                    entry = tool_calls.get(idx)
                    if entry is None:
                        # message item is output_index 0; tool calls follow it.
                        entry = {
                            "output_index": 1 + len(tool_order),
                            "fc_id": _gen_oai_id("function_call"),
                            "call_id": tc.get("id") or _gen_oai_id("function_call"),
                            "name": fn.get("name") or "",
                            "arguments": "",
                            "added": False,
                        }
                        tool_calls[idx] = entry
                        tool_order.append(idx)
                    if tc.get("id"):
                        entry["call_id"] = tc["id"]
                    if fn.get("name"):
                        entry["name"] = fn["name"]
                    if not entry["added"]:
                        entry["added"] = True
                        yield _responses_sse("response.output_item.added", {
                            "type": "response.output_item.added",
                            "output_index": entry["output_index"],
                            "item": {"id": entry["fc_id"], "type": "function_call",
                                     "status": "in_progress", "call_id": entry["call_id"],
                                     "name": entry["name"], "arguments": ""}})
                    arg_piece = fn.get("arguments")
                    if isinstance(arg_piece, str) and arg_piece:
                        entry["arguments"] += arg_piece
                        seq += 1
                        yield _responses_sse("response.function_call_arguments.delta", {
                            "type": "response.function_call_arguments.delta",
                            "item_id": entry["fc_id"], "output_index": entry["output_index"],
                            "delta": arg_piece, "sequence_number": seq})

    if stream_error is not None:
        seq += 1
        err_msg = stream_error.get("message") or "The response was blocked."
        err_code = stream_error.get("code") or stream_error.get("type") or "server_error"
        failed_response = dict(
            base_resp,
            status="failed",
            output=[],
            output_text="",
            usage=final_usage,
            error={"message": err_msg, "code": err_code},
        )
        if zeroshield is not None:
            failed_response["zeroshield"] = zeroshield
        yield _responses_sse("response.failed", {
            "type": "response.failed",
            "sequence_number": seq,
            "response": failed_response,
        })
        yield "data: [DONE]\n\n"
        return

    full_text = "".join(accumulated)
    if started_text:
        yield _responses_sse("response.output_text.done", {
            "type": "response.output_text.done", "item_id": item_id,
            "output_index": 0, "content_index": 0, "text": full_text})
    out_item = {"id": item_id, "type": "message", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": full_text, "annotations": []}]}
    yield _responses_sse("response.output_item.done", {
        "type": "response.output_item.done", "output_index": 0, "item": out_item})
    final_output = [out_item]
    # Finalize streamed function/tool calls: emit arguments.done + output_item.done and
    # include each as a completed function_call item in the response output (non-stream parity).
    for idx in tool_order:
        entry = tool_calls[idx]
        seq += 1
        yield _responses_sse("response.function_call_arguments.done", {
            "type": "response.function_call_arguments.done",
            "item_id": entry["fc_id"], "output_index": entry["output_index"],
            "arguments": entry["arguments"], "sequence_number": seq})
        fc_item = {"id": entry["fc_id"], "type": "function_call", "status": "completed",
                   "call_id": entry["call_id"], "name": entry["name"],
                   "arguments": entry["arguments"]}
        yield _responses_sse("response.output_item.done", {
            "type": "response.output_item.done",
            "output_index": entry["output_index"], "item": fc_item})
        final_output.append(fc_item)
    status = "incomplete" if finish_reason == "length" else "completed"
    final_response = dict(base_resp, status=status, output=final_output,
                          output_text=full_text, usage=final_usage)
    if zeroshield is not None:
        final_response["zeroshield"] = zeroshield
    # persist for previous_response_id chaining
    if store_ctx.get("store") and store_ctx.get("org_id") is not None and full_text:
        try:
            await store_ctx["store"].save(
                store_ctx["org_id"], final_response,
                replay_messages=store_ctx.get("input_messages", []) + [{"role": "assistant", "content": full_text}],
                input_items=store_ctx.get("input_items", []))
        except Exception:
            pass
    yield _responses_sse("response.completed", {"type": "response.completed", "response": final_response})
    yield "data: [DONE]\n\n"


@app.post("/v1/responses", summary="Create a model response (OpenAI Responses API)")
async def proxy_responses(
    request: Request,
    x_user_id: str | None = Header(None),
    x_endpoint_id: str | None = Header(None),
    x_agent_data: str | None = Header(None),
):
    try:
        raw_body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_build_oai_error(400, "Invalid JSON in request body."))
    raw_body = _strip_lone_surrogates(raw_body)
    if not isinstance(raw_body, dict):
        return JSONResponse(status_code=400, content=_build_oai_error(400, "Request body must be a JSON object."))
    if not raw_body.get("model"):
        return JSONResponse(status_code=400, content=_build_oai_error(400, "Missing required parameter: 'model'.", param="model"))
    if not isinstance(raw_body.get("model"), str):
        return JSONResponse(
            status_code=400,
            content=_build_oai_error(400, "'model' must be a string.", param="model", code="invalid_model"),
        )
    if raw_body.get("input") is None and not raw_body.get("instructions"):
        return JSONResponse(status_code=400, content=_build_oai_error(400, "Missing required parameter: 'input'.", param="input"))

    # B12: reject an unsupported Responses input-content part (input_file, input_audio,
    # ...) with a clean 400 instead of silently dropping it or forwarding a part the
    # chat pipeline cannot translate.
    _bad_part = _find_unsupported_input_part(raw_body.get("input"))
    if _bad_part is not None:
        return JSONResponse(
            status_code=400,
            content=_build_oai_error(
                400,
                f"Unsupported input content part type: '{_bad_part}'.",
                param="input",
                code="unsupported_content_part",
            ),
        )

    auth_ctx = getattr(request.state, "auth_context", None)
    org_id = getattr(auth_ctx, "organization_id", None) if auth_ctx else None
    store = _ResponseStore(REDIS_CLIENT)

    # previous_response_id -> prepend prior turn (org-scoped; fail closed on miss)
    prior_messages = []
    prev_id = raw_body.get("previous_response_id")
    if prev_id:
        if org_id is None:
            return JSONResponse(status_code=401, content=_build_oai_error(401, "Authentication required to continue a stored response.", error_type="authentication_error"))
        prior = await store.get_replay_messages(org_id, prev_id)
        if prior is None:
            return JSONResponse(status_code=404, content=_build_oai_error(404, f"Previous response '{prev_id}' not found.", code="response_not_found"))
        prior_messages = prior

    chat_body = _responses_to_chat(raw_body, prior_messages=prior_messages)
    response_id = _gen_oai_id("response")
    created_at = int(time.time())
    is_stream = bool(raw_body.get("stream"))

    chat_response = await _dispatch_chat_internally(request, chat_body, x_user_id, x_endpoint_id, x_agent_data)

    # ── Streaming path ──
    # Pre-stream blocks/errors from proxy_chat are JSONResponse, not SSE. Passing
    # them through as text/event-stream breaks stock SDK stream parsers.
    if is_stream and isinstance(chat_response, StreamingResponse):
        store_ctx = {"store": store if raw_body.get("store") else None,
                     "org_id": org_id,
                     "input_messages": chat_body.get("messages", []),
                     "input_items": raw_body.get("input") if isinstance(raw_body.get("input"), list) else [{"role": "user", "content": raw_body.get("input")}]}
        headers = {"x-request-id": response_id, "Content-Type": "text/event-stream",
                   "Cache-Control": "no-cache", "Connection": "keep-alive"}
        return StreamingResponse(
            _translate_chat_stream_to_responses(chat_response.body_iterator, response_id,
                                                chat_body.get("model") or raw_body["model"], created_at, store_ctx),
            media_type="text/event-stream", headers=headers)

    # ── Non-streaming path ──
    status = getattr(chat_response, "status_code", 500)
    try:
        chat_json = json.loads(bytes(getattr(chat_response, "body", b"") or b"{}"))
    except (TypeError, ValueError):
        chat_json = {}
    if status >= 400:
        # SEAM-C: an INNER-chat block/error carries its OWN canonical request_id in the
        # body (the _build_zeroshield_metadata id). Do NOT pin the header to response_id
        # here — let the compat shim fold the body request_id into x-request-id so the
        # SDK's error.request_id == the body the customer receives == the security log.
        return JSONResponse(status_code=status, content=_coerce_chat_error(status, chat_json))
    resp_obj = _chat_to_responses(chat_json, response_id=response_id, model=raw_body["model"],
                                  store=bool(raw_body.get("store")), metadata=raw_body.get("metadata"),
                                  previous_response_id=prev_id)
    if raw_body.get("store") and org_id is not None:
        replay = prior_messages + chat_body.get("messages", [])[len(prior_messages):] + _replay_msgs(resp_obj)
        await store.save(org_id, resp_obj, replay_messages=replay,
                         input_items=(raw_body.get("input") if isinstance(raw_body.get("input"), list)
                                      else [{"role": "user", "content": raw_body.get("input")}]))
    # SEAM-C: pin the SDK's response._request_id to the response object's own id
    # (resp_…) so r._request_id == r.id. Set request.state.gw_request_id AFTER the inner
    # dispatch (proxy_chat overwrote the shared scope state with its zs- id); the handler
    # header below is preferred by the shim, this is belt-and-suspenders for the body==hdr.
    request.state.gw_request_id = response_id
    return JSONResponse(status_code=200, content=resp_obj, headers={"x-request-id": response_id})


@app.get("/v1/responses/{response_id}", summary="Retrieve a model response")
async def get_response(response_id: str, request: Request):
    auth_ctx = getattr(request.state, "auth_context", None)
    org_id = getattr(auth_ctx, "organization_id", None) if auth_ctx else None
    if org_id is None:
        return JSONResponse(status_code=401, content=_build_oai_error(401, "Authentication required.", error_type="authentication_error"))
    obj = await _ResponseStore(REDIS_CLIENT).get_response(org_id, response_id)
    if obj is None:
        return JSONResponse(status_code=404, content=_build_oai_error(404, f"Response '{response_id}' not found.", code="response_not_found"))
    return JSONResponse(status_code=200, content=obj)


@app.delete("/v1/responses/{response_id}", summary="Delete a model response")
async def delete_response(response_id: str, request: Request):
    auth_ctx = getattr(request.state, "auth_context", None)
    org_id = getattr(auth_ctx, "organization_id", None) if auth_ctx else None
    if org_id is None:
        return JSONResponse(status_code=401, content=_build_oai_error(401, "Authentication required.", error_type="authentication_error"))
    ok = await _ResponseStore(REDIS_CLIENT).delete(org_id, response_id)
    if not ok:
        return JSONResponse(status_code=404, content=_build_oai_error(404, f"Response '{response_id}' not found.", code="response_not_found"))
    return JSONResponse(status_code=200, content={"id": response_id, "object": "response.deleted", "deleted": True})


@app.get("/v1/responses/{response_id}/input_items", summary="List input items for a response")
async def list_response_input_items(response_id: str, request: Request):
    auth_ctx = getattr(request.state, "auth_context", None)
    org_id = getattr(auth_ctx, "organization_id", None) if auth_ctx else None
    if org_id is None:
        return JSONResponse(status_code=401, content=_build_oai_error(401, "Authentication required.", error_type="authentication_error"))
    items = await _ResponseStore(REDIS_CLIENT).get_input_items(org_id, response_id)
    if items is None:
        return JSONResponse(status_code=404, content=_build_oai_error(404, f"Response '{response_id}' not found.", code="response_not_found"))
    data = [{"id": _gen_oai_id("message"), "object": "message", **(it if isinstance(it, dict) else {"content": it})}
            for it in items]
    return JSONResponse(status_code=200, content={"object": "list", "data": data, "has_more": False,
                                                  "first_id": data[0]["id"] if data else None,
                                                  "last_id": data[-1]["id"] if data else None})


# ── ZeroShield management/observability surfaces (bearer-key auth, org-scoped) ──
# Reachable with the SAME gateway key as inference, so SDK-only operators get
# real-time observability + usage without a separate console. Historical
# analytics / audit / tenant CRUD live in the control plane (dashboard).
@app.get("/v1/observability", summary="Real-time firewall observability (org-scoped)")
async def zs_observability(request: Request):
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=401, content=_build_oai_error(401, "Authentication required.", error_type="authentication_error"))
    org_slug = getattr(auth_ctx, "org_slug", "") or "default"
    org_config = CONFIG_SYNC.get_config(org_slug) if CONFIG_SYNC else dict(CONFIG)
    policy_info: dict = {}
    if POLICY_SYNC is not None:
        bundle = (getattr(POLICY_SYNC, "_org_caches", {}) or {}).get(org_slug) or {}
        policy_info = {"policy_count": bundle.get("policy_count", 0), "version": bundle.get("version")}
    models: list = []
    if REDIS_CLIENT is not None:
        try:
            async for key in REDIS_CLIENT.scan_iter(match=f"model_state:{org_slug}:*", count=200):
                kname = (key.decode() if isinstance(key, (bytes, bytearray)) else str(key)).split(":", 2)[-1]
                raw = await REDIS_CLIENT.get(key)
                st = json.loads(raw) if raw else {}
                models.append({"model": kname, "status": st.get("status", "active"),
                               "risk_score": st.get("risk_score")})
        except Exception:
            pass
    payload = {
        "object": "zeroshield.observability",
        "organization": org_slug,
        "firewall_enabled": bool(org_config.get("firewall_enabled", True)),
        "enforcement_mode": org_config.get("enforcement_mode", CONFIG.get("enforcement_mode", "block")),
        "policy": policy_info,
        "models": models,
        "circuit_breaker_loaded": CIRCUIT_BREAKER is not None,
    }
    return JSONResponse(status_code=200, content=payload, headers={"x-request-id": _gen_oai_id("response")})


@app.get("/v1/usage", summary="Current token-usage view for the calling key (org-scoped)")
async def zs_usage(request: Request):
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=401, content=_build_oai_error(401, "Authentication required.", error_type="authentication_error"))
    key_hash = getattr(auth_ctx, "key_hash", "") or ""
    limit = int(getattr(auth_ctx, "rate_limit_tpm", 0) or 0)
    used = 0
    if REDIS_CLIENT is not None and key_hash:
        try:
            bucket = int(time.time()) // 60
            raw = await REDIS_CLIENT.get(f"ratelimit:tpm:{key_hash}:{bucket}")
            used = int(raw) if raw else 0
        except Exception:
            used = 0
    payload = {
        "object": "zeroshield.usage",
        "organization": getattr(auth_ctx, "org_slug", ""),
        "window": "current_minute",
        "tpm_used": used,
        "tpm_limit": limit or None,
        "tpm_remaining": (max(0, limit - used) if limit else None),
        "allowed_models": getattr(auth_ctx, "allowed_models", []),
        "note": "Per-request token usage is on each response's `usage` object; "
                "historical usage/cost analytics are in the ZeroShield dashboard.",
    }
    return JSONResponse(status_code=200, content=payload, headers={"x-request-id": _gen_oai_id("response")})


@app.post(
    "/v1/embeddings",
    summary="Create embeddings (OpenAI-compatible proxy)",
    description=(
        "Proxy endpoint for OpenAI-compatible embedding creation.\n\n"
        "**Flow:**\n"
        "1. Authenticates the request via Gateway API Key\n"
        "2. Checks the model against the key's allowed_models list\n"
        "3. Forwards to the embedding provider via LiteLLM\n"
        "4. Returns the embedding response\n\n"
        "**Headers:**\n"
        "- `Authorization: Bearer <gateway_api_key>` (required)"
    ),
    response_description="OpenAI-compatible embedding response",
    tags=["Embeddings"],
    responses={
        200: {
            "description": "Successful embedding creation",
            "content": {
                "application/json": {
                    "example": {
                        "object": "list",
                        "data": [
                            {
                                "object": "embedding",
                                "embedding": [0.0023064255, -0.009327292],
                                "index": 0,
                            }
                        ],
                        "model": "text-embedding-3-small",
                        "usage": {
                            "prompt_tokens": 8,
                            "total_tokens": 8,
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid JSON body or missing input"},
        403: {"description": "Model not in allowlist"},
        502: {"description": "Upstream embedding request failed"},
    },
)
async def proxy_embeddings(request: Request):
    """Proxy to upstream embedding model. OpenAI-compatible endpoint."""
    METRICS["total_requests"] += 1
    start = time.perf_counter()
    # P9c: one request_id for the whole request, threaded into logs.
    _emb_rid = (
        request.headers.get("X-Request-ID")
        or request.headers.get("x-request-id")
        or f"zs-emb-{_uuid.uuid4().hex[:12]}"
    )
    _REQUEST_ID.set(_emb_rid)
    # SEAM-C: expose the SAME canonical id on the x-request-id RESPONSE header (the
    # compat shim reads request.state.gw_request_id) so the SDK's response._request_id
    # joins the handler's _REQUEST_ID used for every embedding log/telemetry line.
    request.state.gw_request_id = _emb_rid

    try:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        # Boundary type validation (mirror the chat handler). Downstream code
        # assumes ``body`` is a dict and forwards ``model`` raw to the router; a
        # non-object body (top-level array/number) hits ``body.get()`` ->
        # AttributeError -> unhandled 500, and a non-string ``model``
        # (int/float/bool) is forwarded raw to ``LLM_ROUTER.aembedding`` -> 500.
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "Request body must be a JSON object.",
                    "code": "invalid_request_body",
                },
            )
        if body.get("model") is not None and not isinstance(body.get("model"), str):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "message": "'model' must be a string.",
                    "param": "model",
                    "code": "invalid_model",
                },
            )

        if not body.get("input"):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request_error",
                    "message": "`input` is required.",
                    "param": "input",
                },
            )

        # ── R6: validate `input` shape BEFORE dispatch ──
        # `input` must be a string or a flat list of strings. A nested list / dict
        # / mixed-type payload previously reached the embedding provider and
        # triggered a 120s+ retry-storm (each malformed item retried through the
        # fallback chain) — a cheap DoS. Reject it as a 400 up front.
        _emb_in = body.get("input")
        _emb_input_valid = isinstance(_emb_in, str) or (
            isinstance(_emb_in, list)
            and bool(_emb_in)
            and all(isinstance(item, str) for item in _emb_in)
        )
        if not _emb_input_valid:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request_error",
                    "message": "`input` must be a string or an array of strings.",
                    "param": "input",
                    "code": "invalid_embedding_input",
                },
            )

        # ── FIX-2: hard input-size ceiling BEFORE dispatch ──
        # Cap both the batch-item count and total character count so a single
        # request cannot fan out an unbounded provider call / retry-storm.
        if isinstance(_emb_in, list):
            if len(_emb_in) > MAX_EMBED_BATCH:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "payload_too_large",
                        "code": "embedding_input_too_large",
                    },
                )
            _emb_total_chars = sum(len(item) for item in _emb_in)
        else:
            _emb_total_chars = len(_emb_in)
        if _emb_total_chars > MAX_EMBED_INPUT_CHARS:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "payload_too_large",
                    "code": "embedding_input_too_large",
                },
            )

        auth_ctx = getattr(request.state, "auth_context", None)
        org_slug = auth_ctx.org_slug if auth_ctx else ""
        org_config = CONFIG_SYNC.get_config(org_slug) if CONFIG_SYNC else CONFIG

        _rm = body.get("model", "text-embedding-3-small")
        # Coerce to str so the platform/isolation guards below never 500 on a
        # non-string ``model`` (type confusion).
        requested_model = _rm if isinstance(_rm, str) else "text-embedding-3-small"
        if auth_ctx is not None:
            user_id = auth_ctx.user_id
            project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace
            allowed_models = auth_ctx.allowed_models

            if allowed_models and requested_model not in allowed_models:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=403,
                    event_type="embedding_blocked",
                    model=requested_model,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    organization_id=getattr(auth_ctx, "organization_id", None),
                    action="block",
                    risk_score=0.50,
                    threat_type="model_not_allowed",
                    metadata={"detail": f"Model '{_safe_model_echo(requested_model)}' not in key allowlist", "module": "1.3", "module_id": "1.3"},
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Model '{_safe_model_echo(requested_model)}' is not in your allowlist.",
                        "code": "model_not_allowed",
                    },
                )
        else:
            user_id = None
            project_id = None
            allowed_models = None

        # ── FIX-3: mirror the chat path's platform-reservation + isolation gates ──
        # (a) Reserved platform/guard model names are NEVER inferable as an org
        #     target — reject with a GENERIC 403 that never names the internal model.
        try:
            from platform_models import is_platform_model_name as _is_platform_model_emb
        except ImportError:
            from .platform_models import is_platform_model_name as _is_platform_model_emb
        if requested_model and _is_platform_model_emb(requested_model):
            METRICS["blocked"] += 1
            _emit_telemetry(
                status_code=403,
                event_type="embedding_blocked",
                model=requested_model,
                user_id=user_id,
                project_id=str(project_id or ""),
                key_prefix=auth_ctx.prefix if auth_ctx else "",
                organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                action="block",
                risk_score=0.5,
                threat_type="model_not_allowed",
                metadata={"reason": "platform_model_not_inferable", "module": "1.3", "module_id": "1.3"},
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "The requested model is not available for inference.",
                    "code": "model_not_allowed",
                },
            )

        # (b) Org-ownership gate (mirrors the chat path's _validate_org_inference_model).
        # The chat ``allowed_models`` whitelist is chat-scoped and must NOT gate
        # embeddings (an org whose allowed_models=["gemma-free"] still needs its
        # zs-embed). But the requested embedding model MUST still belong to the
        # CALLER's org — otherwise a tenant whose org owns no embedding model can
        # invoke ANOTHER org's BYOK embedding model via the shared global router
        # and burn its upstream credits (cross-tenant IDOR). So resolve the
        # caller-org's OWN embedding model set and reject anything outside it.
        if auth_ctx is not None and CONFIG_SYNC is not None:
            _emb_routing = CONFIG_SYNC.get_model_routing(org_slug)
            if not _emb_routing:
                # cold-cache self-heal (same as the chat path)
                await CONFIG_SYNC.reload_models_now(org_slug=org_slug)
                _emb_routing = CONFIG_SYNC.get_model_routing(org_slug)
            _org_embedding_models = _filter_embedding_eligible_models(_emb_routing)
            _allowed_embeddings = _routing_identity_set(_org_embedding_models)
            _final_embedding = requested_model.strip()
            _emb_owned = bool(_allowed_embeddings) and (
                _final_embedding in _allowed_embeddings or _final_embedding.lower() == "auto"
            )
            if not _emb_owned:
                METRICS["blocked"] += 1
                _emit_telemetry(
                    status_code=422,
                    event_type="embedding_blocked",
                    model=requested_model,
                    user_id=user_id,
                    project_id=str(project_id or ""),
                    key_prefix=auth_ctx.prefix if auth_ctx else "",
                    organization_id=getattr(auth_ctx, "organization_id", None),
                    action="block",
                    risk_score=0.5,
                    threat_type="model_not_allowed",
                    metadata={"reason": "embedding_model_not_configured", "module": "1.3", "module_id": "1.3"},
                )
                if not _allowed_embeddings:
                    return JSONResponse(
                        status_code=422,
                        content={
                            "error": "no_provider_configured",
                            "message": (
                                "No organization embedding model is configured. "
                                "Connect your embedding provider under Firewall → RAG & Vector DB Firewall (1.3) → Vector Provider Config."
                            ),
                            "code": "no_provider_configured",
                            "blocked_by": "model_routing",
                            "category": "inference_not_configured",
                        },
                    )
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": "model_not_configured",
                        "message": (
                            f"Embedding model '{_safe_model_echo(requested_model)}' is not configured for your organization. "
                            "Add it under your organization's embedding provider configuration."
                        ),
                        "code": "model_not_configured",
                        "blocked_by": "model_routing",
                        "category": "inference_not_configured",
                    },
                )

        if LLM_ROUTER is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "message": "LLM router not initialized.",
                },
            )

        # ── Phase 1 §1.1: per-org TPM ceiling (fail-CLOSED) ──
        # Embeddings can be a high-volume vector for resource exhaustion across
        # tenants; enforce the same ceiling as /v1/chat. Estimate token cost from
        # the `input` payload (string or list of strings).
        _emb_input = body.get("input") or ""
        if isinstance(_emb_input, list):
            _emb_text = " ".join(str(x) for x in _emb_input if x)
        else:
            _emb_text = str(_emb_input)
        _emb_est_tokens = _estimate_request_tokens(_emb_text)
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="embedding_blocked",
            model=body.get("model", "text-embedding-3-small"),
            user_id=user_id,
            project_id=str(project_id or ""),
            estimated_tokens=_emb_est_tokens,
        )
        if _rl_resp is not None:
            return _rl_resp

        # ── FIX-2: per-org burst (req/s) + RPM ceiling (same as the chat path) ──
        # org_tpm_limit is effectively always 0, so the TPM gate above never
        # actually fired for configured limits; apply the burst/RPM dampener too.
        _burst_resp = await _enforce_org_burst_rpm(
            auth_ctx,
            event_type="embedding_blocked",
            model=body.get("model", "text-embedding-3-small"),
            user_id=user_id,
            project_id=str(project_id or ""),
        )
        if _burst_resp is not None:
            return _burst_resp

        # I3: embeddings were dispatched with NO kill-switch / model-state gate
        # (unlike proxy_chat) — an operator-disabled embedding model still served.
        # Embeddings have no chat fallback, so a kill-switch 'reroute' is
        # meaningless: any is_killed / isolated / suspended -> 503 (fail-closed).
        if org_config.get("kill_switch_enabled", True):
            if REDIS_CLIENT is None:
                METRICS["blocked"] += 1
                return JSONResponse(status_code=503, content={"error": "service_unavailable", "code": "kill_switch_active", "message": "Kill-switch enforcement unavailable."})
            try:
                from kill_switch import check_kill_switch as _ck_emb
            except ImportError:
                from .kill_switch import check_kill_switch as _ck_emb
            _emb_ks = await _ck_emb(REDIS_CLIENT, requested_model, org_slug=org_slug, key_prefix=auth_ctx.prefix if auth_ctx else "")
            if getattr(_emb_ks, "is_killed", False):
                METRICS["blocked"] += 1
                return JSONResponse(status_code=503, content={"error": "service_unavailable", "code": "kill_switch_active", "message": "This model is currently disabled by an operator kill-switch."})
        if REDIS_CLIENT is not None:
            try:
                from model_state import check_model_state as _cms_emb
            except ImportError:
                from .model_state import check_model_state as _cms_emb
            _emb_ms = await _cms_emb(REDIS_CLIENT, requested_model, org_slug=org_slug or "default")
            if getattr(_emb_ms, "status", "") in ("isolated", "suspended"):
                METRICS["blocked"] += 1
                return JSONResponse(status_code=503, content={"error": "service_unavailable", "code": "model_unavailable", "message": "This model is currently isolated by an operator."})

        # H7: carry the org so aembedding org-qualifies the embedding routing key
        # ({org}::{model}) and uses THIS org's BYOK key — never a same-named peer.
        body["_zs_org_slug"] = org_slug

        # G1/G3/G4: scan + redact PII/secrets in the embedding inputs BEFORE they
        # leave the gateway, with the SAME tier-1 scanner + verdict-aware redactor
        # the chat path uses (gated by the SAME input_scan_enabled config). Without
        # this, a raw email/SSN/API-key was embedded verbatim by the upstream
        # provider — the chat path never had that gap. ``input`` was already shape-
        # validated above to a non-empty str or list[str].
        _emb_input_raw = body.get("input")
        _emb_was_str = isinstance(_emb_input_raw, str)
        _emb_texts = [_emb_input_raw] if _emb_was_str else list(_emb_input_raw)
        _emb_redacted, _emb_block = await _scan_redact_embedding_inputs(_emb_texts, org_config)
        if _emb_block is not None:
            METRICS["blocked"] += 1
            _emit_telemetry(
                status_code=403,
                event_type="embedding_blocked",
                model=requested_model,
                user_id=user_id,
                project_id=str(project_id or ""),
                key_prefix=auth_ctx.prefix if auth_ctx else "",
                organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
                action="block",
                risk_score=0.85,
                threat_type="pii",
                compliance_tags=org_config.get("compliance_frameworks", []),
                pipeline_stage="query",
                metadata={
                    "reason": _emb_block.get("reason"),
                    "module": "1.3",
                    "module_id": "1.3",
                },
            )
            return _build_block_response(
                403,
                "tier_1_pii",
                _build_zeroshield_metadata(
                    action="block",
                    reason="Embedding input blocked: PII detected could not be redacted.",
                    detection_tier="tier_1",
                    threat_type="pii",
                    confidence=0.85,
                    detail=_emb_block.get("reason"),
                ),
                requested_model=requested_model,
            )
        # Write the redacted texts back, preserving the original str-vs-list shape.
        body["input"] = _emb_redacted[0] if _emb_was_str else _emb_redacted

        status, result = await LLM_ROUTER.aembedding(body)

        # Never reflect raw LiteLLM exception text (fallback topology + OpenRouter
        # user_id) on an embedding failure (R5).
        if status != 200:
            result = _sanitize_llm_error_response(status, result)

        # Echo the tenant's requested model ALIAS (e.g. "zs-embed") in the success
        # response rather than the resolved upstream provider id (e.g.
        # "text-embedding-3-small"). Matches the OpenAI contract and avoids
        # disclosing the org's BYOK underlying provider model id — parity with the
        # chat-path model aliasing.
        if status == 200 and isinstance(result, dict) and result.get("model"):
            result["model"] = requested_model

        elapsed_ms = (time.perf_counter() - start) * 1000
        response_headers = {
            "X-ZeroShield-Action": "pass",
            "X-Processing-Time-Ms": f"{elapsed_ms:.1f}",
        }

        _emit_telemetry(
            status_code=status,
            event_type="embedding_request",
            model=body.get("model", "text-embedding-3-small"),
            user_id=user_id,
            project_id=str(project_id or ""),
            key_prefix=auth_ctx.prefix if auth_ctx else "",
            organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
            action="pass",
            risk_score=0.0,
            metadata={
                "status": status,
                "processing_time_ms": elapsed_ms,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(status_code=status, content=result, headers=_latin1_safe_headers(response_headers))
    finally:
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000


# ── Dynamic vector client resolution (org config → env-var default) ──

def _resolve_vector_client(vector_db_type: str, org_id: int | str | None = None):
    """
    Resolve a vector DB client using the credential hierarchy:
        1. Org-level VectorProviderConfig from VECTOR_PROVIDER_SYNC cache
        2. Gateway-level VECTOR_CLIENTS from env-var startup
    Returns (client, provider_type_used) or (None, None).
    """
    # Try org-level config first
    if org_id and VECTOR_PROVIDER_SYNC is not None:
        lookup_types = [vector_db_type]
        if vector_db_type == "custom":
            lookup_types.append("chroma")
        for resolved_type in lookup_types:
            cfg = VECTOR_PROVIDER_SYNC.get_provider_config(org_id, resolved_type)
            if not (cfg and cfg.get("is_active")):
                continue
            # R12 (#8): SSRF guard on the org-supplied connection_url (mirrors
            # rag_collections.client_from_provider_config). Blocks metadata/loopback/
            # link-local; permits private RFC1918 (legit self-hosted vector DBs).
            # Pinecone (no connection_url) is unaffected.
            _conn_url = cfg.get("connection_url")
            _vp_ok = True
            if _conn_url and resolved_type in ("chroma", "milvus", "custom"):
                try:
                    from _url_guard import is_safe_vector_provider_url as _safe_vp
                except ImportError:
                    from ._url_guard import is_safe_vector_provider_url as _safe_vp
                _vp_ok, _why = _safe_vp(str(_conn_url))
                if not _vp_ok:
                    LOG.warning("Rejected org vector provider connection_url for org=%s (SSRF guard): %s", org_id, _why)
            if _vp_ok:
                from vector_client import PineconeClient, MilvusClient, ChromaDBClient
                try:
                    if resolved_type == "pinecone" and cfg.get("api_key"):
                        return PineconeClient(
                            api_key=cfg["api_key"],
                            environment=cfg.get("environment", ""),
                            embedding_model=cfg.get("embedding_model", "text-embedding-3-small"),
                            embedding_api_key=cfg.get("embedding_api_key", ""),
                            reranker_model=cfg.get("reranker_model", ""),
                        ), "pinecone"
                    elif resolved_type == "chroma" and cfg.get("connection_url"):
                        # BYOK Chroma: the org connects their own Chroma server.
                        return ChromaDBClient(
                            url=cfg["connection_url"],
                            auth_token=cfg.get("api_key", ""),
                        ), "chroma"
                    elif resolved_type == "milvus" and cfg.get("connection_url"):
                        return MilvusClient(
                            uri=cfg["connection_url"],
                            token=cfg.get("api_key", ""),
                        ), "milvus"
                    elif resolved_type == "custom" and cfg.get("connection_url"):
                        url = str(cfg["connection_url"])
                        if url.startswith("http://") or url.startswith("https://"):
                            return ChromaDBClient(
                                url=url,
                                auth_token=cfg.get("api_key", ""),
                            ), "chroma"
                        return MilvusClient(
                            uri=url,
                            token=cfg.get("api_key", ""),
                        ), "custom"
                except Exception:
                    LOG.warning(
                        "Failed to create org-level %s client for org=%s, falling back to default",
                        resolved_type,
                        org_id,
                    )

    # Fall back to gateway-level default
    client = VECTOR_CLIENTS.get(vector_db_type)
    if client:
        return client, vector_db_type

    # If requested type not found, try any available
    if VECTOR_CLIENTS:
        fallback_type = next(iter(VECTOR_CLIENTS))
        return VECTOR_CLIENTS[fallback_type], fallback_type

    return None, None


def _alert_vector_policy_miss(*, project_id, collection_name, organization_id, user_id, operation):
    """Loud, high-signal alert when a vector request finds NO compiled policy and
    falls back to the PERMISSIVE default-monitor policy.

    This is the fail-OPEN seam at the root of the rag #1 bypass class: a miss
    (stale/slug-keyed bundle, key drift, an unknown collection, or org_id None)
    silently degrades enforcement to monitor-only. Per the chosen posture we KEEP
    availability (permissive default) but make every miss observable — a warning
    log, a counter, and a telemetry event — so a bypass can never hide silently."""
    try:
        METRICS["vector_policy_miss"] = METRICS.get("vector_policy_miss", 0) + 1
    except Exception:  # noqa: BLE001 - metrics must never break the request
        pass
    LOG.warning(
        "VECTOR POLICY MISS — no compiled policy for org=%s project=%s collection=%s op=%s; "
        "falling back to PERMISSIVE default-monitor (enforcement degraded). Verify the compiled "
        "bundle is organization_id-keyed and the gateway VectorPolicySync reloaded it.",
        organization_id, project_id, collection_name, operation,
    )
    try:
        _emit_telemetry(
            status_code=200, event_type="vector_policy_miss", model="",
            user_id=user_id or "", project_id=str(project_id or ""), key_prefix="",
            organization_id=organization_id, action="monitor", risk_score=0.0,
            metadata={
                "collection": collection_name, "operation": operation,
                "reason": "no_compiled_policy_permissive_default",
                "module": "1.3", "module_id": "1.3",
            },
        )
    except Exception:  # noqa: BLE001
        LOG.debug("vector policy-miss telemetry emit failed", exc_info=True)


def _normalize_collection_name(name: str) -> str:
    """Canonicalize a user-facing collection identifier.

    Case-folds to lowercase so case variants ('Docs', 'DOCS', 'docs') resolve to
    the SAME policy lookup AND the SAME tenant namespace. Without this, a
    case-variant collection name evades a deny / block_sensitive policy keyed on
    the lowercase name and writes into a sibling namespace (R10).
    """
    if not isinstance(name, str):
        return ""
    return name.strip().lower()


def _is_valid_collection_name(name: str) -> bool:
    """Validate user-facing collection identifiers.

    Reject tenant prefix separators to prevent cross-tenant namespace injection
    attempts like "other_tenant__collection". Also reject leading/trailing dots
    and dot-run sequences ("..", "foo.", ".foo") which can be abused to traverse
    or inject into the ``{project_id}__{collection_name}`` namespace (R17).
    """
    if not name or "__" in name:
        return False
    if len(name) > 128:
        return False
    # No leading/trailing separators and no consecutive dots — these are the
    # namespace-injection / path-traversal shapes (a bare "." or ".." would
    # otherwise pass the charset check).
    if name[0] in "._-" or name[-1] in "._-":
        return False
    if ".." in name:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", name))


async def _rag_embedding_killswitch_block(org_slug, org_id, key_prefix, vector_db_type, policy_embedding_model, check_reranker=False):
    """R12 (#2/#4/#6) + R14 (#3): apply the SAME kill-switch + model-state gate to
    the RAG embedding model — AND, for the query/rerank path, the reranker model —
    that proxy_embeddings applies. RAG embeds via byok_embedder → litellm.embedding
    directly (NOT LLM_ROUTER.aembedding) and reranks via the vector client, so the
    /v1/embeddings I3-Med gate did NOT cover either. Resolves the effective models
    (collection-pinned policy embedding model, else the org VectorProviderConfig
    embedding_model; reranker_model from the provider config when check_reranker)
    and returns a JSONResponse(503) when ANY is killed/isolated/suspended (or
    Redis-down with kill-switch on); else None. No-op when nothing resolves."""
    _cfg = None
    if org_id is not None and VECTOR_PROVIDER_SYNC is not None:
        try:
            _cfg = VECTOR_PROVIDER_SYNC.get_provider_config(org_id, vector_db_type)
        except Exception:  # noqa: BLE001 - resolution best-effort; absence => no gate
            _cfg = None
    models: list[str] = []
    _emb = (policy_embedding_model or "").strip()
    if not _emb and _cfg:
        _emb = str(_cfg.get("embedding_model") or "").strip()
    if _emb:
        models.append(_emb)
    if check_reranker and _cfg:
        _rr = str(_cfg.get("reranker_model") or "").strip()
        if _rr and _rr not in models:
            models.append(_rr)
    if not models:
        return None
    org_config = CONFIG_SYNC.get_config(org_slug) if CONFIG_SYNC else CONFIG
    _ks_enabled = bool(org_config.get("kill_switch_enabled", True))
    if _ks_enabled and REDIS_CLIENT is None:
        METRICS["blocked"] += 1
        return JSONResponse(status_code=503, content={"error": "service_unavailable", "code": "kill_switch_active", "message": "Kill-switch enforcement unavailable."})
    _ck_rag = _cms_rag = None
    if _ks_enabled and REDIS_CLIENT is not None:
        try:
            from kill_switch import check_kill_switch as _ck_rag
        except ImportError:
            from .kill_switch import check_kill_switch as _ck_rag
    if REDIS_CLIENT is not None:
        try:
            from model_state import check_model_state as _cms_rag
        except ImportError:
            from .model_state import check_model_state as _cms_rag
    for _m in models:
        if _ck_rag is not None:
            _ks = await _ck_rag(REDIS_CLIENT, _m, org_slug=org_slug, key_prefix=key_prefix or "")
            if getattr(_ks, "is_killed", False):
                METRICS["blocked"] += 1
                return JSONResponse(status_code=503, content={"error": "service_unavailable", "code": "kill_switch_active", "message": "A model required for this RAG request is disabled by an operator kill-switch."})
        if _cms_rag is not None:
            _ms = await _cms_rag(REDIS_CLIENT, _m, org_slug=org_slug or "default")
            if getattr(_ms, "status", "") in ("isolated", "suspended"):
                METRICS["blocked"] += 1
                return JSONResponse(status_code=503, content={"error": "service_unavailable", "code": "model_unavailable", "message": "A model required for this RAG request is currently isolated by an operator."})
    return None


@app.post(
    "/v1/rag/query",
    summary="RAG vector query (with firewall enforcement)",
    description=(
        "Gateway-proxied vector database query for RAG workloads.\n\n"
        "**Flow:**\n"
        "1. Authenticates the request via Gateway API Key\n"
        "2. Looks up VectorCollectionPolicy for the requested collection\n"
        "3. Enforces collection-level RBAC (allowed operations, max results, query length)\n"
        "4. Scans the query for prompt injection / vector injection attacks\n"
        "5. Executes the query against the configured vector database\n"
        "6. Scans retrieved documents for indirect injection, PII, secrets, toxicity\n"
        "7. Detects embedding anomalies via distance threshold + statistical outlier analysis\n"
        "8. Returns filtered results with scan verdict metadata\n\n"
        "**Headers:**\n"
        "- `Authorization: Bearer <gateway_api_key>` (required)\n\n"
        "**Namespace Isolation:**\n"
        "The actual vector DB collection is namespaced as `{project_id}__{collection_name}` "
        "to prevent cross-tenant data leakage."
    ),
    tags=["RAG"],
    responses={
        200: {
            "description": "Successful RAG query with filtered results",
            "content": {
                "application/json": {
                    "example": {
                        "collection": "customer_docs",
                        "query": "What is the refund policy?",
                        "documents": [
                            {
                                "id": "doc-123",
                                "content": "Our refund policy states...",
                                "metadata": {"category": "policies"},
                                "distance": 0.12,
                            }
                        ],
                        "total_retrieved": 6,
                        "filtered_count": 1,
                        "scan_verdict": {
                            "action": "allow",
                            "flagged_documents": [],
                            "detail": "",
                        },
                    }
                }
            },
        },
        400: {"description": "Invalid request body or missing required fields"},
        403: {
            "description": "Blocked by vector policy, injection detected, or access denied",
            "content": {
                "application/json": {
                    "examples": {
                        "no_policy": {
                            "value": {
                                "error": "forbidden",
                                "message": "Access denied: no policy for collection 'customer_docs'.",
                                "code": "rag_access_denied",
                            }
                        },
                        "injection_blocked": {
                            "value": {
                                "error": "blocked",
                                "message": "RAG query blocked: prompt_injection detected.",
                                "code": "rag_query_blocked",
                                "threat_type": "prompt_injection",
                            }
                        },
                    }
                }
            },
        },
        503: {"description": "Vector DB client not configured or RAG firewall not enabled"},
    },
)
async def rag_query(request: Request):
    """Proxy to vector database after pipeline-aware policy enforcement."""
    METRICS["total_requests"] += 1
    METRICS["active_connections"] += 1
    start_rag = time.perf_counter()
    # P9c: one request_id for the whole request, threaded into logs.
    _REQUEST_ID.set(
        request.headers.get("X-Request-ID")
        or request.headers.get("x-request-id")
        or f"zs-rag-{_uuid.uuid4().hex[:12]}"
    )

    try:
        if RAG_PIPELINE is None or VECTOR_POLICY_SYNC is None:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "service_unavailable",
                    "message": "RAG firewall not enabled. Set GATEWAY_RAG_ENABLED=true.",
                    "code": "rag_not_enabled",
                },
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        # Boundary validation: a non-object body or type-confused fields would
        # otherwise crash `.strip()` / `min()` with an unhandled 500 (the chat
        # endpoint already guards this — mirror it here).
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "Request body must be a JSON object.", "code": "invalid_request_body"},
            )
        _coll = body.get("collection", "")
        _query = body.get("query", "")
        _vdb = body.get("vector_db_type", "pinecone")
        _ns = body.get("namespace", "")
        collection_name = _coll.strip() if isinstance(_coll, str) else ""
        query_text = _query.strip() if isinstance(_query, str) else ""
        vector_db_type = (_vdb.strip() if isinstance(_vdb, str) else "pinecone") or "pinecone"
        namespace = _ns if isinstance(_ns, str) else ""
        try:
            n_results = int(body.get("n_results", CONFIG.get("rag_default_max_results", 10)))
        except (TypeError, ValueError, OverflowError):
            # OverflowError: JSON ``Infinity`` / ``1e999`` -> float('inf');
            # ``int(inf)`` raises OverflowError, which must be caught here or it
            # escapes as a raw 500.
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "'n_results' must be an integer.", "code": "invalid_n_results"},
            )
        if n_results < 1:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "'n_results' must be >= 1.", "code": "invalid_n_results"},
            )
        where_filter = body.get("where")
        if where_filter is None:
            # Some callers use ``filter`` instead of ``where``; honour both.
            where_filter = body.get("filter")
        # E3: a malformed ``where``/``filter`` (e.g. a string or list) would be
        # forwarded verbatim to the vector backend (Pinecone) and surface as an
        # unhandled ApiError 500. Reject anything that is not a JSON object here.
        if where_filter is not None and not isinstance(where_filter, dict):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "bad_request",
                    "message": "'where'/'filter' must be a JSON object.",
                    "code": "invalid_filter",
                },
            )

        if not collection_name:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "Missing required field: 'collection'."},
            )
        if not _is_valid_collection_name(collection_name):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                    "code": "rag_namespace_violation",
                },
            )
        # Case-fold AFTER validation so the deny/block_sensitive policy lookup and
        # the tenant namespace both key on the canonical name (R10).
        collection_name = _normalize_collection_name(collection_name)
        if not query_text:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "Missing required field: 'query'."},
            )

        auth_ctx = getattr(request.state, "auth_context", None)
        if auth_ctx is None:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Authentication required.",
                    "code": "auth_required",
                },
            )
        project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace

        # ── Phase 1 §1.1: per-org TPM ceiling on RAG query traffic ──
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="rag_query_blocked",
            model=body.get("embedding_model", "") or "",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=str(project_id or ""),
            estimated_tokens=_estimate_request_tokens(query_text),
        )
        if _rl_resp is not None:
            return _rl_resp

        # FINDING-10: per-org burst (req/s) + RPM ceiling — org_tpm_limit is
        # effectively always 0, so the TPM gate above never fires for configured
        # limits; apply the burst/RPM dampener too (mirrors the embeddings path).
        _burst_resp = await _enforce_org_burst_rpm(
            auth_ctx,
            event_type="rag_query_blocked",
            model=body.get("embedding_model", "") or "",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=str(project_id or ""),
        )
        if _burst_resp is not None:
            return _burst_resp

        policy = VECTOR_POLICY_SYNC.get_policy(
            str(project_id),
            collection_name,
            organization_id=getattr(auth_ctx, "organization_id", None),
        )
        if policy is None:
            _alert_vector_policy_miss(
                project_id=project_id, collection_name=collection_name,
                organization_id=getattr(auth_ctx, "organization_id", None),
                user_id=getattr(auth_ctx, "user_id", ""), operation="query",
            )
            # Fail CLOSED for any named (non-'default') collection that resolves to
            # NO policy. The permissive default-monitor fallback is reserved for the
            # 'default' collection only; otherwise a case-variant or unknown name
            # could quietly bypass a deny / block_sensitive policy (R10).
            if collection_name != "default":
                METRICS["blocked"] += 1
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Access denied: no policy for collection '{collection_name}'.",
                        "code": "rag_access_denied",
                    },
                )
            # Fallback: generate a permissive default policy for the default
            # collection so the pipeline still runs (with monitoring).
            policy = {
                "enabled": True,
                "collection_name": collection_name,
                "project_id": str(project_id),
                "default_action": "monitor",
                "allowed_operations": ["query", "insert"],
                "max_results_per_query": 50,
                "max_query_length": 2048,
                "require_context_scan": True,
                "block_sensitive_documents": False,
                "sensitive_fields": [],
                "anomaly_distance_threshold": None,
                "embedding_model": "",
                "embedding_dimension": None,
            }

        if not policy.get("enabled", False):
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Policy for collection '{collection_name}' is disabled.",
                    "code": "rag_policy_disabled",
                },
            )

        # ── Per-operation access decision ──
        # ``default_action`` governs operations NOT in ``allowed_operations``.
        # A READ-ONLY collection is default_action=deny + allowed_operations=
        # ["query"]: the query IS permitted (deny restricts only the un-listed
        # ops, e.g. insert/delete). So check whether THIS operation is allowed
        # FIRST; apply the deny/block default only when it is not. (Gating deny
        # before allowed_operations wrongly 403'd legitimate reads on every
        # read-only KB. The field was previously not consumed at all, so deny
        # policies also failed OPEN — both extremes are wrong.)
        allowed_ops = policy.get("allowed_operations", [])
        _default_action = str(policy.get("default_action", "monitor") or "monitor").strip().lower()
        if "query" not in allowed_ops:
            METRICS["blocked"] += 1
            if _default_action in ("deny", "block"):
                _emit_telemetry(
                    status_code=403,
                    event_type="rag_query",
                    model="",
                    user_id=getattr(auth_ctx, "user_id", ""),
                    project_id=str(project_id),
                    key_prefix=getattr(auth_ctx, "prefix", ""),
                    organization_id=getattr(auth_ctx, "organization_id", None),
                    action="block",
                    risk_score=0.0,
                    threat_type="policy_violation",
                    pipeline_stage="policy",
                    latency_ms=(time.perf_counter() - start_rag) * 1000,
                    metadata={
                        "collection": collection_name,
                        "vector_db_type": vector_db_type,
                        "default_action": _default_action,
                        "detail": "Collection access denied by vector policy default_action.",
                        "module": "1.3",
                        "module_id": "1.3",
                    },
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Access denied: policy for collection '{collection_name}' denies access.",
                        "code": "rag_access_denied",
                    },
                )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Query operation not permitted on this collection.",
                    "code": "rag_operation_denied",
                },
            )

        # ── Embedding access control enforcement ──
        req_embedding_model = body.get("embedding_model", "")
        req_embedding_dim = body.get("embedding_dimension")
        policy_embedding_model = policy.get("embedding_model", "")
        policy_embedding_dim = policy.get("embedding_dimension")

        _req_emb_concrete = bool(req_embedding_model) and str(req_embedding_model).strip().lower() != "auto"
        if policy_embedding_model and _req_emb_concrete and req_embedding_model != policy_embedding_model:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": f"Embedding model mismatch: expected '{policy_embedding_model}', got '{req_embedding_model}'.",
                    "code": "embedding_model_mismatch",
                },
            )
        # L8: when the collection policy pins NO embedding model, a client-supplied
        # CONCRETE embedding_model had nothing to validate against and was accepted
        # (and used) without checking — an unknown/unowned model slipping through.
        # The RAG embedding is determined by the org's vector-provider config, not
        # a free client override, so reject a concrete client value here. ('auto'
        # and an absent value still defer to the provider's configured model.)
        if not policy_embedding_model and _req_emb_concrete:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=404,
                content={
                    "error": "model_not_configured",
                    "message": (
                        f"Embedding model '{_safe_model_echo(req_embedding_model)}' is not configured for "
                        "this collection. Omit 'embedding_model' (or send 'auto') to use the org's provider model."
                    ),
                    "code": "embedding_model_not_configured",
                    "blocked_by": "vector_policy",
                },
            )
        if policy_embedding_dim and req_embedding_dim:
            try:
                _req_dim = int(req_embedding_dim)
                _pol_dim = int(policy_embedding_dim)
            except (TypeError, ValueError, OverflowError):
                # Client-supplied embedding_dimension may be a non-integer / NaN /
                # Infinity (JSON ``1e999``); int() of those raises and would be a
                # raw 500. Reject with a 400 instead.
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "bad_request",
                        "message": "'embedding_dimension' must be an integer.",
                        "code": "invalid_embedding_dimension",
                    },
                )
            if _req_dim != _pol_dim:
                METRICS["blocked"] += 1
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Embedding dimension mismatch: expected {policy_embedding_dim}, got {req_embedding_dim}.",
                        "code": "embedding_dimension_mismatch",
                    },
                )

        max_results = policy.get("max_results_per_query", CONFIG.get("rag_default_max_results", 10))
        n_results = min(n_results, max_results)

        # ── Execute 4-stage RAG Firewall Pipeline ──
        rag_policy = dict(policy)
        rag_policy["_org_slug"] = getattr(auth_ctx, "org_slug", None) or ""
        # Resolve the caller's per-org vector client (VectorProviderConfig →
        # VECTOR_PROVIDER_SYNC) so retrieval uses the org's own Pinecone/Milvus
        # credentials. Falls back to the gateway-level env client inside
        # _resolve_vector_client when no org config exists.
        effective_vector_db_type = policy.get("vector_db_type", vector_db_type)
        org_id_for_client = getattr(auth_ctx, "organization_id", None) if auth_ctx else None

        # R12 (#2/#6): gate the RAG embedding model on operator kill-switch +
        # model-state (parity with /v1/embeddings). RAG embeds via byok_embedder
        # (not LLM_ROUTER.aembedding) so the I3-Med gate did not cover this path.
        _rag_ks_block = await _rag_embedding_killswitch_block(
            rag_policy["_org_slug"], org_id_for_client,
            (getattr(auth_ctx, "prefix", "") if auth_ctx else ""),
            effective_vector_db_type, policy_embedding_model,
            check_reranker=True,  # R14 (#3): the query path reranks — gate it too
        )
        if _rag_ks_block is not None:
            return _rag_ks_block

        # FIX rag#9b: an unknown/unconfigured vector_db_type is a CONFIGURATION
        # error, not a security threat. Validate it here BEFORE running the
        # pipeline — otherwise the retriever surfaces it as action="block"
        # threat_type="client_unavailable" (a misleading 403 threat block) and
        # _resolve_vector_client silently falls back to a different provider.
        # Mirror the create/delete handlers' no_provider_configured → 422 pattern.
        _known_vector_provider_types = {"pinecone", "chroma", "milvus", "custom"}
        _org_provider_types = [effective_vector_db_type]
        if effective_vector_db_type == "custom":
            _org_provider_types.append("chroma")
        _provider_configured = (
            effective_vector_db_type in _known_vector_provider_types
            and (
                effective_vector_db_type in VECTOR_CLIENTS
                or (
                    org_id_for_client is not None
                    and VECTOR_PROVIDER_SYNC is not None
                    and any(
                        VECTOR_PROVIDER_SYNC.get_provider_config(org_id_for_client, ptype) is not None
                        for ptype in _org_provider_types
                    )
                )
            )
        )
        if not _provider_configured:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "no_provider_configured",
                    "code": "no_provider_configured",
                    "message": f"No vector provider configured for type: {effective_vector_db_type}",
                },
            )

        resolved_vector_client, _ = _resolve_vector_client(effective_vector_db_type, org_id=org_id_for_client)
        result = await RAG_PIPELINE.execute(
            query_text=query_text,
            collection_name=collection_name,
            project_id=str(project_id),
            vector_db_type=effective_vector_db_type,
            n_results=n_results,
            where_filter=where_filter,
            namespace=namespace,
            policy=rag_policy,
            key_hash=getattr(auth_ctx, "key_hash", ""),
            organization_id=getattr(auth_ctx, "organization_id", None) if auth_ctx else None,
            user_id=getattr(auth_ctx, "user_id", None) if auth_ctx else None,
            vector_client_override=resolved_vector_client,
        )

        if result.action == "block":
            METRICS["blocked"] += 1
            last = result.pipeline_context.stages[-1] if result.pipeline_context and result.pipeline_context.stages else None
            elapsed_rag_ms = (time.perf_counter() - start_rag) * 1000
            _emit_telemetry(
                status_code=403,
                event_type="rag_query",
                model="",
                user_id=getattr(auth_ctx, "user_id", ""),
                project_id=str(project_id),
                key_prefix=getattr(auth_ctx, "prefix", ""),
                organization_id=getattr(auth_ctx, "organization_id", None),
                action="block",
                risk_score=last.verdict.confidence if last else 0.0,
                threat_type=last.verdict.threat_type if last else "",
                pipeline_stage=last.stage_name if last else "unknown",
                latency_ms=elapsed_rag_ms,
                metadata={
                    "collection": collection_name,
                    "vector_db_type": vector_db_type,
                    "request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                    "pipeline_request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                    "blocked_at_stage": last.stage_name if last else "unknown",
                    "detail": last.verdict.detail if last else "",
                    "module": "1.3",
                    "module_id": "1.3",
                },
            )
            return JSONResponse(
                status_code=403,
                content={
                    "error": "blocked",
                    "message": f"RAG pipeline blocked at {last.stage_name if last else 'unknown'}: {last.verdict.detail if last else ''}",
                    "code": "rag_pipeline_blocked",
                    "threat_type": last.verdict.threat_type if last else "",
                    "pipeline_stage": last.stage_name if last else "",
                },
            )

        METRICS["allowed"] += 1
        elapsed_rag_ms = (time.perf_counter() - start_rag) * 1000
        LOG.info(
            "RAG query completed (project=%s, collection=%s, retrieved=%d, filtered=%d, stages=%d)",
            project_id, collection_name, result.total_retrieved, result.filtered_count,
            len(result.pipeline_context.stages) if result.pipeline_context else 0,
        )

        # ── Top-level RAG query telemetry (feeds general dashboards / threat feed) ──
        _emit_telemetry(
            status_code=200,
            event_type="rag_query",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=str(project_id),
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action=result.action,
            risk_score=0.0,
            pipeline_stage="rag",
            latency_ms=elapsed_rag_ms,
            metadata={
                "collection": collection_name,
                "vector_db_type": vector_db_type,
                "total_retrieved": result.total_retrieved,
                "filtered_count": result.filtered_count,
                "context_binding_id": result.context_binding_id or "",
                "request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                "pipeline_request_id": result.pipeline_context.request_id if result.pipeline_context else "",
                "stages_executed": len(result.pipeline_context.stages) if result.pipeline_context else 0,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        # Do NOT leak the internal Bedrock guard model id (e.g.
        # "openai.gpt-oss-120b-1:0") to tenants. Surface a generic label when a
        # downgrade occurred, and scrub any "downgrade to {model}" substring from
        # the client-facing scan_verdict.detail. The raw id stays on internal
        # telemetry channels.
        _client_model_downgrade = "zeroshield-safe" if result.model_downgrade else ""

        _client_scan_verdict = result.scan_verdict
        if isinstance(_client_scan_verdict, dict):
            import copy as _copy
            import re as _re

            _client_scan_verdict = _copy.deepcopy(_client_scan_verdict)
            _detail = _client_scan_verdict.get("detail")
            if isinstance(_detail, str) and _detail:
                # Replace "downgrade to <model id>" → "downgrade to zeroshield-safe".
                _detail = _re.sub(
                    r"(downgrade to\s+)\S+",
                    r"\1zeroshield-safe",
                    _detail,
                    flags=_re.IGNORECASE,
                )
                # Also scrub any bare internal guard model id if it leaked verbatim.
                if result.model_downgrade:
                    _detail = _detail.replace(str(result.model_downgrade), "zeroshield-safe")
                _client_scan_verdict["detail"] = _detail

        # ── E11: client-egress retrieved-context PII backstop ──
        # The generation-time PII backstop (``_redact_retrieved_pii``) lives in the
        # GeneratorStage, which is gated by ``rag_generator_enabled`` (default OFF).
        # On the DEFAULT guardrails-only / ranker-only paths the generator never
        # runs, so a document whose PII the ranker did not drop — especially a
        # scanner-missed bare phone — would reach the client RAW. Re-run the SAME
        # GATED helper here, at the single client-egress choke point, over every
        # returned document's content (and any returned context_chunks). This
        # covers ALL pipeline paths uniformly and is idempotent: the helper only
        # fires its bare-digit backstop when the typed redactor already found PII
        # in the chunk, so legitimate document/order numbers in clean citations are
        # NOT mangled, and a chunk the generator already redacted is left unchanged.
        from rag_pipeline.generator_stage import (
            _redact_retrieved_pii as _egress_redact_pii,
            _redact_metadata_values as _egress_redact_meta,
        )

        # ── E11b: UNCONDITIONAL client-egress indirect-injection backstop ──
        # Retrieved-document injection ("ignore all previous instructions",
        # hidden HTML/ChatML directives, persona-reassignment) is otherwise only
        # scanned in the RANKER stage (context_guard.scan_documents), which is OFF
        # by default (rag_ranker_enabled=False) and only forced on by certain
        # policies. So a pre-poisoned vector in a collection WITHOUT that policy is
        # returned to the client UNSCANNED. Run the SAME detector the ranker uses
        # (CONTEXT_GUARD.scan_single_document → INDIRECT_INJECTION_PATTERNS +
        # HIDDEN_INSTRUCTION_PATTERNS) here, at the single client-egress choke
        # point, over EVERY returned document — regardless of rag_ranker_enabled /
        # policy. A document the detector flags as injection / hidden-instruction
        # is DROPPED (never served); secret/PII/toxicity stay the responsibility of
        # the PII-redaction backstop below (toxicity is a flag, not a drop). The
        # scan is sync (ThreadPoolExecutor-backed); call it via asyncio.to_thread to
        # keep the async handler non-blocking. Fail-safe: if CONTEXT_GUARD is None,
        # skip entirely (no crash, no behaviour change).
        _egress_dropped_injection: list[dict] = []
        _egress_documents = result.documents
        if CONTEXT_GUARD is not None and isinstance(_egress_documents, list):
            _kept_after_injection: list = []
            for _doc in _egress_documents:
                if not isinstance(_doc, dict):
                    _kept_after_injection.append(_doc)
                    continue
                # G67: coerce a non-str (list of content-parts / dict) content to text so a
                # poisoned document whose content is list/dict-shaped can't SKIP the egress
                # indirect-injection backstop (the str-only gate served it unscanned).
                _scan_text = _content_to_text(_doc.get("content"))
                if _scan_text:
                    try:
                        _inj_verdict = await asyncio.to_thread(
                            CONTEXT_GUARD._scan_single_document_sync, _scan_text
                        )
                    except Exception:  # noqa: BLE001 — a scanner error must not 500 the egress
                        LOG.warning(
                            "Egress injection scan failed (fail-safe: doc kept) doc_id=%s",
                            _doc.get("_doc_id") or _doc.get("id") or "",
                        )
                        _inj_verdict = None
                    if (
                        _inj_verdict is not None
                        and getattr(_inj_verdict, "action", "allow") == "block"
                        and getattr(_inj_verdict, "threat_type", "")
                        in ("indirect_injection", "hidden_instruction")
                    ):
                        _did = _doc.get("_doc_id") or _doc.get("id") or ""
                        _egress_dropped_injection.append({
                            "id": _did,
                            "threat_type": getattr(_inj_verdict, "threat_type", ""),
                            "detail": getattr(_inj_verdict, "detail", ""),
                        })
                        LOG.warning(
                            "Egress backstop DROPPED retrieved doc for %s (id=%s)",
                            getattr(_inj_verdict, "threat_type", ""), _did,
                        )
                        continue  # do NOT serve this document
                _kept_after_injection.append(_doc)
            _egress_documents = _kept_after_injection
            result.documents = _egress_documents

        if isinstance(_egress_documents, list):
            for _doc in _egress_documents:
                if not isinstance(_doc, dict):
                    continue
                _content = _doc.get("content")
                if isinstance(_content, str) and _content:
                    _doc["content"] = _egress_redact_pii(_content)
                elif isinstance(_content, (list, dict)):
                    # G67: a non-str content bypassed the str-only PII backstop -> PII/secret
                    # served RAW to the client. Flatten to text and redact; replace the content
                    # ONLY when PII was actually masked, so a benign list/dict doc keeps its
                    # original structure (no over-mutation of clean documents).
                    _flat = _content_to_text(_content)
                    if _flat:
                        _redacted_flat = _egress_redact_pii(_flat)
                        if _redacted_flat != _flat:
                            _doc["content"] = _redacted_flat
                # E11 fail-open fix: also mask PII in metadata VALUES (not just the
                # ranker's declared sensitive_fields) before client egress — PII in
                # an undeclared metadata field otherwise returns raw to the client.
                _meta = _doc.get("metadata")
                if isinstance(_meta, (dict, list)):
                    _doc["metadata"] = _egress_redact_meta(_meta)
                # Redact any per-document context_chunks (list[str]) if a path
                # surfaces them on the document object.
                _doc_chunks = _doc.get("context_chunks")
                if isinstance(_doc_chunks, list):
                    _doc["context_chunks"] = [
                        _egress_redact_pii(_c) if isinstance(_c, str) and _c else _c
                        for _c in _doc_chunks
                    ]

        _egress_context_chunks = getattr(result, "context_chunks", None)
        if isinstance(_egress_context_chunks, list):
            _egress_context_chunks = [
                _egress_redact_pii(_c) if isinstance(_c, str) and _c else _c
                for _c in _egress_context_chunks
            ]

        # When the egress backstop dropped injection-bearing documents, reflect the
        # drop in the response: bump filtered_count and surface the dropped set in
        # the client-facing scan_verdict (matching the existing partial-success
        # shape: a flagged/filtered list alongside the documents/ids that survived).
        _egress_filtered_count = result.filtered_count
        if _egress_dropped_injection:
            _egress_filtered_count = (result.filtered_count or 0) + len(_egress_dropped_injection)
            if isinstance(_client_scan_verdict, dict):
                _client_scan_verdict.setdefault("flagged_documents", [])
                _client_scan_verdict["egress_filtered"] = _egress_dropped_injection
                _client_scan_verdict["egress_filtered_count"] = len(_egress_dropped_injection)
                if not _client_scan_verdict.get("threat_type"):
                    _client_scan_verdict["threat_type"] = _egress_dropped_injection[0]["threat_type"]

        response_content = {
            "collection": collection_name,
            "query": query_text,
            "documents": _egress_documents,
            "total_retrieved": result.total_retrieved,
            "filtered_count": _egress_filtered_count,
            "scan_verdict": _client_scan_verdict,
            "pipeline_audit": result.pipeline_audit,
            "context_binding_id": result.context_binding_id or "",
            "canary_word": result.canary_word or "",
            "model_downgrade": _client_model_downgrade,
        }

        headers = {}
        if result.context_binding_id:
            headers["X-ZeroShield-RAG-Context-ID"] = result.context_binding_id
        if result.pipeline_context:
            headers["X-ZeroShield-Pipeline-Request-ID"] = result.pipeline_context.request_id

        return JSONResponse(content=response_content, headers=_latin1_safe_headers(headers))

    finally:
        METRICS["active_connections"] -= 1
        METRICS["sum_latency_ms"] += (time.perf_counter() - start_rag) * 1000


# ---------------------------------------------------------------------------
# RAG Ingestion, Document Management & Collection Management
# ---------------------------------------------------------------------------

@app.post(
    "/v1/rag/ingest",
    summary="Ingest documents into a vector DB collection (with firewall enforcement)",
    tags=["RAG"],
)
async def rag_ingest(request: Request):
    """
    Production document ingestion with full policy enforcement.

    Flow:
    1. Authenticate via Gateway API Key
    2. Look up VectorCollectionPolicy for the target collection
    3. Verify 'insert' operation is allowed
    4. Scan document content via ContextGuard (injection, PII, toxicity)
    5. Generate embeddings and upsert into the vector database
    6. Return ingestion receipt with scan verdict
    """
    METRICS["total_requests"] += 1
    METRICS["active_connections"] += 1
    start = time.perf_counter()

    try:
        # Gate ONLY on the firewall being disabled (VECTOR_POLICY_SYNC unset).
        # Do NOT gate on the global env-var VECTOR_CLIENTS dict — under
        # guardrails-only BYOK it is intentionally empty and the real client is
        # resolved per-org below. The old `not VECTOR_CLIENTS` clause hard-503'd
        # the ENTIRE write path in BYOK prod, so the ingest-time ContextGuard /
        # PII / Tier-2 scan never ran (rag #2).
        if VECTOR_POLICY_SYNC is None:
            return JSONResponse(
                status_code=503,
                content={"error": "service_unavailable", "message": "RAG firewall not enabled."},
            )

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        # Boundary validation: a non-object body or type-confused fields would
        # otherwise crash `.strip()` / iterate a dict/string as "documents"
        # (KeyError / silent char-by-char ingest) -> unhandled 500. Mirror the
        # rag_query guard.
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "Request body must be a JSON object.", "code": "invalid_request_body"},
            )
        _coll = body.get("collection", "")
        collection_name = _coll.strip() if isinstance(_coll, str) else ""
        documents = body.get("documents", [])
        ids = body.get("ids", [])
        metadatas = body.get("metadatas", [])
        _vdb = body.get("vector_db_type", "pinecone")
        vector_db_type = (_vdb.strip() if isinstance(_vdb, str) else "pinecone") or "pinecone"

        # Also accept single-document shorthand
        if not documents and body.get("content"):
            documents = [body["content"]]
            ids = [body.get("id", f"doc-{_uuid.uuid4().hex[:8]}")]
            metadatas = [body.get("metadata", {})]

        # `documents` MUST be a list. A dict raises KeyError at `documents[0]`;
        # a bare string would be iterated char-by-char and each character stored
        # as its own "document" (silent data-corruption + a 200). ids/metadatas
        # must be lists too, else `len()` on a scalar raises -> 500.
        if documents and not isinstance(documents, list):
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "'documents' must be an array.", "code": "invalid_documents"},
            )
        if not isinstance(ids, list):
            ids = []
        if not isinstance(metadatas, list):
            metadatas = []

        # FI: cap batch COUNT + total size BEFORE the per-document scan fan-out
        # (each doc runs an inline ContextGuard scan + optional Tier-2 upstream
        # call). Sibling capacity endpoints (/v1/vector/upsert, /v1/embeddings)
        # cap this; rag_ingest never did, so a huge batch could exhaust the worker
        # via scan fan-out. The TPM limiter ahead is fail-open by design.
        if isinstance(documents, list):
            _max_docs = int(os.getenv("RAG_INGEST_MAX_DOCS", "1000"))
            if len(documents) > _max_docs:
                return JSONResponse(
                    status_code=413,
                    content={"error": "payload_too_large", "message": f"Batch exceeds {_max_docs} documents.", "code": "rag_batch_too_large", "max_documents": _max_docs},
                )
            _max_chars = int(os.getenv("RAG_INGEST_MAX_CHARS", "2000000"))
            if sum(len(str(d)) for d in documents) > _max_chars:
                return JSONResponse(
                    status_code=413,
                    content={"error": "payload_too_large", "message": "Ingest payload too large.", "code": "rag_input_too_large"},
                )

        if not collection_name:
            return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
        if not _is_valid_collection_name(collection_name):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                    "code": "rag_namespace_violation",
                },
            )
        # Case-fold AFTER validation so the policy lookup and the tenant namespace
        # both key on the canonical name (R10) — a case-variant write must land in
        # the SAME namespace the deny/block_sensitive policy guards.
        collection_name = _normalize_collection_name(collection_name)
        if not documents:
            return JSONResponse(status_code=400, content={"error": "Missing 'documents' or 'content'."})

        # Auto-generate IDs if not provided
        if not ids or len(ids) != len(documents):
            ids = [f"doc-{_uuid.uuid4().hex[:8]}" for _ in documents]
        if not metadatas or len(metadatas) != len(documents):
            metadatas = [{"source": "gateway"} for _ in documents]
        # Vector providers expect metadata dictionaries for each document.
        metadatas = [m if m else {"source": "gateway"} for m in metadatas]

        # ── Auth ──
        auth_ctx = getattr(request.state, "auth_context", None)
        if auth_ctx is None:
            return JSONResponse(status_code=403, content={"error": "Authentication required."})
        project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace
        org_id = coerce_org_id(getattr(auth_ctx, "organization_id", None))

        # ── Phase 1 §1.1: per-org TPM ceiling on RAG ingest (largest write surface) ──
        _ingest_text = " ".join(str(d) for d in documents if d)
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="rag_ingest_blocked",
            model="",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=project_id,
            estimated_tokens=_estimate_request_tokens(_ingest_text),
        )
        if _rl_resp is not None:
            return _rl_resp

        # R12 (#3): per-org burst (req/s) + RPM ceiling — rag_ingest was missing the
        # dampener that rag_query + /v1/embeddings already apply, so a client could
        # hammer ingest to evade the per-org rate ceiling (DoS / amplification).
        _burst_resp = await _enforce_org_burst_rpm(
            auth_ctx,
            event_type="rag_ingest_blocked",
            model="",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=project_id,
        )
        if _burst_resp is not None:
            return _burst_resp

        # ── Policy enforcement ──
        policy = VECTOR_POLICY_SYNC.get_policy(project_id, collection_name, organization_id=getattr(auth_ctx, "organization_id", None))
        if policy is None:
            _alert_vector_policy_miss(
                project_id=project_id, collection_name=collection_name,
                organization_id=getattr(auth_ctx, "organization_id", None),
                user_id=getattr(auth_ctx, "user_id", ""), operation="insert",
            )
            # Fail CLOSED on a write to any named (non-'default') collection with no
            # policy — persisting documents under a case-variant / unknown name
            # would let PII land in a namespace a deny/block_sensitive policy is
            # meant to guard (R10).
            if collection_name != "default":
                METRICS["blocked"] += 1
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Access denied: no policy for collection '{collection_name}'.",
                        "code": "rag_access_denied",
                    },
                )
            policy = {
                "enabled": True, "collection_name": collection_name,
                "project_id": project_id, "default_action": "monitor",
                "allowed_operations": ["query", "insert"],
                "max_results_per_query": 50, "require_context_scan": True,
            }

        if not policy.get("enabled", False):
            METRICS["blocked"] += 1
            return JSONResponse(status_code=403, content={"error": "Policy disabled for this collection."})

        # Per-operation access decision: ``default_action`` governs operations
        # NOT in ``allowed_operations``. Insert is permitted iff it is listed;
        # only when it is NOT do we apply the deny/block default (access_denied)
        # vs a plain operation_denied. (Gating deny before allowed_operations
        # wrongly blocked inserts on collections that explicitly allow them.)
        allowed_ops = policy.get("allowed_operations", [])
        _default_action = str(policy.get("default_action", "monitor") or "monitor").strip().lower()
        if "insert" not in allowed_ops:
            METRICS["blocked"] += 1
            if _default_action in ("deny", "block"):
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Access denied: policy for collection '{collection_name}' denies access.",
                        "code": "rag_access_denied",
                    },
                )
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "message": "Insert operation not permitted.", "code": "rag_operation_denied"},
            )

        # BYOK availability — checked AFTER the policy/deny/operation checks so a
        # denied collection returns 403 (not a provider-status 422). The env-var
        # VECTOR_CLIENTS dict is empty under guardrails-only BYOK, so check the
        # per-org resolver; a missing provider is a 422 (org has not connected a
        # vector DB), NOT a 503 (which reads as "the firewall is down").
        if not rag_vector_available(org_id, vector_clients=VECTOR_CLIENTS, provider_sync=VECTOR_PROVIDER_SYNC):
            return JSONResponse(
                status_code=422,
                content={
                    "error": "no_provider_configured",
                    "message": "No vector database is connected for this organization. Connect a BYOK vector provider to ingest documents.",
                    "code": "rag_no_provider",
                },
            )

        # NOTE: async ingest used to 202 HERE — BEFORE any content scan — and
        # hand the RAW documents to a no-op worker, so guardrails never ran and
        # nothing was stored. The scan + typed-placeholder redaction below now
        # ALWAYS run inline; only the (slow) embed+upsert of the already-cleaned
        # documents is optionally handed to the worker further down.

        # ── Per-org RAG guardrail config (redaction + Tier-2 toggles) ──
        org_slug = getattr(auth_ctx, "org_slug", "") or ""
        org_config = (
            CONFIG_SYNC.get_config(org_slug)
            if (CONFIG_SYNC is not None and org_slug)
            else {}
        ) or {}
        # B5 (RAG redaction default / nothing raw at rest): the typed-placeholder
        # pass is SAFE-BY-DEFAULT. The PII-safety guarantee at rest is already
        # policy-forced by ``input_scan_enabled`` (default True) via the unified
        # ``_scan_redact_embedding_inputs`` / ``_scan_redact_metadata`` pass below,
        # but defaulting this toggle ON adds structure-preserving typed redaction
        # ([EMAIL]/[SSN]) as defense-in-depth so an org that never set the flag
        # still never embeds/stores raw PII. An org may still explicitly disable it.
        rag_redaction_enabled = bool(org_config.get("rag_redaction_enabled", True))
        rag_tier2_enabled = bool(org_config.get("rag_tier2_enabled", False))

        # R12 (#2/#6): gate the RAG ingest embedding model on operator kill-switch
        # + model-state (parity with /v1/embeddings + rag_query). The embed+upsert
        # below uses byok_embedder, bypassing the I3-Med /v1/embeddings gate.
        _ingest_ks_block = await _rag_embedding_killswitch_block(
            org_slug, org_id,
            (getattr(auth_ctx, "prefix", "") if auth_ctx else ""),
            policy.get("vector_db_type", vector_db_type),
            policy.get("embedding_model", ""),
        )
        if _ingest_ks_block is not None:
            return _ingest_ks_block

        # ── Content scanning: Tier-1 (ContextGuard) + optional Tier-2 (ML guard) ──
        # Per document. Tier-2 fails OPEN on Bedrock-breaker degradation
        # (org_tier2_strict=False) so a guard-model outage never 451s an ingest.
        scan_results = []
        blocked_indices: set[int] = set()
        if policy.get("require_context_scan", True):
            for i, doc_text in enumerate(documents):
                if isinstance(doc_text, dict):
                    text_to_scan = doc_text.get("content", "") or doc_text.get("text", "") or str(doc_text)
                else:
                    text_to_scan = str(doc_text)
                # FA + RAG-C5-META-BYPASS: scan this doc's METADATA string values.
                # Metadata is attacker-controllable on ingest and round-trips to the
                # caller (and into downstream RAG prompts). Scan BOTH the top-level
                # metadatas[i] AND the inline-document-dict's own ``d['metadata']`` —
                # the inline shape is what STORAGE persists below, so scanning only the
                # synthetic top-level placeholder let a poisoned inline metadata be
                # stored UNSCANNED. _collect_nested_strings folds every string in the
                # (possibly nested) metadata so a secret/injection can't hide in a
                # nested dict/list either.
                _meta_sources = []
                _meta_i = metadatas[i] if i < len(metadatas) else None
                if isinstance(_meta_i, dict):
                    _meta_sources.append(_meta_i)
                if isinstance(doc_text, dict) and isinstance(doc_text.get("metadata"), dict):
                    _meta_sources.append(doc_text["metadata"])
                _mvals: list[str] = []
                for _ms in _meta_sources:
                    _mvals.extend(_collect_nested_strings(_ms))
                if _mvals:
                    text_to_scan = (text_to_scan or "") + "\n" + "\n".join(_mvals)
                action = "allow"
                threats: list[str] = []
                if CONTEXT_GUARD is not None:
                    v = await CONTEXT_GUARD.scan_single_document(text_to_scan)
                    if v.threat_type:
                        threats.append(v.threat_type)
                    if v.action == "block":
                        action = "block"
                if rag_tier2_enabled and INPUT_SCANNER is not None and action != "block":
                    try:
                        t2 = await INPUT_SCANNER.scan_prompt_with_tier2(
                            text_to_scan,
                            is_rag=True,
                            org_tier2_override=True,
                            org_slug=org_slug,
                            org_tier2_strict=False,
                            request_id=_REQUEST_ID.get(""),
                        )
                        if getattr(t2, "threat_type", ""):
                            threats.append(t2.threat_type)
                        if t2.action == "block":
                            action = "block"
                    except Exception:
                        LOG.warning("RAG ingest Tier-2 scan failed (fail-open) for doc index %d", i)
                scan_results.append({"index": i, "action": action, "threats": threats})
                if action == "block":
                    blocked_indices.add(i)

        # ── Partial success: ingest the allowed documents, skip blocked ones ──
        allowed_indices = [i for i in range(len(documents)) if i not in blocked_indices]
        if not allowed_indices:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=422,
                content={
                    "error": "all_documents_blocked",
                    "message": f"All {len(documents)} document(s) were blocked by content scanning.",
                    "code": "rag_content_blocked",
                    "scan_results": scan_results,
                },
            )

        # Normalize the ALLOWED documents to list[str], applying vector-safe
        # typed-placeholder redaction before embedding when the org enabled it
        # (so stored vectors never carry raw PII; [EMAIL]/[SSN] preserve the
        # sentence structure for meaningful similarity).
        if rag_redaction_enabled:
            from typed_placeholder_redactor import detect_and_redact_typed

        redacted_count = 0
        doc_strings = []
        normalized_ids = []
        normalized_metas = []
        for i in allowed_indices:
            d = documents[i]
            if isinstance(d, dict):
                # G66: a document's `content`/`text` may be a LIST of content-part dicts or
                # a dict (non-conforming, same shape class as G57). The raw `or` chain
                # grabbed the truthy list -> detect_and_redact_typed CRASHED (TypeError) and
                # _scan_redact_embedding_inputs SKIPPED it (non-str), so PII/secrets were
                # embedded + PERSISTED unscanned (at-rest leak). Coerce to text first so the
                # ingest scan always sees a string.
                text = _content_to_text(d.get("content")) or _content_to_text(d.get("text")) or str(d)
                doc_id = d.get("id", ids[i] if i < len(ids) else f"doc-{_uuid.uuid4().hex[:8]}")
                meta = d.get("metadata", metadatas[i] if i < len(metadatas) else {"source": "gateway"})
            else:
                text = str(d)
                doc_id = ids[i] if i < len(ids) else f"doc-{_uuid.uuid4().hex[:8]}"
                meta = metadatas[i] if i < len(metadatas) else {"source": "gateway"}
            if rag_redaction_enabled:
                _r = detect_and_redact_typed(text)
                if _r.redacted:
                    redacted_count += 1
                    text = _r.text
            doc_strings.append(text)
            normalized_ids.append(doc_id)
            if isinstance(meta, dict):
                # FA-low: strip caller-supplied provenance trust-boost keys so a
                # tenant cannot self-assert 'verified_source' / 'created_by'=system
                # to inflate the ranker's trust score — these are platform-asserted
                # signals and must be server-stamped, never honored from inbound
                # metadata.
                meta = {k: v for k, v in meta.items() if k not in ("verified_source", "created_by")}
                # G2b: document CONTENT is typed-redacted above, but caller-supplied
                # METADATA VALUES were NOT — a PII/secret hidden in a metadata field
                # (e.g. {"author": "ssn 123-45-6789"}) was stored verbatim AND
                # round-trips back to RAG-query callers and into downstream prompts.
                # When the org enabled rag_redaction, mask string metadata values
                # with the SAME deterministic typed redactor, recursing into nested
                # dict/list values so a secret can't hide one level deep.
                if rag_redaction_enabled:
                    def _redact_meta_value(_v):
                        if isinstance(_v, str):
                            _mr = detect_and_redact_typed(_v)
                            if _mr.redacted:
                                nonlocal redacted_count
                                redacted_count += 1
                            return _mr.text
                        if isinstance(_v, dict):
                            return {_k: _redact_meta_value(_vv) for _k, _vv in _v.items()}
                        if isinstance(_v, (list, tuple)):
                            return [_redact_meta_value(_item) for _item in _v]
                        return _v
                    meta = {k: _redact_meta_value(v) for k, v in meta.items()}
            normalized_metas.append(meta if meta else {"source": "gateway"})

        # G3: UNIFY the embedding-input redaction with /v1/embeddings. The docs
        # above were scanned by CONTEXT_GUARD (+ optional Tier-2) and optionally
        # typed-placeholder-redacted, but those run only when their toggles are on
        # and do NOT use the SAME deterministic INPUT_SCANNER redactor that the
        # /v1/embeddings path now applies — so a PII/secret value could still be
        # embedded verbatim by the upstream provider on ingest. Run the IDENTICAL
        # helper here (same input_scan_enabled gate, same byte-verified fail-closed)
        # so both surfaces redact identically. A doc whose PII genuinely cannot be
        # masked is SKIPPED (not embedded) — matching the existing partial-success
        # contract (parallel ids/metas/strings stay aligned).
        _emb_redacted_docs: list[str] = []
        _emb_kept_ids: list = []
        _emb_kept_metas: list = []
        _emb_blocked_count = 0
        for _di, _dtext in enumerate(doc_strings):
            _red, _blk = await _scan_redact_embedding_inputs([_dtext], org_config)
            if _blk is not None:
                _emb_blocked_count += 1
                _bidx = allowed_indices[_di] if _di < len(allowed_indices) else _di
                blocked_indices.add(_bidx)
                scan_results.append({
                    "index": _bidx,
                    "action": "block",
                    "threats": ["pii"],
                    "reason": _blk.get("reason"),
                })
                continue
            _emb_redacted_docs.append(_red[0])
            _emb_kept_ids.append(normalized_ids[_di])
            # G2-metadata: redact metadata VALUES at the same input_scan_enabled gate
            # as the content above (not only under the off-by-default rag_redaction).
            _emb_kept_metas.append(await _scan_redact_metadata(normalized_metas[_di], org_config))
        doc_strings = _emb_redacted_docs
        normalized_ids = _emb_kept_ids
        normalized_metas = _emb_kept_metas
        allowed_indices = [i for i in allowed_indices if i not in blocked_indices]
        if not allowed_indices:
            METRICS["blocked"] += 1
            return JSONResponse(
                status_code=422,
                content={
                    "error": "all_documents_blocked",
                    "message": "All document(s) were blocked by content scanning.",
                    "code": "rag_content_blocked",
                    "scan_results": scan_results,
                },
            )

        # ── Async upsert (decision: implement the worker) ──
        # The documents are ALREADY scanned + redacted above, so guardrails ran
        # inline and the 202 response carries the real blocked/redacted counts +
        # scan_results. Only the slow embed+upsert of the CLEANED docs is handed
        # to the worker, which re-resolves the same per-org BYOK client.
        if _async_vector_ingest_enabled:
            job_id = f"rag-ingest-{_uuid.uuid4().hex[:12]}"
            await enqueue_job(
                job_type="vector_ingest",
                request_id=job_id,
                org_id=getattr(auth_ctx, "organization_id", None),
                payload={
                    "job_id": job_id,
                    "organization_id": getattr(auth_ctx, "organization_id", None),
                    "project_id": project_id,
                    "collection": collection_name,
                    "vector_db_type": vector_db_type,
                    "documents": doc_strings,
                    "ids": normalized_ids,
                    "metadatas": normalized_metas,
                },
            )
            METRICS["allowed"] += 1
            elapsed_ms = (time.perf_counter() - start) * 1000
            _emit_telemetry(
                status_code=202, event_type="rag_ingest", model="",
                user_id=getattr(auth_ctx, "user_id", ""), project_id=project_id,
                key_prefix=getattr(auth_ctx, "prefix", ""),
                organization_id=getattr(auth_ctx, "organization_id", None),
                action="allow", risk_score=0.0,
                metadata={
                    "collection": collection_name, "provider": vector_db_type,
                    "doc_count": len(doc_strings), "queued": True,
                    "module": "1.3", "module_id": "1.3",
                },
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "accepted",
                    "job_id": job_id,
                    "collection": collection_name,
                    "provider": vector_db_type,
                    "queued": True,
                    "ingested_count": len(allowed_indices),
                    "blocked_count": len(blocked_indices),
                    "redacted_count": redacted_count,
                    "scan_results": scan_results,
                    "processing_time_ms": round(elapsed_ms, 1),
                },
            )

        # ── Synchronous upsert: resolve the per-org BYOK client and store now ──
        client, used_vdb = _resolve_vector_client(vector_db_type, org_id)
        if client is None:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "no_provider_configured",
                    "message": "No vector client is resolvable for this organization.",
                    "code": "rag_no_provider",
                },
            )
        vector_db_type = used_vdb or vector_db_type

        try:
            count = await client.add(
                collection_name=collection_name,
                documents=doc_strings,
                ids=normalized_ids,
                metadatas=normalized_metas,
                project_id=project_id,
            )
        except Exception as exc:
            LOG.exception("RAG ingest failed (provider=%s, collection=%s)", vector_db_type, collection_name)
            return JSONResponse(
                status_code=500,
                content={"error": "ingest_failed", "message": "RAG ingestion failed."},
            )

        METRICS["allowed"] += 1
        elapsed_ms = (time.perf_counter() - start) * 1000
        LOG.info(
            "RAG ingest completed (project=%s, collection=%s, provider=%s, docs=%d, %.1fms)",
            project_id, collection_name, vector_db_type, count, elapsed_ms,
        )

        _emit_telemetry(
            status_code=200,
            event_type="rag_ingest",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=project_id,
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action="allow",
            risk_score=0.0,
            metadata={
                "collection": collection_name,
                "provider": vector_db_type,
                "doc_count": count,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(content={
            "status": "ingested" if not blocked_indices else "partial",
            "collection": collection_name,
            "provider": vector_db_type,
            "doc_count": count,
            "ingested_count": len(allowed_indices),
            "blocked_count": len(blocked_indices),
            "redacted_count": redacted_count,
            "ids": normalized_ids,
            "scan_results": scan_results,
            "processing_time_ms": round(elapsed_ms, 1),
        })
    finally:
        METRICS["active_connections"] -= 1
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000


@app.delete(
    "/v1/rag/documents",
    summary="Delete documents from a vector DB collection",
    tags=["RAG"],
)
async def rag_delete_documents(request: Request):
    """Delete specific documents by ID from a collection with policy enforcement."""
    METRICS["total_requests"] += 1
    start = time.perf_counter()

    try:
        # Gate only on the firewall being disabled (NOT the empty env-var dict;
        # the per-org client is resolved below) — same rag #2 fix as ingest.
        if VECTOR_POLICY_SYNC is None:
            return JSONResponse(status_code=503, content={"error": "RAG firewall not enabled."})

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "message": "Request body must be a JSON object.", "code": "invalid_request_body"},
            )
        _coll = body.get("collection", "")
        collection_name = _coll.strip() if isinstance(_coll, str) else ""
        doc_ids = body.get("ids", [])
        if not isinstance(doc_ids, list):
            doc_ids = []
        _vdb = body.get("vector_db_type", "")
        vector_db_type = _vdb.strip() if isinstance(_vdb, str) else ""
        # Remember whether the caller pinned a provider; if not, delete is
        # attempted across ALL of the org's active providers (a doc id can live
        # in any of them) so a multi-provider org never gets a silent no-op.
        _vdb_explicit = bool(vector_db_type)
        if not vector_db_type:
            vector_db_type = "pinecone"

        if not collection_name:
            return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
        if not _is_valid_collection_name(collection_name):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "forbidden",
                    "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                    "code": "rag_namespace_violation",
                },
            )
        # Case-fold AFTER validation so the policy lookup and the tenant namespace
        # both key on the canonical name (R10).
        collection_name = _normalize_collection_name(collection_name)
        if not doc_ids:
            return JSONResponse(status_code=400, content={"error": "Missing 'ids'."})

        auth_ctx = getattr(request.state, "auth_context", None)
        if auth_ctx is None:
            return JSONResponse(status_code=403, content={"error": "Authentication required."})
        project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace
        org_id = coerce_org_id(getattr(auth_ctx, "organization_id", None))
        # NOTE: the no-provider 422 is enforced AFTER the delete-op policy check
        # below (see `if not targets:`), so a denied collection returns 403 — not
        # a provider-status 422.

        # ── Phase 1 §1.1: per-org TPM ceiling on RAG delete (abuse vector) ──
        _rl_resp = await _enforce_org_tpm_rate_limit(
            auth_ctx,
            event_type="rag_delete_blocked",
            model="",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=project_id,
            estimated_tokens=max(20, len(doc_ids) * 4),
        )
        if _rl_resp is not None:
            return _rl_resp

        # R14 (#10): per-org burst (req/s) + RPM ceiling — rag_delete had only the
        # TPM gate (org_tpm_limit is usually 0 so it never fires); mirror
        # rag_query/rag_ingest so delete can't be hammered to evade the rate ceiling.
        _burst_resp = await _enforce_org_burst_rpm(
            auth_ctx,
            event_type="rag_delete_blocked",
            model="",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=project_id,
        )
        if _burst_resp is not None:
            return _burst_resp

        policy = VECTOR_POLICY_SYNC.get_policy(project_id, collection_name, organization_id=getattr(auth_ctx, "organization_id", None))
        if policy is None:
            # Parity with query/ingest: a policy MISS must alert loudly (it was
            # the only op that silently fell open).
            _alert_vector_policy_miss(
                project_id=project_id, collection_name=collection_name,
                organization_id=getattr(auth_ctx, "organization_id", None),
                user_id=getattr(auth_ctx, "user_id", ""), operation="delete",
            )
            # Fail CLOSED for any named (non-'default') collection with no policy
            # (R10) — a case-variant name must not bypass a deny policy.
            if collection_name != "default":
                METRICS["blocked"] += 1
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "forbidden",
                        "message": f"Access denied: no policy for collection '{collection_name}'.",
                        "code": "rag_access_denied",
                    },
                )
            policy = {"allowed_operations": ["query", "insert", "delete"], "default_action": "monitor"}
        # Per-operation gate: delete permitted iff listed; otherwise a deny/block
        # default is access_denied (delete used to ignore default_action).
        _del_allowed = policy.get("allowed_operations", [])
        if "delete" not in _del_allowed:
            METRICS["blocked"] += 1
            _del_default = str(policy.get("default_action", "monitor") or "monitor").strip().lower()
            if _del_default in ("deny", "block"):
                return JSONResponse(
                    status_code=403,
                    content={"error": "forbidden", "message": f"Access denied: policy for collection '{collection_name}' denies access.", "code": "rag_access_denied"},
                )
            return JSONResponse(
                status_code=403,
                content={"error": "forbidden", "message": "Delete operation not permitted.", "code": "rag_operation_denied"},
            )

        # Resolve the per-org BYOK client(s). Named provider -> that one;
        # unspecified -> attempt across all of the org's active providers and sum.
        if _vdb_explicit:
            client, used_vdb = _resolve_vector_client(vector_db_type, org_id)
            targets = [(used_vdb or vector_db_type, client)] if client is not None else []
        else:
            targets = iter_vector_clients_for_org(
                org_id, vector_clients=VECTOR_CLIENTS, provider_sync=VECTOR_PROVIDER_SYNC
            )
        if not targets:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "no_provider_configured",
                    "message": "No vector client is resolvable for this organization.",
                    "code": "rag_no_provider",
                },
            )

        deleted = 0
        for _ptype, _client in targets:
            try:
                deleted += await _client.delete(
                    collection_name=collection_name, ids=doc_ids, project_id=project_id
                )
            except Exception as exc:
                LOG.warning(
                    "RAG delete failed on provider=%s collection=%s: %s",
                    _ptype, collection_name, exc,
                )
        if _vdb_explicit and targets:
            vector_db_type = targets[0][0]

        elapsed_ms = (time.perf_counter() - start) * 1000
        LOG.info("RAG delete completed (project=%s, collection=%s, deleted=%d)", project_id, collection_name, deleted)

        _emit_telemetry(
            status_code=200,
            event_type="rag_delete",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=project_id,
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action="allow",
            risk_score=0.0,
            metadata={
                "collection": collection_name,
                "deleted_count": deleted,
                "ids": doc_ids,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(content={
            "status": "deleted", "collection": collection_name,
            "deleted_count": deleted, "ids": doc_ids,
            "processing_time_ms": round(elapsed_ms, 1),
        })
    finally:
        METRICS["sum_latency_ms"] += (time.perf_counter() - start) * 1000


@app.get(
    "/v1/rag/collections",
    summary="List vector DB collections for the current project",
    tags=["RAG"],
)
async def rag_list_collections(request: Request):
    """List all collections visible to the authenticated project."""
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=403, content={"error": "Authentication required."})
    project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace
    org_id = getattr(auth_ctx, "organization_id", None)

    rl_block = await _enforce_org_tpm_rate_limit(
        auth_ctx,
        event_type="rag_collections_list_blocked",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=project_id,
        estimated_tokens=20,
    )
    if rl_block is not None:
        return rl_block

    clients = iter_vector_clients_for_org(
        org_id,
        vector_clients=VECTOR_CLIENTS,
        provider_sync=VECTOR_PROVIDER_SYNC,
    )
    if not clients:
        return JSONResponse(content=no_provider_configured_payload(project_id))

    result = await list_collections_for_clients(project_id, clients)
    return JSONResponse(content={
        "project_id": project_id,
        "collections": result,
        "rag_available": True,
    })


@app.post(
    "/v1/rag/collections",
    summary="Create a new vector DB collection",
    tags=["RAG"],
)
async def rag_create_collection(request: Request):
    """Create a new namespaced collection in the specified vector DB."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid request body."})

    # FIX-4: guard against non-string collection / vector_db_type (type confusion
    # → 500). Coerce to a clean empty/default and let the validation below reject.
    _c = body.get("collection")
    collection_name = _c.strip() if isinstance(_c, str) else ""
    _vt = body.get("vector_db_type")
    vector_db_type = _vt.strip() if isinstance(_vt, str) and _vt.strip() else "pinecone"

    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                "code": "rag_namespace_violation",
            },
        )

    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=403, content={"error": "Authentication required."})
    project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace
    org_id = getattr(auth_ctx, "organization_id", None)

    rl_block = await _enforce_org_tpm_rate_limit(
        auth_ctx,
        event_type="rag_collection_create_blocked",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=project_id,
        estimated_tokens=20,
    )
    if rl_block is not None:
        return rl_block

    client, _ = resolve_vector_client_for_org(
        vector_db_type,
        org_id,
        vector_clients=VECTOR_CLIENTS,
        provider_sync=VECTOR_PROVIDER_SYNC,
        resolver=_resolve_vector_client,
    )
    if client is None:
        return JSONResponse(
            status_code=422,
            content={
                "error": "no_provider_configured",
                "code": "no_provider_configured",
                "message": f"No vector provider configured for type: {vector_db_type}",
            },
        )

    # FIX rag#6: don't falsely report "created" when the resolved provider client
    # has no create_collection method (e.g. PineconeClient) — the collection never
    # exists and an immediate DELETE 404s. Surface 501 so the caller knows the op
    # is unsupported on this provider.
    if not hasattr(client, "create_collection"):
        return JSONResponse(
            status_code=501,
            content={
                "error": "unsupported_operation",
                "message": "Provider does not support collection creation via the gateway.",
                "code": "rag_create_unsupported",
            },
        )

    try:
        namespaced = await client.create_collection(collection_name=collection_name, project_id=project_id)

        _emit_telemetry(
            status_code=200,
            event_type="rag_collection_create",
            model="",
            user_id=getattr(auth_ctx, "user_id", ""),
            project_id=project_id,
            key_prefix=getattr(auth_ctx, "prefix", ""),
            organization_id=getattr(auth_ctx, "organization_id", None),
            action="allow",
            risk_score=0.0,
            metadata={
                "collection": collection_name,
                "provider": vector_db_type,
                "module": "1.3",
                "module_id": "1.3",
            },
        )

        return JSONResponse(content={
            "status": "created", "collection": collection_name,
            "namespaced_name": namespaced, "provider": vector_db_type,
        })
    except Exception as exc:
        LOG.exception("Failed to create collection %s", collection_name)
        return JSONResponse(status_code=500, content={"error": "Failed to create collection."})


@app.delete(
    "/v1/rag/collections",
    summary="Delete a vector DB collection",
    tags=["RAG"],
)
async def rag_delete_collection(request: Request):
    """Delete a namespaced collection from the specified vector DB."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid request body."})

    # FIX-4: guard against non-string collection / vector_db_type (type confusion
    # → 500). Coerce to a clean empty/default and let the validation below reject.
    _c = body.get("collection")
    collection_name = _c.strip() if isinstance(_c, str) else ""
    _vt = body.get("vector_db_type")
    vector_db_type = _vt.strip() if isinstance(_vt, str) and _vt.strip() else "pinecone"

    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "message": "Invalid collection identifier. Use plain collection names without tenant prefixes.",
                "code": "rag_namespace_violation",
            },
        )

    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=403, content={"error": "Authentication required."})
    project_id = _org_ns_project_id(auth_ctx)  # B6: org-isolated vector namespace
    org_id = getattr(auth_ctx, "organization_id", None)

    rl_block = await _enforce_org_tpm_rate_limit(
        auth_ctx,
        event_type="rag_collection_delete_blocked",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=project_id,
        estimated_tokens=20,
    )
    if rl_block is not None:
        return rl_block

    # Check policy allows delete
    policy = VECTOR_POLICY_SYNC.get_policy(project_id, collection_name, organization_id=getattr(auth_ctx, "organization_id", None)) if VECTOR_POLICY_SYNC else None
    if policy and "delete" not in policy.get("allowed_operations", []):
        METRICS["blocked"] += 1
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "Delete not permitted by policy.", "code": "rag_operation_denied"},
        )

    client, _ = resolve_vector_client_for_org(
        vector_db_type,
        org_id,
        vector_clients=VECTOR_CLIENTS,
        provider_sync=VECTOR_PROVIDER_SYNC,
        resolver=_resolve_vector_client,
    )
    if client is None:
        return JSONResponse(
            status_code=422,
            content={
                "error": "no_provider_configured",
                "code": "no_provider_configured",
                "message": f"No vector provider configured for type: {vector_db_type}",
            },
        )

    # FIX rag#6: a provider client without delete_collection cannot delete; return
    # 501 (unsupported) rather than a misleading 404 (not found).
    if not hasattr(client, "delete_collection"):
        return JSONResponse(
            status_code=501,
            content={
                "error": "unsupported_operation",
                "message": "Provider does not support collection deletion via the gateway.",
                "code": "rag_delete_unsupported",
            },
        )

    try:
        ok = await client.delete_collection(collection_name=collection_name, project_id=project_id)
        if ok:
            _emit_telemetry(
                status_code=200,
                event_type="rag_collection_delete",
                model="",
                user_id=getattr(auth_ctx, "user_id", ""),
                project_id=project_id,
                key_prefix=getattr(auth_ctx, "prefix", ""),
                organization_id=getattr(auth_ctx, "organization_id", None),
                action="allow",
                risk_score=0.0,
                metadata={
                    "collection": collection_name,
                    "provider": vector_db_type,
                    "module": "1.3",
                    "module_id": "1.3",
                },
            )
            return JSONResponse(content={"status": "deleted", "collection": collection_name, "provider": vector_db_type})
        else:
            return JSONResponse(status_code=404, content={"error": f"Collection '{collection_name}' not found."})
    except Exception as exc:
        LOG.exception("Failed to delete collection %s", collection_name)
        return JSONResponse(status_code=500, content={"error": "Failed to delete collection."})


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/v1/admin/db-test",
    summary="Test vector database connection",
    tags=["Admin"],
)
async def admin_db_test(request: Request):
    """Test connectivity to a vector database (Pinecone, Milvus, or custom Milvus-compatible URI)."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "message": "Request body must be a JSON object."},
        )
    # Coerce each field to a string so a wrong-type value (int/list/dict) can't
    # AttributeError on ``.lower()`` / the provider clients below (-> 500).
    _prov = body.get("provider", "")
    provider = _prov.lower() if isinstance(_prov, str) else ""
    _cu = body.get("connection_url", "")
    connection_url = _cu if isinstance(_cu, str) else ""
    _ak = body.get("api_key", "")
    api_key = _ak if isinstance(_ak, str) else ""
    _env = body.get("environment", "")
    environment = _env if isinstance(_env, str) else ""

    # I4: SSRF guard — connection_url is caller-controlled and is connected to
    # below (Milvus/Chroma/custom). Validate against the SSRF allowlist so a tenant
    # can't probe internal hosts/metadata/redis via the db-test. Operators needing
    # an internal vector DB add it to MCP_ALLOW_INTERNAL_HOSTS.
    if connection_url:
        try:
            from _url_guard import is_safe_outbound_url as _safe_db_url
        except ImportError:
            from ._url_guard import is_safe_outbound_url as _safe_db_url
        _ok, _reason = _safe_db_url(connection_url)
        if not _ok:
            return JSONResponse(
                status_code=400,
                content={"error": "bad_request", "code": "connection_url_rejected", "message": f"connection_url rejected: {_reason}"},
            )

    import time as _time
    start = _time.perf_counter()

    try:
        if provider == "pinecone":
            from pinecone import Pinecone
            pc = Pinecone(api_key=api_key)
            indexes = pc.list_indexes()
            index_list = []
            if hasattr(indexes, 'indexes') and indexes.indexes:
                index_list = [idx.name if hasattr(idx, 'name') else str(idx) for idx in indexes.indexes]
            elif hasattr(indexes, '__iter__'):
                index_list = [idx.name if hasattr(idx, 'name') else str(idx) for idx in indexes]
            elapsed = round((_time.perf_counter() - start) * 1000)
            return JSONResponse(content={
                "status": "connected",
                "provider": "pinecone",
                "details": f"Indexes: {len(index_list)}",
                "indexes": index_list,
                "latency_ms": elapsed,
            })

        elif provider in {"milvus", "custom"}:
            from pymilvus import connections, utility
            from urllib.parse import urlparse
            parsed = urlparse(connection_url or "http://localhost:19530")
            connections.connect(
                alias="test_conn",
                host=parsed.hostname or "localhost",
                port=str(parsed.port or 19530),
                token=api_key or "",
            )
            colls = utility.list_collections(using="test_conn")
            connections.disconnect("test_conn")
            elapsed = round((_time.perf_counter() - start) * 1000)
            return JSONResponse(content={
                "status": "connected",
                "provider": provider,
                "details": f"Collections: {len(colls)}",
                "collections": list(colls) if colls else [],
                "latency_ms": elapsed,
            })

        else:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "error": f"Unsupported provider: {provider}"},
            )
    except Exception as exc:
        elapsed = round((_time.perf_counter() - start) * 1000)
        LOG.warning("DB connection test failed for %s: %s", provider, exc)
        return JSONResponse(
            status_code=200,
            content={
                "status": "error",
                "provider": provider,
                "error": "Connection test failed. Check provider, URL, and credentials.",
                "latency_ms": elapsed,
            },
        )


@app.post(
    "/v1/admin/bedrock-test",
    summary="Test Bedrock API connectivity",
    tags=["Admin"],
)
async def admin_bedrock_test(request: Request):
    """Test AWS Bedrock API connectivity and optionally scan a test prompt."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "message": "Request body must be a JSON object."},
        )
    check_health = body.get("check_health", False)
    prompt = body.get("prompt", "")

    import time as _time
    result = {}

    if check_health:
        try:
            from bedrock_client import BedrockClient
            import os
            client = BedrockClient()
            start = _time.perf_counter()
            available = client.is_available()
            elapsed = round((_time.perf_counter() - start) * 1000)
            result["health"] = {
                "available": available,
                "region": client.region,
                "model": client.model_id,
                "latency_ms": elapsed,
            }
            result["status"] = "ok" if available else "unavailable"
        except Exception as exc:
            LOG.warning("Bedrock health check failed: %s", exc)
            result["health"] = {"available": False, "error": str(exc)}
            result["status"] = "error"

    if prompt:
        try:
            from bedrock_scanner import BedrockScanner
            scanner = BedrockScanner()
            start = _time.perf_counter()
            scan_result = scanner.scan(prompt)
            elapsed = round((_time.perf_counter() - start) * 1000)
            result["scan_result"] = scan_result
            result["scan_latency_ms"] = elapsed
            if "status" not in result:
                result["status"] = "ok"
        except Exception as exc:
            LOG.warning("Bedrock scan test failed: %s", exc)
            result["scan_error"] = str(exc)
            if "status" not in result:
                result["status"] = "error"

        if INPUT_SCANNER is not None:
            try:
                tier1_verdict = await INPUT_SCANNER.scan_prompt(prompt)
                result["tier1_result"] = {
                    "action": tier1_verdict.action,
                    "threat_type": tier1_verdict.threat_type,
                    "confidence": tier1_verdict.confidence,
                    "detail": tier1_verdict.detail,
                    "tier": tier1_verdict.tier,
                }
                result["pipeline_note"] = (
                    "In the live gateway, Tier 1 runs first. "
                    "If Tier 1 blocks, Bedrock (Tier 2) is skipped."
                )
            except Exception as exc:
                LOG.warning("Tier 1 scan in bedrock-test failed: %s", exc)

    if not check_health and not prompt:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": "Provide 'check_health' or 'prompt'."},
        )

    return JSONResponse(content=result)


@app.get(
    "/v1/admin/circuit-breaker-state",
    summary="List circuit breaker states (all models with Redis keys)",
    tags=["Admin"],
)
async def admin_circuit_breaker_state(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not CIRCUIT_BREAKER or not REDIS_CLIENT:
        return JSONResponse(content={"models": [], "enabled": False})

    models: set[str] = set()
    cursor = 0
    while True:
        cursor, keys = await REDIS_CLIENT.scan(cursor, match="circuit:state:*", count=100)
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            models.add(key_str.replace("circuit:state:", ""))
        if cursor == 0:
            break

    import time as _time

    window = int(_time.time()) // 60
    results = []
    for model in sorted(models):
        status = await CIRCUIT_BREAKER.check(model)
        error_count = int(await REDIS_CLIENT.get(f"circuit:errors:{model}:{window}") or 0)
        total_count = int(await REDIS_CLIENT.get(f"circuit:total:{model}:{window}") or 0)
        results.append({
            "model": model,
            "state": status.state.value,
            "error_rate": round(error_count / total_count, 3) if total_count > 0 else 0.0,
            "error_count": error_count,
            "total_requests": total_count,
            "opened_at": status.opened_at,
            "should_block": status.should_block,
        })

    return JSONResponse(content={"models": results, "enabled": True})


@app.post(
    "/v1/admin/circuit-breaker-trigger",
    summary="Record simulated errors to open the circuit (dev/ops)",
    tags=["Admin"],
)
async def admin_circuit_breaker_trigger(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not CIRCUIT_BREAKER:
        return JSONResponse(status_code=503, content={"error": "Circuit breaker not initialized"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "message": "Request body must be a JSON object."},
        )
    model = body.get("model", "")
    error_count = min(int(body.get("error_count", 10)), 50)
    error_type = body.get("error_type", "simulated_error")
    for _ in range(error_count):
        await CIRCUIT_BREAKER.record_error(model, error_type)
    status = await CIRCUIT_BREAKER.check(model)
    return JSONResponse(content={
        "model": model,
        "errors_injected": error_count,
        "state": status.state.value,
        "should_block": status.should_block,
    })


@app.post(
    "/v1/admin/circuit-breaker-reset",
    summary="Clear circuit breaker Redis keys for a model",
    tags=["Admin"],
)
async def admin_circuit_breaker_reset(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    if not REDIS_CLIENT:
        return JSONResponse(status_code=503, content={"error": "Redis not available"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "message": "Request body must be a JSON object."},
        )
    model = body.get("model", "")
    cursor = 0
    deleted = 0
    while True:
        cursor, keys = await REDIS_CLIENT.scan(cursor, match=f"circuit:*:{model}*", count=100)
        if keys:
            await REDIS_CLIENT.delete(*keys)
            deleted += len(keys)
        if cursor == 0:
            break
    # Also clear the circuit-breaker-owned kill-switch mirror so a reset model is
    # unblocked immediately instead of staying disabled until the 240s TTL. The
    # helper deletes only keys with trigger_source=="circuit_breaker", so an
    # operator-set kill switch on the same model is preserved.
    mirror_cleared = False
    if model and CIRCUIT_BREAKER is not None:
        try:
            await CIRCUIT_BREAKER._clear_kill_switch_for_model(model)
            mirror_cleared = True
        except Exception:
            LOG.warning("CB reset: kill-switch mirror clear failed for model=%s", model)
    return JSONResponse(content={
        "model": model,
        "keys_deleted": deleted,
        "kill_switch_mirror_cleared": mirror_cleared,
        "state": "closed",
    })


# --- Phase 1 F-3.1: admin proxy for RAG collection management ------------
# Per-tenant `/v1/rag/collections` requires an org Bearer key (per-org RBAC).
# The control plane cannot retrieve the raw key (only SHA-256 hash is
# stored). These admin variants take ``project_id`` explicitly, are gated
# by ``_require_admin_role`` (which accepts the GATEWAY_INTERNAL_API_KEY),
# and reuse the same vector-client logic. Downstream RBAC therefore lives
# in the Django proxy layer (IsAdminOrSuperuser).
def _strip_namespace(name: str, prefix: str) -> str:
    if isinstance(name, str) and name.startswith(prefix):
        return name[len(prefix):]
    return name


def _resolve_admin_rag_scope(request, client_org, client_project):
    """Cross-org IDOR guard for the admin RAG endpoints (FIX-1).

    These endpoints historically trusted a CLIENT-supplied ``organization_id`` /
    ``project_id``. When the caller authenticated with an org-bound Bearer key
    (``request.state.auth_context`` carries an ``organization_id``), the request
    MUST stay within that bound org/project — a client-supplied scope that
    differs is a cross-tenant access attempt and is rejected with a GENERIC 403.

    When the caller is the trusted control-plane proxy (server-to-server via
    ``GATEWAY_INTERNAL_API_KEY``; no org-bound ``auth_context``), the supplied
    scope is honored as before (Django RBAC already constrained it upstream).

    Returns ``(error_response_or_None, effective_org_id, effective_project_id)``.
    The effective values are the BOUND org/project when an org-bound key is
    present, else the client-supplied values.
    """
    auth_ctx = getattr(request.state, "auth_context", None)
    bound_org = getattr(auth_ctx, "organization_id", None) if auth_ctx is not None else None
    # No org-bound auth context → trusted internal proxy path; preserve behavior.
    if bound_org is None:
        return None, coerce_org_id(client_org), (str(client_project).strip() if client_project else "")

    bound_org_norm = coerce_org_id(bound_org)
    bound_project = str(getattr(auth_ctx, "project_id", "") or "").strip()

    # Any client-supplied org that differs from the bound org is a scope violation.
    client_org_norm = coerce_org_id(client_org)
    if client_org_norm is not None and client_org_norm != bound_org_norm:
        return (
            JSONResponse(
                status_code=403,
                content={"error": "forbidden", "code": "org_scope_violation"},
            ),
            None,
            "",
        )

    # Constrain project_id to the bound org's project too.
    client_project_norm = str(client_project).strip() if client_project else ""
    if client_project_norm and bound_project and client_project_norm != bound_project:
        return (
            JSONResponse(
                status_code=403,
                content={"error": "forbidden", "code": "org_scope_violation"},
            ),
            None,
            "",
        )

    effective_project = bound_project or client_project_norm
    return None, bound_org_norm, effective_project


@app.get(
    "/v1/admin/rag/collections",
    summary="List vector DB collections for a given project (admin)",
    tags=["Admin"],
)
async def admin_rag_list_collections(
    request: Request,
    project_id: str = "",
    organization_id: str = "",
):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block

    # FIX-1: derive the org/project from the bound key when present; reject any
    # client-supplied scope that differs (generic 403). Trusted internal proxy
    # (no org-bound auth_context) keeps the supplied scope.
    scope_block, org_id, scoped_project = _resolve_admin_rag_scope(
        request, organization_id, project_id
    )
    if scope_block is not None:
        return scope_block
    # B6: admin RAG ops MUST use the same org-prefixed namespace as tenant ops
    # (else an operator can't read tenant data). org_id is the TARGET org from
    # _resolve_admin_rag_scope, not the operator's own org.
    project_id = (
        f"org{org_id}-{(scoped_project or '').strip() or 'default'}"
        if org_id is not None
        else (scoped_project or "").strip()
    )
    if not project_id:
        return JSONResponse(status_code=400, content={"error": "Missing 'project_id' query parameter."})

    clients = iter_vector_clients_for_org(
        org_id,
        vector_clients=VECTOR_CLIENTS,
        provider_sync=VECTOR_PROVIDER_SYNC,
    )
    if not clients:
        return JSONResponse(content=no_provider_configured_payload(project_id))

    result = await list_collections_for_clients(project_id, clients)
    return JSONResponse(content={
        "project_id": project_id,
        "collections": result,
        "rag_available": True,
    })


@app.post(
    "/v1/admin/rag/collections",
    summary="Create a vector DB collection for a given project (admin)",
    tags=["Admin"],
)
async def admin_rag_create_collection(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid request body."})
    _c = body.get("collection")
    collection_name = _c.strip() if isinstance(_c, str) else ""
    _vt = body.get("vector_db_type")
    vector_db_type = _vt.strip() if isinstance(_vt, str) and _vt.strip() else "pinecone"
    # FIX-1: ignore client-supplied org/project; derive from bound key. Trusted
    # internal proxy (no org-bound auth_context) keeps the supplied scope.
    scope_block, org_id, scoped_project = _resolve_admin_rag_scope(
        request, body.get("organization_id"), body.get("project_id")
    )
    if scope_block is not None:
        return scope_block
    # B6: admin RAG ops MUST use the same org-prefixed namespace as tenant ops
    # (else an operator can't read tenant data). org_id is the TARGET org from
    # _resolve_admin_rag_scope, not the operator's own org.
    project_id = (
        f"org{org_id}-{(scoped_project or '').strip() or 'default'}"
        if org_id is not None
        else (scoped_project or "").strip()
    )
    if not project_id:
        return JSONResponse(status_code=400, content={"error": "Missing 'project_id'."})
    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(status_code=403, content={
            "error": "forbidden",
            "message": "Invalid collection identifier. Use plain names without tenant prefixes.",
            "code": "rag_namespace_violation",
        })

    client, _ = resolve_vector_client_for_org(
        vector_db_type,
        org_id,
        vector_clients=VECTOR_CLIENTS,
        provider_sync=VECTOR_PROVIDER_SYNC,
        resolver=_resolve_vector_client,
    )
    if client is None:
        return JSONResponse(
            status_code=422,
            content={
                "error": "no_provider_configured",
                "code": "no_provider_configured",
                "message": f"No vector provider configured for type: {vector_db_type}",
            },
        )
    try:
        if hasattr(client, "create_collection"):
            namespaced = await client.create_collection(collection_name=collection_name, project_id=project_id)
        else:
            namespaced = f"{project_id}__{collection_name}"
        return JSONResponse(content={
            "status": "created", "collection": collection_name,
            "namespaced_name": namespaced, "provider": vector_db_type,
            "project_id": project_id,
        })
    except Exception as exc:
        LOG.exception("admin_rag_create_collection failed: %s", collection_name)
        return JSONResponse(status_code=500, content={"error": "Failed to create collection."})


@app.delete(
    "/v1/admin/rag/collections",
    summary="Delete a vector DB collection for a given project (admin)",
    tags=["Admin"],
)
async def admin_rag_delete_collection(request: Request):
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "Invalid request body."})
    _c = body.get("collection")
    collection_name = _c.strip() if isinstance(_c, str) else ""
    _vt = body.get("vector_db_type")
    vector_db_type = _vt.strip() if isinstance(_vt, str) and _vt.strip() else "pinecone"
    # FIX-1: ignore client-supplied org/project; derive from bound key. Trusted
    # internal proxy (no org-bound auth_context) keeps the supplied scope.
    scope_block, org_id, scoped_project = _resolve_admin_rag_scope(
        request, body.get("organization_id"), body.get("project_id")
    )
    if scope_block is not None:
        return scope_block
    # B6: admin RAG ops MUST use the same org-prefixed namespace as tenant ops
    # (else an operator can't read tenant data). org_id is the TARGET org from
    # _resolve_admin_rag_scope, not the operator's own org.
    project_id = (
        f"org{org_id}-{(scoped_project or '').strip() or 'default'}"
        if org_id is not None
        else (scoped_project or "").strip()
    )
    if not project_id:
        return JSONResponse(status_code=400, content={"error": "Missing 'project_id'."})
    if not collection_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'collection'."})
    if not _is_valid_collection_name(collection_name):
        return JSONResponse(status_code=403, content={
            "error": "forbidden",
            "message": "Invalid collection identifier. Use plain names without tenant prefixes.",
            "code": "rag_namespace_violation",
        })

    client, _ = resolve_vector_client_for_org(
        vector_db_type,
        org_id,
        vector_clients=VECTOR_CLIENTS,
        provider_sync=VECTOR_PROVIDER_SYNC,
        resolver=_resolve_vector_client,
    )
    if client is None:
        return JSONResponse(
            status_code=422,
            content={
                "error": "no_provider_configured",
                "code": "no_provider_configured",
                "message": f"No vector provider configured for type: {vector_db_type}",
            },
        )
    try:
        if hasattr(client, "delete_collection"):
            ok = await client.delete_collection(collection_name=collection_name, project_id=project_id)
        else:
            ok = False
        return JSONResponse(content={
            "status": "deleted" if ok else "not_found",
            "collection": collection_name,
            "provider": vector_db_type,
            "project_id": project_id,
        })
    except Exception as exc:
        LOG.exception("admin_rag_delete_collection failed: %s", collection_name)
        return JSONResponse(status_code=500, content={"error": "Failed to delete collection."})


@app.get(
    "/v1/admin/logs",
    summary="Stream gateway logs via SSE",
    tags=["Admin"],
)
async def admin_logs(request: Request, service: str = "all", level: str = "DEBUG"):
    """Stream real-time gateway logs as Server-Sent Events."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        from log_buffer import LOG_BUFFER
    except ImportError:
        from .log_buffer import LOG_BUFFER

    async def event_generator():
        try:
            async for event in LOG_BUFFER.subscribe(
                service_filter=service,
                level_filter=level,
            ):
                yield event
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Bedrock Dedicated Log Endpoints ──────────────────────────────────────────

@app.get(
    "/v1/admin/bedrock-logs",
    summary="Recent Bedrock logs (JSON)",
    description=(
        "Returns the most recent Bedrock log entries from the in-memory ring buffer. "
        "Use `limit` to control how many entries (max 500), and `level` to filter "
        "by minimum severity (DEBUG, INFO, WARNING, ERROR)."
    ),
    tags=["Admin", "Bedrock"],
)
async def admin_bedrock_logs(request: Request, limit: int = 100, level: str = "DEBUG"):
    """Return recent Bedrock logs from the in-memory ring buffer."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        from bedrock_logger import BEDROCK_LOG_RING
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={"error": "bedrock_logger not available"},
        )
    entries = BEDROCK_LOG_RING.recent(limit=min(limit, 500), level_filter=level)
    return JSONResponse(content={
        "count": len(entries),
        "total_logged": BEDROCK_LOG_RING.counter,
        "level_filter": level,
        "entries": entries,
    })


@app.get(
    "/v1/admin/bedrock-logs/stream",
    summary="Stream Bedrock logs via SSE (live)",
    description=(
        "Server-Sent Events stream of Bedrock-only logs. "
        "Connect with EventSource or curl to watch Bedrock operations in real-time. "
        "Use `level` query param to set minimum severity (DEBUG, INFO, WARNING, ERROR)."
    ),
    tags=["Admin", "Bedrock"],
)
async def admin_bedrock_logs_stream(request: Request, level: str = "DEBUG"):
    """Stream real-time Bedrock logs as Server-Sent Events."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    try:
        from bedrock_logger import BEDROCK_LOG_RING
    except ImportError:
        return JSONResponse(
            status_code=501,
            content={"error": "bedrock_logger not available"},
        )

    import json as _json

    level_priority = {
        "DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4,
    }
    min_level = level_priority.get(level.upper(), 0)

    async def bedrock_event_generator():
        last_seen = BEDROCK_LOG_RING.counter
        yield ": connected to bedrock log stream\n\n"
        heartbeat_counter = 0

        while True:
            current = BEDROCK_LOG_RING.counter
            if current > last_seen:
                new_count = current - last_seen
                items = list(BEDROCK_LOG_RING._buf)
                new_items = items[-new_count:] if new_count <= len(items) else items

                for item in new_items:
                    item_level = level_priority.get(item.level.upper(), 0)
                    if item_level < min_level:
                        continue
                    yield f"data: {_json.dumps(item.to_dict(), default=str)}\n\n"

                last_seen = current
                heartbeat_counter = 0
            else:
                heartbeat_counter += 1
                if heartbeat_counter >= 30:  # 15s heartbeat
                    yield ": heartbeat\n\n"
                    heartbeat_counter = 0

            await asyncio.sleep(0.5)

    return StreamingResponse(
        bedrock_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get(
    "/v1/admin/bedrock-logs/file",
    summary="Download Bedrock log file",
    description="Returns the current bedrock.log file content for download or inspection.",
    tags=["Admin", "Bedrock"],
)
async def admin_bedrock_logs_file(request: Request, tail: int = 200):
    """Return the last N lines of the bedrock.log file."""
    admin_block = _require_admin_role(request)
    if admin_block is not None:
        return admin_block
    import os as _os
    log_dir = _os.getenv("BEDROCK_LOG_DIR", "/var/log/bedrock")
    log_path = _os.path.join(log_dir, "bedrock.log")

    if not _os.path.exists(log_path):
        return JSONResponse(
            status_code=404,
            content={"error": "bedrock.log not found", "path": log_path},
        )

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail_lines = lines[-tail:] if len(lines) > tail else lines
        return JSONResponse(content={
            "file": log_path,
            "total_lines": len(lines),
            "returned_lines": len(tail_lines),
            "lines": [line.rstrip("\n") for line in tail_lines],
        })
    except OSError as exc:
        LOG.warning("admin_logs: failed to read log file: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Failed to read the requested log file."},
        )


# ─────────────────────────────────────────────────────────────────────
# Lightweight policy-check endpoint — Phase 1 hot-path benchmark surface.
#
def _ambiguous_policy_block_response(*, org_slug: str) -> JSONResponse:
    """Fail-closed when policy engine returns block without matched rules."""
    LOG.error(
        "Policy check returned block without matched rules (org=%s); fail-closed",
        org_slug,
    )
    METRICS["blocked"] += 1
    return JSONResponse(
        status_code=503,
        content={
            "error": "service_unavailable",
            "message": (
                "Policy engine returned an ambiguous block. "
                "Request denied (fail-closed)."
            ),
            "code": "policy_ambiguous_block",
        },
    )


def _should_block_tier1_verdict(verdict, org_config: dict) -> bool:
    """Return True when a tier-1 scan verdict should block the request."""
    injection_threshold = org_config.get("prompt_injection_threshold", 0.80)
    _INJECTION_THREAT_TYPES = {"prompt_injection", "jailbreak", "goal_hijacking"}
    _is_injection = verdict.threat_type in _INJECTION_THREAT_TYPES
    _should_block = (
        verdict.action == "block"
        and verdict.threat_type not in ("pii", "secret")
    )
    if _is_injection:
        _should_block = (
            _should_block
            and org_config.get("scan_block_on_injection", True)
            and verdict.confidence >= injection_threshold
        )
    return _should_block


async def _policy_check_tier1_scan_block(
    *,
    prompt: str,
    org_config: dict,
    request: Request,
    auth_ctx,
    model: str = "",
) -> JSONResponse | None:
    """Run tier-1 input scan for /v1/policy/check; return 403 JSON if blocked."""
    if INPUT_SCANNER is None or not org_config.get("input_scan_enabled", True) or not prompt:
        return None
    try:
        verdict = await INPUT_SCANNER.scan_prompt(prompt)
    except Exception as exc:
        LOG.warning("Tier-1 scan failed on /v1/policy/check: %s", exc)
        return None
    if not _should_block_tier1_verdict(verdict, org_config):
        return None
    _emit_telemetry(
        status_code=403,
        event_type="input_blocked",
        model=model or "",
        user_id=getattr(auth_ctx, "user_id", None),
        project_id=getattr(auth_ctx, "project_id", "") or "",
        key_prefix=getattr(auth_ctx, "key_prefix", "") or "",
        # M4: stamp org scope explicitly. This path emits BEFORE _REQUEST_ORG_ID
        # is set for the request, so without an explicit organization_id the
        # event carries a null org and the worker drops it ("Skipping unscoped
        # telemetry event") — ~99 input_blocked events were lost this way.
        organization_id=getattr(auth_ctx, "organization_id", None),
        action="block",
        risk_score=verdict.confidence,
        threat_type=verdict.threat_type,
        compliance_tags=org_config.get("compliance_frameworks", []),
        pipeline_stage="query",
        metadata={
            "detail": verdict.detail,
            "confidence": verdict.confidence,
            "matched_patterns": verdict.matched_patterns,
            "endpoint": "/v1/policy/check",
        },
    )
    METRICS["blocked"] += 1
    tier_label = {
        "tier_1": "Tier 1 regex",
        "tier_1_5": "Tier 1.5 fuzzy",
        "tier_1_6": "Tier 1.6 semantic",
        "tier_2": "Tier 2 ML",
    }.get(verdict.tier, verdict.tier or "tier_1")
    return _build_block_response(
        403,
        f"{verdict.tier or 'tier_1'}_{verdict.threat_type}",
        _build_zeroshield_metadata(
            action="block",
            reason=f"Input blocked by {tier_label} scanner.",
            detection_tier=verdict.tier or "tier_1",
            threat_type=verdict.threat_type,
            confidence=verdict.confidence,
            matched_patterns=verdict.matched_patterns,
            original_prompt=prompt,
            detail=verdict.detail,
        ),
    )


# Exercises the full hot path (auth → per-key TPM → org TPM → tier-1 scan →
# cached policy evaluation) WITHOUT forwarding to an LLM. This isolates the
# firewall overhead for p99 measurement under load (target: < 50 ms p99).
# ─────────────────────────────────────────────────────────────────────
@app.post(
    "/v1/policy/check",
    summary="Evaluate a prompt against compiled policies (no LLM call)",
    tags=["Policy"],
)
async def policy_check_endpoint(request: Request):
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Missing or invalid API key."},
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    # Scan the FULL prompt/response — do NOT truncate to the first 8000 chars.
    # Truncating let an attacker pad 8000 bytes of filler and hide an injection /
    # PII payload after the cut, which then passed the pre-LLM policy check as
    # action=allow. str() coercion also turns a wrong-typed field into a clean
    # value instead of a 500 (a non-str sliced with [:N] raised TypeError).
    prompt = str(body.get("prompt") or "")
    if not prompt and isinstance(body, dict) and body.get("messages"):
        prompt = str(_extract_prompt_from_messages(body.get("messages")) or "")
    response_text = str(body.get("response") or "")

    # Per-key TPM
    rate_limit_tpm = getattr(auth_ctx, "rate_limit_tpm", 0) or 0
    policy_est_tokens = _estimate_request_tokens(
        prompt or "",
        int(body.get("max_tokens") or 0) if isinstance(body, dict) else 0,
    )
    if RATE_LIMITER is not None and rate_limit_tpm:
        allowed, current = await RATE_LIMITER.check_rate_limit(
            auth_ctx.key_hash, rate_limit_tpm, policy_est_tokens,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "scope": "key",
                    "current": current,
                    "limit": rate_limit_tpm,
                },
                headers={"Retry-After": "60"},
            )

    # Per-org TPM (new in Phase 1 hardening)
    org_tpm = 0
    if CONFIG_SYNC is not None and auth_ctx.org_slug:
        try:
            org_cfg = CONFIG_SYNC.get_config(auth_ctx.org_slug) or {}
            org_tpm = int(org_cfg.get("org_tpm_limit", 0) or 0)
        except Exception:
            org_tpm = 0
    if RATE_LIMITER is not None and org_tpm:
        policy_est_tokens = _estimate_request_tokens(
            prompt or "",
            int(body.get("max_tokens") or 0) if isinstance(body, dict) else 0,
        )
        org_allowed, org_current = await RATE_LIMITER.check_org_rate_limit(
            auth_ctx.org_slug, org_tpm, policy_est_tokens,
        )
        if not org_allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "scope": "org",
                    "current": org_current,
                    "limit": org_tpm,
                },
                headers={"Retry-After": "60"},
            )

    org_cfg: dict = {}
    if CONFIG_SYNC is not None and auth_ctx.org_slug:
        try:
            org_cfg = CONFIG_SYNC.get_config(auth_ctx.org_slug) or {}
        except Exception:
            org_cfg = {}

    tier1_block = await _policy_check_tier1_scan_block(
        prompt=prompt,
        org_config=org_cfg,
        request=request,
        auth_ctx=auth_ctx,
        model=body.get("model") if isinstance(body, dict) else "",
    )
    if tier1_block is not None:
        return tier1_block

    import time as _t
    _t0 = _t.perf_counter()
    status, result = _policy_check_cached(
        prompt=prompt,
        response_text=response_text,
        user_id=getattr(auth_ctx, "user_id", None),
        endpoint_id=None,
        project_id=None,
        risk_score=None,
        model=body.get("model"),
        org_slug=auth_ctx.org_slug or "default",
        actor=_build_policy_actor(auth_ctx),
    )
    # Emit telemetry for /v1/policy/check evaluations (Phase 1 hot-path observability).
    try:
        _latency_ms = (_t.perf_counter() - _t0) * 1000.0
        _action = "block" if status in (403, 451) else "allow"
        _decision = (result or {}).get("decision") if isinstance(result, dict) else None
        _REQUEST_ORG_ID.set(getattr(auth_ctx, "organization_id", None))
        _REQUEST_ORG_SLUG.set(getattr(auth_ctx, "org_slug", "") or "")
        _REQUEST_SOURCE_IP.set(request.client.host if request.client else "")
        _REQUEST_METHOD.set("POST")
        _emit_telemetry(
            status_code=status,
            event_type="policy_check",
            model=body.get("model") or "",
            user_id=getattr(auth_ctx, "user_id", None),
            project_id=getattr(auth_ctx, "project_id", "") or "",
            key_prefix=getattr(auth_ctx, "key_prefix", "") or "",
            latency_ms=_latency_ms,
            action=_action,
            pipeline_stage="query",
            metadata={"decision": _decision, "endpoint": "/v1/policy/check"},
        )
    except Exception:
        pass
    return JSONResponse(status_code=status, content=result)


@app.get(
    "/health",
    summary="Health check",
    description="Liveness probe. Returns the gateway status and registered agent ID.",
    tags=["Health"],
    responses={
        200: {
            "description": "Gateway is healthy",
            "content": {
                "application/json": {
                    "example": {
                        "status": "ok",
                        "agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    }
                }
            },
        },
    },
)
async def health():
    # Phase 0 D-G1-v3: signing-key misconfig check MUST run before any
    # CONFIG access — health is also called from probes during startup
    # before _bootstrap() initialises CONFIG, and we want a deterministic
    # 503 with structured reason rather than a 500 from None.get().
    if signing_enforced() and _get_signing_key() is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "reason": "policy_signing_key_missing",
                "agent_id": AGENT_ID,
            },
        )
    policy_info = {}
    if POLICY_SYNC is not None:
        policy_info = {
            "policy_cache_loaded": POLICY_SYNC.is_loaded,
            "policy_cache_version": POLICY_SYNC.version,
            "policy_count": POLICY_SYNC.policy_count,
        }
    config_info = {}
    if CONFIG_SYNC is not None and CONFIG is not None:
        config_info = {
            "config_sync_loaded": CONFIG_SYNC.is_loaded,
            "firewall_enabled": CONFIG.get("firewall_enabled", True),
            "enforcement_mode": CONFIG.get("enforcement_mode", "block"),
        }
    vector_info = {}
    if VECTOR_POLICY_SYNC is not None:
        vector_info = {
            "vector_policy_cache_loaded": VECTOR_POLICY_SYNC.is_loaded,
            "vector_policy_cache_version": VECTOR_POLICY_SYNC.version,
            "vector_policy_count": VECTOR_POLICY_SYNC.policy_count,
            "vector_clients": list(VECTOR_CLIENTS.keys()),
        }
    runtime_metrics = {}
    if REDIS_CLIENT is not None:
        try:
            runtime_metrics["telemetry_queue_depth"] = int(await REDIS_CLIENT.llen("telemetry:events"))
        except Exception:
            runtime_metrics["telemetry_queue_depth"] = -1
    cache_required = CONFIG.get("policy_cache_require_loaded", True) if CONFIG is not None else True
    cache_loaded = bool(policy_info.get("policy_cache_loaded", False)) if policy_info else False
    if cache_required and not cache_loaded:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "reason": "policy_cache_unavailable",
                "agent_id": AGENT_ID,
                **policy_info,
                **config_info,
                **vector_info,
                **runtime_metrics,
            },
        )

    return {
        "status": "ok",
        "agent_id": AGENT_ID,
        **policy_info,
        **config_info,
        **vector_info,
        **runtime_metrics,
    }


@app.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "Prometheus text-format metrics for the AI Mesh Firewall gateway. "
        "Exposes counters/gauges/histograms only — no PII in label values. "
        "Should be bound to an internal/scraper-only network in production."
    ),
    tags=["Health"],
    include_in_schema=False,
)
async def prometheus_metrics(request: Request):
    try:
        from .metrics_auth import verify_metrics_scraper
    except ImportError:
        from metrics_auth import verify_metrics_scraper  # type: ignore[no-redef]
    if not verify_metrics_scraper(dict(request.headers)):
        from starlette.responses import JSONResponse

        return JSONResponse(
            status_code=401,
            content={
                "error": "unauthorized",
                "message": "Valid X-Metrics-Scraper-Key or Authorization: Bearer required.",
                "code": "metrics_auth_required",
            },
        )
    try:
        from .metrics import render_latest
    except ImportError:
        from metrics import render_latest  # type: ignore[no-redef]
    body, content_type = render_latest()
    from starlette.responses import Response
    return Response(content=body, media_type=content_type)


@app.get(
    "/v1/models",
    summary="List available models",
    description="Returns the list of LLM models configured in the gateway (OpenAI-compatible format).",
    tags=["Models"],
    responses={
        200: {
            "description": "List of available models",
            "content": {
                "application/json": {
                    "example": {
                        "object": "list",
                        "data": [
                            {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
                        ],
                    }
                }
            },
        },
    },
)
async def list_models(request: Request):
    # Auth-gate consistently with the inference endpoints: a missing / invalid /
    # expired / revoked key gets 401 — not a 200 empty list (which was an
    # inconsistent auth contract that could mask key revocation). /v1/models is
    # SOFT_AUTH, so the middleware sets auth_context only for a valid key and
    # otherwise leaves it None; reject None here.
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(
            status_code=401,
            content={
                "error": "unauthorized",
                "message": "A valid API key is required to list models.",
                "code": "unauthorized",
            },
        )
    return JSONResponse(content={"object": "list", "data": _resolve_models_for_request(request)})


def _resolve_models_for_request(request: Request) -> list[dict]:
    """OpenAI-format model list for the request's org. Shared by GET /v1/models and
    GET /v1/models/{model} so a single-model lookup never diverges from the list.
    Caller is responsible for auth-gating (both endpoints reject a missing auth_ctx)."""
    if LLM_ROUTER is None:
        return []
    auth_ctx = getattr(request.state, "auth_context", None)
    models = LLM_ROUTER.get_model_list()
    org_slug = auth_ctx.org_slug if auth_ctx else ""
    if org_slug and CONFIG_SYNC:
        org_routing = CONFIG_SYNC.get_model_routing(org_slug)
        eligible = _filter_inference_eligible_models(org_routing) if org_routing else []
        # Enumerate the org's OWN inference-eligible models by CLIENT-FACING name
        # (model_name) — the exact string a caller passes as `model`.
        _ordered: list[str] = []
        _seen_names: set[str] = set()
        for _m in eligible:
            _nm = str(_m.get("model_name") or _m.get("model_id") or "").strip()
            if _nm and _nm not in _seen_names and not _is_routing_sentinel_model(_nm):
                _seen_names.add(_nm)
                _ordered.append(_nm)
        models = [
            {"id": _n, "object": "model", "created": 1704067200, "owned_by": org_slug}
            for _n in _ordered
        ]
    # R8: dedup by id (a model can appear under multiple routing identities/aliases).
    _seen: set = set()
    _deduped = []
    for _m in models:
        _mid = _m.get("id")
        if _mid in _seen:
            continue
        _seen.add(_mid)
        _deduped.append(_m)
    return _deduped


@app.get("/v1/models/{model_id:path}", summary="Retrieve a model", tags=["Models"])
async def retrieve_model(model_id: str, request: Request):
    """D4-P0: ``client.models.retrieve(id)`` — many tools probe a model before use."""
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=401, content={
            "error": "unauthorized", "message": "A valid API key is required.", "code": "unauthorized"})
    for _m in _resolve_models_for_request(request):
        if _m.get("id") == model_id:
            return JSONResponse(content=_m)
    return JSONResponse(status_code=404, content={
        "error": "model_not_found",
        "message": f"The model '{model_id}' does not exist or you do not have access to it.",
        "code": "model_not_found"})


@app.post("/v1/moderations", summary="Classify text against ZeroShield safety policies", tags=["Moderations"])
async def create_moderations(request: Request):
    """D4-P1: ``client.moderations.create()`` — expose the firewall's input detectors as a
    first-class OpenAI surface. Returns the exact OpenAI moderation schema; the standard
    OpenAI categories this firewall does not classify are reported ``False``, while the
    ZeroShield signals (prompt_injection / jailbreak / pii / credential) carry the verdict."""
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=401, content={
            "error": "unauthorized", "message": "A valid API key is required.", "code": "unauthorized"})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "error": "invalid_request", "message": "Invalid JSON body.", "code": "invalid_request"})
    _inp = body.get("input") if isinstance(body, dict) else None
    if _inp is None:
        return JSONResponse(status_code=400, content={
            "error": "invalid_request", "message": "Missing required parameter: 'input'.",
            "code": "missing_required_parameter"})
    texts = [_inp] if isinstance(_inp, str) else (_inp if isinstance(_inp, list) else [str(_inp)])
    # G63: bound the batch — every item is tier-1 scanned (~ms each), so an unbounded
    # array is a CPU resource-exhaustion DoS. Cap the item count + total chars up front
    # (413), mirroring the /v1/embeddings ceilings. Per-item length is already bounded
    # by the scanner's MAX_PROMPT_LENGTH.
    if len(texts) > MAX_MODERATION_BATCH:
        return JSONResponse(status_code=413, content={
            "error": "payload_too_large",
            "message": f"'input' array exceeds the maximum of {MAX_MODERATION_BATCH} items.",
            "param": "input", "code": "moderation_input_too_large"})
    _mod_total_chars = sum(len(_t) if isinstance(_t, str) else len(str(_t)) for _t in texts)
    if _mod_total_chars > MAX_MODERATION_INPUT_CHARS:
        return JSONResponse(status_code=413, content={
            "error": "payload_too_large",
            "message": "'input' total size exceeds the maximum.",
            "param": "input", "code": "moderation_input_too_large"})
    results = []
    for _t in texts:
        s = _t if isinstance(_t, str) else str(_t)
        threat, conf, action = "", 0.0, "allow"
        if INPUT_SCANNER is not None and s.strip():
            try:
                v = await INPUT_SCANNER.scan_prompt(s)
                threat = str(getattr(v, "threat_type", "") or "")
                conf = float(getattr(v, "confidence", 0.0) or 0.0)
                action = str(getattr(v, "action", "allow") or "allow")
            except Exception:
                pass
        zs = {
            "prompt_injection": threat in ("prompt_injection", "indirect_injection", "injection"),
            "jailbreak": threat == "jailbreak",
            "pii": threat == "pii",
            "credential": threat in ("secret", "credential"),
        }
        categories = {"hate": False, "hate/threatening": False, "harassment": False,
                      "self-harm": False, "sexual": False, "violence": False, **zs}
        flagged = action in ("block", "flag", "redact") or any(zs.values())
        scores = {k: (round(conf, 4) if v else 0.0) for k, v in categories.items()}
        results.append({"flagged": bool(flagged), "categories": categories, "category_scores": scores})
    # SEAM-C: mint the moderation id ONCE and expose it on both the body ``id`` and the
    # x-request-id RESPONSE header (via request.state.gw_request_id, read by the compat
    # shim) so the SDK's response._request_id can be joined to the moderation result id.
    _mod_id = f"modr-{_uuid.uuid4().hex[:24]}"
    request.state.gw_request_id = _mod_id
    return JSONResponse(content={
        "id": _mod_id,
        "model": (body.get("model") if isinstance(body, dict) else None) or "zeroshield-moderation",
        "results": results})


@app.post("/v1/completions", summary="Legacy text completions (OpenAI-compatible)", tags=["Completions"])
async def create_legacy_completion(
    request: Request,
    x_user_id: str | None = Header(None),
    x_endpoint_id: str | None = Header(None),
    x_agent_data: str | None = Header(None),
):
    """C4 (D4): ``client.completions.create(prompt=...)``. The legacy text-completion surface
    is a FORMAT ADAPTER over ``proxy_chat`` — exactly like /v1/responses — so the prompt is
    routed prompt->messages through the UNCHANGED firewall chain (auth, kill-switch, Tier-1/2
    scan, output guard, redaction, telemetry) and the chat result is translated back into a
    ``text_completion`` object. The firewall is inherited, never forked."""
    auth_ctx = getattr(request.state, "auth_context", None)
    if auth_ctx is None:
        return JSONResponse(status_code=401, content=_build_oai_error(
            401, "A valid API key is required.", error_type="authentication_error"))
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_build_oai_error(
            400, "Invalid JSON body.", code="invalid_request"))
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=_build_oai_error(
            400, "Request body must be a JSON object.", code="invalid_request"))
    model = body.get("model")
    if not model or not isinstance(model, str):
        return JSONResponse(status_code=400, content=_build_oai_error(
            400, "Missing required parameter: 'model'.", code="missing_required_parameter", param="model"))
    if body.get("stream"):
        # Streaming legacy text_completion chunks are not yet implemented; fail fast with a
        # clean, SDK-parseable error rather than a silent hang. (Chat + Responses stream.)
        return JSONResponse(status_code=400, content=_build_oai_error(
            400, "Streaming is not supported on /v1/completions; use /v1/chat/completions.",
            code="invalid_request", param="stream"))
    prompt = body.get("prompt")
    if prompt is None:
        return JSONResponse(status_code=400, content=_build_oai_error(
            400, "Missing required parameter: 'prompt'.", code="missing_required_parameter", param="prompt"))
    # OpenAI accepts str | list[str] | token-id arrays. We can only firewall-scan TEXT, so a
    # str -> one prompt, a list[str] -> a batch (one indexed choice each). Token-id arrays are
    # rejected (cannot be scanned) rather than silently forwarded raw.
    if isinstance(prompt, str):
        prompts = [prompt]
    elif isinstance(prompt, list) and prompt and all(isinstance(p, str) for p in prompt):
        prompts = prompt
    else:
        return JSONResponse(status_code=400, content=_build_oai_error(
            400, "'prompt' must be a string or a non-empty list of strings.", code="invalid_request", param="prompt"))
    # G64: bound the batch — each prompt is a SEPARATE upstream LLM call, so an unbounded
    # array is a cost/DoS amplification. Reject an over-limit count or total-char array with
    # 413 BEFORE any dispatch (per-prompt length is already bounded by the chat pipeline).
    if len(prompts) > MAX_COMPLETION_PROMPTS:
        return JSONResponse(status_code=413, content=_build_oai_error(
            413, f"'prompt' array exceeds the maximum of {MAX_COMPLETION_PROMPTS} items.",
            code="completion_input_too_large", param="prompt"))
    if sum(len(p) for p in prompts) > MAX_COMPLETION_INPUT_CHARS:
        return JSONResponse(status_code=413, content=_build_oai_error(
            413, "'prompt' total size exceeds the maximum.",
            code="completion_input_too_large", param="prompt"))

    # Sampling params that are valid on BOTH surfaces flow through to chat unchanged.
    _passthrough = {k: body[k] for k in (
        "max_tokens", "temperature", "top_p", "stop", "seed", "user",
        "presence_penalty", "frequency_penalty", "logit_bias",
    ) if k in body}

    cmpl_id = f"cmpl-{_uuid.uuid4().hex[:24]}"
    request.state.gw_request_id = cmpl_id
    created = int(time.time())
    choices: list[dict] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    resolved_model = model
    zeroshield = None

    for idx, text_prompt in enumerate(prompts):
        chat_body = {"model": model, "messages": [{"role": "user", "content": text_prompt}], **_passthrough}
        chat_response = await _dispatch_chat_internally(
            request, chat_body, x_user_id, x_endpoint_id, x_agent_data)
        status = getattr(chat_response, "status_code", 500)
        try:
            chat_json = json.loads(bytes(getattr(chat_response, "body", b"") or b"{}"))
        except (TypeError, ValueError):
            chat_json = {}
        if status >= 400:
            # Any sub-prompt block/error aborts the whole completion (the firewall fired) —
            # surface the inner OpenAI error envelope verbatim so e.code/e.type/request_id hold.
            return JSONResponse(status_code=status, content=_coerce_chat_error(status, chat_json))
        _choice = (chat_json.get("choices") or [{}])[0]
        _msg = _choice.get("message") or {}
        choices.append({
            "text": _msg.get("content") or "",
            "index": idx,
            "logprobs": None,
            "finish_reason": _choice.get("finish_reason") or "stop",
        })
        _u = chat_json.get("usage") or {}
        for _k in usage:
            usage[_k] += int(_u.get(_k) or 0)
        resolved_model = chat_json.get("model") or resolved_model
        if zeroshield is None and isinstance(chat_json.get("zeroshield"), dict):
            zeroshield = chat_json["zeroshield"]

    result = {
        "id": cmpl_id,
        "object": "text_completion",
        "created": created,
        "model": resolved_model,
        "choices": choices,
        "usage": usage,
    }
    if zeroshield is not None:
        # Mirror the ZeroShield trace top-level (parity with chat/responses; SDK extra=allow).
        result["zeroshield"] = zeroshield
    return JSONResponse(status_code=200, content=result, headers={"x-request-id": cmpl_id})


# D4: surfaces a firewall gateway does not implement. Return a clean nested 404 (via the
# compat shim) so the stock SDK raises NotFoundError instead of hanging or 500-ing.
# (/v1/files + /v1/batches + /v1/images/* + /v1/audio/* are candidates for future thin
#  implementations; today they fail fast and correctly. /v1/completions is now implemented.)
async def _openai_surface_unimplemented(request: Request):
    return JSONResponse(status_code=404, content={
        "error": "not_found",
        "message": f"The endpoint '{request.url.path}' is not implemented by the ZeroShield gateway.",
        "code": "endpoint_not_found"})

for _p, _methods in (
    ("/v1/files", ["POST", "GET"]),
    ("/v1/files/{rest:path}", ["GET", "POST", "DELETE"]),
    ("/v1/batches", ["POST", "GET"]),
    ("/v1/batches/{rest:path}", ["GET", "POST"]),
    ("/v1/images/{rest:path}", ["POST"]),
    ("/v1/audio/{rest:path}", ["POST"]),
    ("/v1/fine_tuning/{rest:path}", ["GET", "POST"]),
):
    app.add_api_route(_p, _openai_surface_unimplemented, methods=_methods, include_in_schema=False)


class _HealthEndpointAccessFilter(logging.Filter):
    """Suppress uvicorn access-log lines for health-check endpoints.

    Prevents readiness-probe polling (/health, /api/health, etc.) from
    drowning out real request traffic in the gateway access log.
    """

    _SUPPRESSED = frozenset(
        ["/health", "/api/health", "/readyz", "/livez", "/ping", "/_health"]
    )

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        msg = record.getMessage()
        return not any(path in msg for path in self._SUPPRESSED)


class _TopologyRedactionFilter(logging.Filter):
    """M8: redact internal model-group / fallback-chain topology from log records
    emitted by LiteLLM's OWN loggers (e.g. "Received Model Group=...",
    "Available Model Group Fallbacks=[...]"). The gateway already sanitizes its
    own error logs + client responses; this catches the third-party litellm
    Router logging so internal model names never reach log aggregators/dashboards.
    """

    _KEYWORDS = ("Available Model Group Fallbacks", "Model Group", "model_group", "Fallbacks")

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
            scrubbed = msg
            if any(kw in scrubbed for kw in self._KEYWORDS):
                for kw in self._KEYWORDS:
                    scrubbed = scrubbed.replace(kw, "[REDACTED]")
            scrubbed = _scrub_log_topology(scrubbed)
            if scrubbed != msg:
                record.msg = scrubbed
                record.args = ()
        except Exception:
            pass
        return True


# B-1: scrub the concrete platform GUARD model id + AWS region from ANY log line.
# The guard model / region appear across several gateway loggers (bedrock_client,
# llm_judge, main) — scrubbing at the formatter (below) + this filter is universal
# so internal topology never reaches log aggregators regardless of emitter.
_TOPOLOGY_MODEL_RE = re.compile(
    r"\b(?:global\.|us\.|eu\.|apac\.)?(?:anthropic|amazon|meta|cohere|mistral|ai21|deepseek|stability)"
    r"\.[\w.:\-]+",
    re.IGNORECASE,
)
_TOPOLOGY_REGION_RE = re.compile(r"\b(?:us|eu|ap|sa|ca|me|af|apac)-[a-z]+-\d\b", re.IGNORECASE)


def _scrub_log_topology(text: str) -> str:
    """Alias concrete guard-model ids + AWS regions in a log string (B-1)."""
    try:
        text = _TOPOLOGY_MODEL_RE.sub("zeroshield-guard", text)
        text = _TOPOLOGY_REGION_RE.sub("[region]", text)
    except Exception:
        pass
    return text


_LOGGING_CONFIGURED = False


def _configure_logging() -> None:
    """Configure root logging level from GATEWAY_LOG_LEVEL env var.

    Falls back to INFO when the env var is absent (production default).
    When GATEWAY_LOG_JSON=true, emits every record as a compact JSON line
    so structured log aggregators (Loki, CloudWatch, Datadog) can parse them.

    Idempotent: safe to call from both ``main()`` and module import. This
    matters because the production container launches the app via
    ``uvicorn ai_mesh_gateway.main:app``, which imports the ``app`` object
    directly and never calls ``main()`` — without the import-time call below,
    application loggers (``gateway.*``) would only emit via Python's bare
    ``lastResort`` handler (WARNING+ only, no formatting), silently dropping
    INFO diagnostics such as the stdio-adapter's stderr-tail capture.
    """
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    raw_level = os.environ.get("GATEWAY_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, raw_level, logging.INFO)

    use_json = os.environ.get("GATEWAY_LOG_JSON", "false").lower() in ("1", "true", "yes")

    if use_json:
        # Inline JSON formatter — no extra dependency required.
        import json as _json

        class _JSONFormatter(logging.Formatter):
            SERVICE = "gateway"

            def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
                # logging.Formatter.formatTime uses time.strftime, which does NOT
                # support %f (microseconds) — it would emit the literal ".%f".
                # Build a real ISO-8601 UTC timestamp from record.created instead.
                import datetime as _dt

                _ts = _dt.datetime.fromtimestamp(
                    record.created, tz=_dt.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")
                payload = {
                    "timestamp": _ts,
                    "level": record.levelname,
                    "service": self.SERVICE,
                    "component": record.name,
                    "module": record.module,
                    "lineno": record.lineno,
                    # B-1: scrub internal guard-model id + region from EVERY log
                    # line at the universal formatter choke point.
                    "message": _scrub_log_topology(record.getMessage()),
                }
                # P9c: surface the per-request correlation id (set at the top of
                # the chat/rag/embeddings handlers) so every log line for a
                # request can be joined. Empty/unset is fine — never crash.
                try:
                    _rid = _REQUEST_ID.get("")
                    if _rid:
                        payload["request_id"] = _rid
                except Exception:
                    pass
                if record.exc_info:
                    payload["exc_info"] = _scrub_log_topology(self.formatException(record.exc_info))
                return _json.dumps(payload, ensure_ascii=False)

        fmt = _JSONFormatter()
    else:
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[handler], force=True)

    # Avoid dumping full HTTP response bodies (e.g. Django DEBUG HTML pages) when
    # GATEWAY_LOG_LEVEL=DEBUG.
    for noisy in ("httpcore", "httpx", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Apply health-endpoint suppression to the uvicorn access logger so that
    # readiness-probe requests (/health, /api/health, etc.) don't pollute the log
    # stream even when --log-level debug is active.
    uvicorn_access_log = logging.getLogger("uvicorn.access")
    uvicorn_access_log.addFilter(_HealthEndpointAccessFilter())

    # M8: scrub internal model-group / fallback topology from LiteLLM's own log
    # records so internal model names never leak into log aggregators.
    _topology_filter = _TopologyRedactionFilter()
    for _ln in (
        "LiteLLM Router", "LiteLLM", "litellm", "litellm.router",
        # B-1: the bedrock_client's own INFO lines leak the concrete guard model
        # id + region; scrub them through the same topology filter.
        "gateway.bedrock_client",
    ):
        logging.getLogger(_ln).addFilter(_topology_filter)

    _LOGGING_CONFIGURED = True


# Configure logging at import time so application loggers are wired even when the
# app is launched via ``uvicorn ai_mesh_gateway.main:app`` (which never calls
# ``main()``). The idempotent guard above makes the later ``main()`` call a no-op.
try:
    _configure_logging()
except Exception:  # pragma: no cover — logging setup must never break startup
    pass


def main():
    _configure_logging()
    cfg = load_config()
    uvicorn.run(app, host="0.0.0.0", port=cfg["port"])


if __name__ == "__main__":
    main()
