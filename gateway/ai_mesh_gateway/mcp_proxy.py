"""Gateway proxy routes for MCP traffic via Secure-MCP-Gateway and direct upstreams.

Provides transparent proxying from the gateway's data plane to upstream MCP
servers (streamable-http, sse, websocket) and stdio adapter execution, plus
an external proxy for hostnames that have OpenSSL compatibility issues when
called from internal containers.

Additionally provides org-scoped external gateway routes at
/gateway/{org_slug}/mcp/{server_slug}/* for agent/SDK consumption.
"""

import hmac
import json
import asyncio
import logging
import os
import re
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from jobs import enqueue_job

from mcp_scan_orchestrator import scan_mcp_payload

try:  # package vs top-level import (mirrors mcp_oauth_proxy import style)
    from ._url_guard import is_safe_outbound_url
except ImportError:  # pragma: no cover - flat-module deployment
    from _url_guard import is_safe_outbound_url

try:  # CLEANUP-01: the gateway failure classifier (clean client errors, dev-only cause)
    from .mcp_error_classifier import sanitize_mcp_error
except ImportError:  # pragma: no cover - flat-module deployment
    from mcp_error_classifier import sanitize_mcp_error

LOG = logging.getLogger("gateway.mcp_proxy")

router = APIRouter(prefix="/v1/mcp", tags=["MCP Proxy"])


def _discovery_error_response(clean: dict, *, jsonrpc: str = "2.0", msg_id=1):
    """CLEANUP-02: a clean JSON-RPC discovery error envelope. The message is the
    sanitized, non-revealing summary; the stable client ``code`` + correlation ``ref``
    ride alongside so the backend/frontend can surface them and a dev can retrieve the
    real cause by ref. NEVER carries a raw exc / upstream body / hostname."""
    return JSONResponse(
        content={
            "jsonrpc": jsonrpc,
            "id": msg_id,
            "error": {"code": -32000, "message": clean["error"]},
            "code": clean["code"],
            "ref": clean["ref"],
        },
        status_code=200,
    )

# Org-scoped external gateway router — auth IS enforced on this router.
org_gateway_router = APIRouter(prefix="/gateway", tags=["MCP Org Gateway"])

_MCP_FIREWALL_URL = os.environ.get("MCP_FIREWALL_URL", "http://mcp-firewall:8080")
_BACKEND_URL = (
    os.environ.get("BACKEND_URL")
    or os.environ.get("AIGUARDX_BACKEND_URL")
    or os.environ.get("AI_MESH_CONTROL_URL")
    or "http://control:8000"
)
_GATEWAY_INTERNAL_API_KEY = os.environ.get(
    "GATEWAY_INTERNAL_API_KEY", os.environ.get("AGENT_API_KEY", "")
)

_CONTROL_LOG_SNIPPET = 240


def _control_request_headers(org_slug: str = "", extra: dict | None = None) -> dict[str, str]:
    """Headers for server-to-server calls to the Django control plane."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Host": "control",
        "X-Gateway-Auth": "true",
    }
    slug = (org_slug or "").strip()
    if slug:
        headers["X-Org-Slug"] = slug
    if _GATEWAY_INTERNAL_API_KEY:
        headers["X-Gateway-Internal-Key"] = _GATEWAY_INTERNAL_API_KEY
    if extra:
        headers.update(extra)
    return headers


def _control_error_snippet(resp: httpx.Response) -> str:
    """Short, safe log fragment for a non-JSON control-plane response."""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/html" in ctype or resp.text.lstrip().startswith("<"):
        return f"HTTP {resp.status_code} HTML error page (control DEBUG may be on)"
    text = (resp.text or "").strip().replace("\n", " ")
    if not text:
        return f"HTTP {resp.status_code} (empty body)"
    return f"HTTP {resp.status_code}: {text[:_CONTROL_LOG_SNIPPET]}"


def _valid_internal_key(presented: str) -> bool:
    """Constant-time validation of the server-to-server internal key.

    Replaces a naive ``==`` comparison to remove a timing side channel:
    these internal endpoints are gated solely by this shared secret, so
    the comparison must not leak length/prefix information.
    """
    if not presented or not _GATEWAY_INTERNAL_API_KEY:
        return False
    return hmac.compare_digest(presented, _GATEWAY_INTERNAL_API_KEY)

_TIMEOUT = float(os.environ.get("MCP_PROXY_TIMEOUT", "30"))
_GATEWAY_ASYNC_MCP_AUDIT = os.environ.get("GATEWAY_ASYNC_MCP_AUDIT", "false").strip().lower() in ("1", "true", "yes")

# ── CP49: decouple best-effort audit from the tool-call hot path ─────────────
# CP48 root-caused the shared ~47 RPS throughput ceiling (idle CPU; a single
# server nearly saturated the whole fleet) to the legacy audit path: it opened a
# FRESH httpx client PER tool call and AWAITED a POST to control (single-thread
# daphne) INLINE — so every tool-call completion serialized behind daphne's audit
# throughput. Fix: keep the EXACT same POST to the SAME endpoint (telemetry is
# byte-identical — control's _record_event + EnforcementEvent bridge preserved),
# but move it OFF the hot path via a PROCESS-LOCAL POOLED client + a BOUNDED
# fire-and-forget task set. The enforcement DECISION is still computed and applied
# inline; only the best-effort audit RECORD is decoupled, and it is dropped (never
# blocks the tool call, never grows unbounded) under sustained audit backpressure.
_control_audit_client: "httpx.AsyncClient | None" = None
_AUDIT_TASKS: set = set()
_AUDIT_INFLIGHT = 0
_AUDIT_MAX_INFLIGHT = int(os.environ.get("GATEWAY_AUDIT_MAX_INFLIGHT", "64"))
# CHG-0094: security-decision audits get a HIGHER inflight ceiling so they survive a
# backpressure burst that (correctly) sheds the high-volume allow/monitor/clean records.
# The shared counter still bounds total inflight to this high cap.
_AUDIT_MAX_INFLIGHT_HIGH = int(os.environ.get("GATEWAY_AUDIT_MAX_INFLIGHT_HIGH", "256"))
_AUDIT_HIGH_PRIORITY_DECISIONS = frozenset({"block", "redact", "rate_limited", "error"})


def _get_control_audit_client() -> "httpx.AsyncClient":
    """Process-local pooled client to control — each audit is a keep-alive request,
    not a fresh TCP+TLS handshake (one per gateway worker's event loop)."""
    global _control_audit_client
    if _control_audit_client is None or _control_audit_client.is_closed:
        _control_audit_client = httpx.AsyncClient(
            timeout=5,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
    return _control_audit_client


async def _post_audit_event(headers: dict, payload: dict) -> None:
    global _AUDIT_INFLIGHT
    try:
        await _get_control_audit_client().post(
            f"{_BACKEND_URL}/api/mcp-connector/internal/record-event/",
            headers=headers,
            json=payload,
        )
    except Exception as exc:  # noqa: BLE001 - audit is best-effort; never surface to the caller
        LOG.warning("Audit event record failed (async): %s", exc)
    finally:
        _AUDIT_INFLIGHT -= 1


def _spawn_audit_event(headers: dict, payload: dict) -> bool:
    """Fire the audit POST off the tool-call hot path, bounded. Best-effort: if too
    many audits are already draining to (serial) control, DROP this record rather
    than block the response or grow the backlog unbounded. asyncio is single-loop
    per worker, so the counter needs no lock.

    CHG-0094: the drop is DECISION-PRIORITY-AWARE. A SECURITY decision
    (block/redact/rate_limited/error) gets a higher inflight ceiling
    (``_AUDIT_MAX_INFLIGHT_HIGH``) so it survives a backpressure burst that sheds the
    high-volume allow/monitor/clean records first — previously an attack that produced
    many blocks could fill the queue and DROP the very block/redact audits it created,
    breaking the ...->tag->AUDIT chain silently. Every drop now also increments the
    ``mcp_audit_dropped_total`` metric so lost security audits are visible/alertable."""
    global _AUDIT_INFLIGHT
    decision = str(payload.get("decision") or "").strip().lower()
    high = decision in _AUDIT_HIGH_PRIORITY_DECISIONS
    cap = _AUDIT_MAX_INFLIGHT_HIGH if high else _AUDIT_MAX_INFLIGHT
    if _AUDIT_INFLIGHT >= cap:
        LOG.warning(
            "Audit dropped under backpressure (inflight=%s decision=%s priority=%s)",
            _AUDIT_INFLIGHT, decision or "?", "high" if high else "normal",
        )
        try:  # visibility: a non-zero high-priority series = a lost security audit
            import metrics as _metrics
            _metrics.record_mcp_audit_dropped("high" if high else "normal", decision or "unknown")
        except Exception:
            pass
        return False
    _AUDIT_INFLIGHT += 1
    task = asyncio.create_task(_post_audit_event(headers, payload))
    _AUDIT_TASKS.add(task)  # hold a ref so the task isn't GC'd mid-flight
    task.add_done_callback(_AUDIT_TASKS.discard)
    return True

# CP23: the control plane returns a 4xx with a ``reason`` in the body for an
# ENFORCEMENT denial (a policy/guard blocked the call), not a server fault. When
# the gateway records the backend's non-200 it must classify these as
# ``decision="block"`` — recording them as ``error`` under-counts blocks and
# inflates the error rate (the CP22 anomaly). These are the ``reason`` codes the
# control ``MCPToolCallView`` puts in its 400/403 RESPONSE BODY.
_ENFORCEMENT_BODY_REASONS = frozenset({
    "tool_disabled",
    "tool_not_registered",
    "tool_not_allowed",
    "invalid_arguments",          # schema validation failure (control returns 400)
    "schema_validation_failed",
    "blocked_by_policy",
    "blocked_by_builtin_policy",
    "org_scope_violation",
    "scope_denied",
    "policy_block",
})


def _classify_backend_failure(status_code: int, data) -> tuple[str, str, str]:
    """Classify a non-200 control ``/tools/call`` response.

    Returns ``(decision, reason, enforced_at)``. An enforcement denial (a 400/403
    carrying a known enforcement ``reason``, or a policy block) is a
    ``decision="block"`` attributed to the backend guard — NOT an ``error`` — so
    the audit counters reflect real enforced reality (CP22/CP23). Genuine faults
    (5xx, malformed request, server-not-found, no reason) stay ``error``.
    """
    reason = ""
    err = ""
    if isinstance(data, dict):
        reason = str(data.get("reason") or "").strip().lower()
        err = str(data.get("error") or "").strip().lower()
    if status_code in (400, 403):
        if reason in _ENFORCEMENT_BODY_REASONS:
            return "block", reason, "backend"
        # Policy blocks carry a free-text ``reason`` (the policy message); identify
        # them by the stable error marker instead.
        if "blocked by policy" in err or ("block" in err and "polic" in err):
            return "block", (reason or "blocked_by_policy"), "backend"
    return "error", f"backend_error_http_{status_code}", "gateway"

# Allowlist of external MCP server domains that can be proxied.
# Prevents open-relay abuse while still allowing known MCP endpoints.
_ALLOWED_MCP_DOMAINS = {
    "mcp.context7.com",
    "api.githubcopilot.com",
    "mcp.linear.app",
}

# CHG-0034: per-request body-size ceiling for the MCP routes (validation / DoS).
# The MCP tool-call handlers buffer the whole body (request.body()/json()); with
# no ceiling, a tenant could POST a huge body and exhaust gateway memory. The
# RAG/embeddings paths already have 413 guards; the MCP routes had none.
_MCP_MAX_BODY_BYTES = int(os.environ.get("MCP_MAX_BODY_BYTES", str(10 * 1024 * 1024)))
# CHG-0064: response-side twin of the above. The ext_mcp_proxy path buffers an
# UNTRUSTED external MCP server's whole response (resp.aread()) to scan it; an httpx
# timeout bounds TIME, not SIZE, so a fast multi-GB response OOMs the (shared) gateway
# — a cross-tenant DoS a compromised tenant server can inflict. This caps the buffered
# response bytes; the streaming (non-finite SSE) passthrough is unaffected (never held
# in memory). Env-tunable for operators who forward legitimately large tool results.
_MCP_MAX_RESPONSE_BYTES = int(os.environ.get("MCP_MAX_RESPONSE_BYTES", str(10 * 1024 * 1024)))
# CHG-0098: per-EVENT cap for the non-finite SSE streaming scanner. A single SSE event
# (one notification / server message) is small; buffering ONLY up to one event (not the
# whole long-lived stream) bounds memory while still scanning each frame. An event that
# exceeds this without a boundary is withheld (fail-closed), so an untrusted upstream
# cannot force unbounded buffering by never closing an event.
_MCP_SSE_EVENT_MAX_BYTES = int(os.environ.get("MCP_SSE_EVENT_MAX_BYTES", str(1 * 1024 * 1024)))
# CHG-0117: TOTAL bounds on a non-finite SSE stream (notifications / *subscribe). CHG-0098
# caps each EVENT at 1MB and never buffers the whole stream, but an untrusted upstream can
# stream an INFINITE sequence of small (<1MB) events forever — holding the gateway connection,
# burning CPU scanning each event, and egressing unbounded data (httpx's per-read timeout does
# NOT bound a slow-but-steady infinite stream). Cap total bytes + total events (generous
# defaults ≫ any realistic long-lived notification feed; env-tunable) and close the stream when
# exceeded, so a runaway/DoS stream is contained.
_MCP_SSE_STREAM_MAX_BYTES = int(os.environ.get("MCP_SSE_STREAM_MAX_BYTES", str(100 * 1024 * 1024)))
_MCP_SSE_STREAM_MAX_EVENTS = int(os.environ.get("MCP_SSE_STREAM_MAX_EVENTS", "100000"))
# CHG-0104: cap the NUMBER of content blocks in a tool result. The 10MB byte cap does NOT
# stop a many-tiny-block resource bomb (50k × ~200B = ~3-10MB, UNDER the byte cap) that
# amplifies cost across every per-block loop (scan, JSON serialize, filter) — a real
# CPU/mem resource limit missing alongside the byte limit. A result with more blocks is
# withheld (fail-closed). Generous default (10k ≫ any realistic legit result, which has a
# handful of blocks); env-tunable.
_MCP_MAX_CONTENT_BLOCKS = int(os.environ.get("MCP_MAX_CONTENT_BLOCKS", "10000"))

# CHG-0115: max nesting depth of a tool RESULT structure. A deeply-nested untrusted result
# (thousands of levels) makes the recursive scan/serialize hit Python's recursion limit
# (~1000) → RecursionError. The floor's except already fail-CLOSES on that (so it never
# leaks), but relying on catching a mid-scan RecursionError is fragile and only yields a
# generic SCAN_ERROR. This PROACTIVE cap detects excessive nesting O(depth-bounded) BEFORE
# the scan and fail-closes with a clear RESOURCE_LIMIT reason. 500 ≫ any realistic legit
# result (a handful of levels) and well under the stack limit; env-tunable.
_MCP_MAX_RESULT_DEPTH = int(os.environ.get("MCP_MAX_RESULT_DEPTH", "500"))
# CHG-0116: same cap for inbound tool ARGS (attacker-controlled). Defaults to the result
# cap; separately env-tunable.
_MCP_MAX_ARG_DEPTH = int(os.environ.get("MCP_MAX_ARG_DEPTH", str(_MCP_MAX_RESULT_DEPTH)))


def _exceeds_nesting_depth(obj, limit: int) -> bool:
    """True if ``obj`` nests deeper than ``limit``. ITERATIVE (its own explicit stack)
    so the guard itself NEVER recurses — a deeply-nested untrusted payload cannot DoS
    the check that is meant to catch it. Short-circuits on the first over-limit path."""
    stack = [(obj, 0)]
    while stack:
        cur, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(cur, dict):
            nxt = depth + 1
            for v in cur.values():
                stack.append((v, nxt))
        elif isinstance(cur, list):
            nxt = depth + 1
            for v in cur:
                stack.append((v, nxt))
    return False


def _mcp_body_too_large(request) -> bool:
    """True when the declared Content-Length exceeds the MCP body ceiling.

    A cheap first-line DoS guard: an honest/naive oversized body is rejected
    BEFORE it is buffered by request.body()/json(). Defensive against test doubles
    (no headers/content-length → False). NOTE: a chunked request that omits
    Content-Length is not caught here — that adversarial case is covered by the
    streaming byte-cap in ``_mcp_read_body_capped`` (CHG-0063), which every MCP
    entry point uses to buffer the body.
    """
    hdrs = getattr(request, "headers", None)
    if not hdrs:
        return False
    try:
        cl = hdrs.get("content-length")
    except Exception:  # noqa: BLE001 — non-mapping test double
        return False
    if not cl:
        return False
    try:
        return int(cl) > _MCP_MAX_BODY_BYTES
    except (ValueError, TypeError):
        return False


def _mcp_body_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "error": "payload_too_large",
            "code": "mcp_body_too_large",
            "message": f"MCP request body exceeds the {_MCP_MAX_BODY_BYTES}-byte ceiling.",
        },
    )


class _MCPBodyTooLarge(Exception):
    """Raised by ``_mcp_read_body_capped`` when the streamed body exceeds the ceiling."""


async def _mcp_read_body_capped(request) -> bytes:
    """Buffer the request body, enforcing ``_MCP_MAX_BODY_BYTES`` on the ACTUAL bytes
    streamed — not just the declared Content-Length.

    ``_mcp_body_too_large`` only pre-checks the Content-Length HEADER; a chunked /
    no-Content-Length body slips past it, and ``request.body()`` / ``request.json()``
    then buffer the whole stream into memory with NO ceiling (a memory-exhaustion
    DoS — CHG-0034's documented limitation). This reads the stream incrementally and
    raises ``_MCPBodyTooLarge`` the instant the accumulated size crosses the ceiling,
    so the gateway never holds more than the ceiling in memory regardless of framing.
    The result is cached on the request (``_body``) so a later ``request.json()`` /
    ``request.body()`` reuses it (Starlette's own cache slot).
    """
    cached = getattr(request, "_body", None)
    if cached is not None:
        if len(cached) > _MCP_MAX_BODY_BYTES:
            raise _MCPBodyTooLarge()
        return cached
    stream = getattr(request, "stream", None)
    if not callable(stream):
        # Object without a stream() (a test double). Real Starlette Requests always
        # expose stream(), so the incremental cap below is what runs in production;
        # this branch only bounds test doubles. Fall back to body() if present, else
        # no-op (a double that supplies its payload via a mocked json()/other path).
        body_fn = getattr(request, "body", None)
        if not callable(body_fn):
            return b""
        body_bytes = await body_fn()
        if len(body_bytes) > _MCP_MAX_BODY_BYTES:
            raise _MCPBodyTooLarge()
        return body_bytes
    total = 0
    chunks: list[bytes] = []
    async for chunk in stream():
        total += len(chunk)
        if total > _MCP_MAX_BODY_BYTES:
            raise _MCPBodyTooLarge()
        chunks.append(chunk)
    body_bytes = b"".join(chunks)
    try:
        request._body = body_bytes  # populate Starlette's cache for downstream reads
    except Exception:  # pragma: no cover - non-Request test double
        pass
    return body_bytes


async def _read_response_capped(resp) -> bytes:
    """Buffer an httpx streaming RESPONSE body, enforcing ``_MCP_MAX_RESPONSE_BYTES`` on
    the ACTUAL bytes read. ``resp.aread()`` buffers the whole body with no size ceiling;
    an httpx timeout bounds only TIME, so an untrusted upstream can stream a huge body
    fast and exhaust gateway memory (CHG-0064). Reads incrementally and raises
    ``_MCPBodyTooLarge`` the instant the running total crosses the ceiling, so the
    gateway never holds more than the ceiling from an untrusted upstream in memory.
    """
    total = 0
    chunks: list[bytes] = []
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > _MCP_MAX_RESPONSE_BYTES:
            raise _MCPBodyTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def _mcp_upstream_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "error": "upstream_response_too_large",
            "code": "mcp_upstream_response_too_large",
            "message": f"Upstream MCP response exceeds the {_MCP_MAX_RESPONSE_BYTES}-byte ceiling.",
        },
    )

# ── Server config cache for transport-aware routing ──────────────────
_server_config_cache: dict[str, dict] = {}
_server_config_ttl: dict[str, float] = {}
_CONFIG_CACHE_TTL = 120  # seconds

# ── Enabled-tools cache for per-tool enable/disable enforcement ──────
# Keyed by f"{org_slug}/{server_slug}". Value is a dict:
#   {"known": set[str], "enabled": set[str], "disabled": set[str]}
# Entries expire after _ENABLED_TOOLS_TTL seconds. On backend lookup
# failure, value is None -> fail-open (allow all), but only for the SHORT
# _ENABLED_TOOLS_NEG_TTL window (M-15): a transient control-plane blip must
# not fail-open for the full TTL.
_enabled_tools_cache: dict[str, dict | None] = {}
_enabled_tools_ttl: dict[str, float] = {}
# Scan-config version observed (from Redis) when the entry was fetched.
_enabled_tools_ver: dict[str, str] = {}
_ENABLED_TOOLS_TTL = float(os.environ.get("MCP_ENABLED_TOOLS_TTL", "30"))
_ENABLED_TOOLS_NEG_TTL = float(os.environ.get("MCP_ENABLED_TOOLS_NEG_TTL", "2"))

# ── Cross-process cache invalidation via Redis version keys (M-15) ──
# Control bumps (INCR) these keys whenever tool enable/disable or scan
# config changes (mcp_connector/signals.py):
#   mcp:scan_ver:{org_slug}                 — org-scoped scan-control changes
#   mcp:scan_ver:{org_slug}:{server_slug}   — server/tool-level changes
# The gateway folds both into a composite version and treats a cached
# enabled-tools entry as stale the moment the version changes. This is one
# cheap Redis MGET per MCP call; absent keys read as version 0. When Redis
# is unreachable the check degrades to pure-TTL behaviour (status quo ante)
# instead of forcing an HTTP refetch per call.
_SCAN_VER_KEY_PREFIX = "mcp:scan_ver"

_scan_ver_redis = None  # lazy singleton; tests inject a fakeredis client here


def _get_scan_ver_redis():
    """Lazy shared async Redis client for scan-version reads."""
    global _scan_ver_redis
    if _scan_ver_redis is None:
        import redis.asyncio as aioredis

        _scan_ver_redis = aioredis.from_url(
            os.environ.get("GATEWAY_REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return _scan_ver_redis


async def _current_scan_version(org_slug: str, server_slug: str) -> str | None:
    """Composite "org_ver:server_ver" from Redis, or None when unavailable.

    None means "cannot determine" — the caller degrades to pure-TTL cache
    validity instead of refetching over HTTP on every call.
    """
    try:
        client = _get_scan_ver_redis()
        org_ver, srv_ver = await client.mget(
            f"{_SCAN_VER_KEY_PREFIX}:{org_slug}",
            f"{_SCAN_VER_KEY_PREFIX}:{org_slug}:{server_slug}",
        )
        return f"{org_ver or 0}:{srv_ver or 0}"
    except Exception as exc:
        LOG.debug(
            "Scan-version read failed (org=%s server=%s): %s — degrading to TTL-only cache",
            org_slug, server_slug, exc,
        )
        return None


async def _proxy(base_url: str, path: str, request: Request) -> JSONResponse:
    """Forward an HTTP request to an upstream MCP service."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding", "accept-encoding")
    }
    body = await request.body()
    async with httpx.AsyncClient(timeout=max(_TIMEOUT, 60)) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body if body else None,
                params=dict(request.query_params),
            )
            try:
                data = resp.json()
            except Exception:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/html" in ctype or (resp.text or "").lstrip().startswith("<"):
                    LOG.warning("MCP proxy upstream returned HTML: %s", _control_error_snippet(resp))
                    return JSONResponse(
                        content={
                            "error": "Upstream returned an HTML error page",
                            "detail": _control_error_snippet(resp),
                        },
                        status_code=502,
                    )
                data = resp.text
            return JSONResponse(content=data, status_code=resp.status_code)
        except httpx.RequestError as exc:
            # CLEANUP-04: never leak the raw exception (host / connection internals).
            LOG.error("MCP proxy error [%s] → %s: %s", type(exc).__name__, url, exc)
            clean = await sanitize_mcp_error(exc=exc)
            return JSONResponse(content=clean, status_code=502)


async def _get_server_config(org_slug: str, server_slug: str) -> dict | None:
    """Fetch server registration config from backend (cached).

    Returns dict with keys: transport, command, args, env_vars, url, name
    or None if server not found.
    """
    cache_key = f"{org_slug}/{server_slug}"
    now = time.time()
    if cache_key in _server_config_cache and now - _server_config_ttl.get(cache_key, 0) < _CONFIG_CACHE_TTL:
        return _server_config_cache[cache_key]

    headers = _control_request_headers(org_slug)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BACKEND_URL}/api/mcp-connector/servers/",
                headers=headers,
            )
            if resp.status_code != 200:
                LOG.warning(
                    "Server config lookup failed: %s",
                    _control_error_snippet(resp),
                )
                return None
            servers = resp.json()
            if not isinstance(servers, list):
                servers = servers.get("results", [])
            for srv in servers:
                if srv.get("server_slug") == server_slug:
                    config = {
                        "transport": (srv.get("transport") or "streamable-http").strip().lower(),
                        "command": srv.get("command", ""),
                        "args": srv.get("args", []),
                        "env_vars": srv.get("env_vars", {}),
                        "url": srv.get("url", ""),
                        "name": srv.get("name", ""),
                    }
                    _server_config_cache[cache_key] = config
                    _server_config_ttl[cache_key] = now
                    return config
    except Exception as exc:
        LOG.warning("Server config lookup error: %s", exc)
    return None


def _apply_fresh_config_overrides(
    config: dict | None, body: dict, org_slug: str, server_slug: str
) -> dict | None:
    """Override (TTL-)cached server config with authoritative values from the body.

    The control plane sends the current ``url``/``transport`` in every internal
    discover/call request. Without this, an operator who edits a server's URL or
    transport and re-syncs within ``_CONFIG_CACHE_TTL`` (120s) would hit the STALE
    cached config -- which previously made a fixed Cloudflare server still look
    broken (the gateway kept using the old /sse URL). We prefer the body-supplied
    values and refresh the shared cache so the data-plane sees the new config too.

    Returns the effective config (possibly ``None`` if neither cache nor body
    yields a usable config, so the caller can return 404).
    """
    override: dict = {}
    url = (body.get("url") or "").strip()
    transport = (body.get("transport") or "").strip().lower()
    if url:
        override["url"] = url
    if transport:
        override["transport"] = transport
    if not override:
        return config
    merged = {**(config or {}), **override}
    cache_key = f"{org_slug}/{server_slug}"
    _server_config_cache[cache_key] = merged
    _server_config_ttl[cache_key] = time.time()
    return merged


# ── Per-tool enable/disable enforcement (works for ALL transports) ──

async def _get_enabled_tools(org_slug: str, server_slug: str) -> dict | None:
    """Fetch (and cache) enabled/disabled/known tool sets for a server.

    Returns a dict {"known": set, "enabled": set, "disabled": set} or
    None if the backend lookup failed (caller should fail-open).
    """
    if not org_slug or not server_slug:
        return None
    # TODO(D10, G7/G8): Cache key is currently per-(org, server) only. If
    # per-user/per-agent/per-role tool enablement is ever introduced upstream
    # (control plane), this key MUST be widened to include the actor dimension
    # (e.g. key_prefix or user_id or sorted(roles)) to avoid cross-actor cache
    # bleed where actor A's enabled-tool view would mask actor B's restrictions.
    # Today, tool enable/disable is server-scoped (not actor-scoped), so the
    # current key is correct; this comment marks the invariant for future work.
    cache_key = f"{org_slug}/{server_slug}"
    now = time.time()
    fetch_ver: str | None = None
    have_ver = False
    if cache_key in _enabled_tools_cache:
        cached = _enabled_tools_cache[cache_key]
        age = now - _enabled_tools_ttl.get(cache_key, 0)
        if cached is None:
            # Negative cache: a failed backend lookup fails-open, but only
            # for the short _ENABLED_TOOLS_NEG_TTL window — never the full TTL.
            if age < _ENABLED_TOOLS_NEG_TTL:
                return None
        elif age < _ENABLED_TOOLS_TTL:
            fetch_ver = await _current_scan_version(org_slug, server_slug)
            have_ver = True
            if fetch_ver is None or fetch_ver == _enabled_tools_ver.get(cache_key):
                # Version unchanged (or Redis unavailable -> TTL-only fallback).
                return cached
            # Control bumped the scan version -> entry is stale; refetch now.

    if not have_ver:
        # Read the version BEFORE the HTTP fetch: if control bumps mid-fetch,
        # the stored version predates the bump and the next call refetches.
        fetch_ver = await _current_scan_version(org_slug, server_slug)

    headers = _control_request_headers(org_slug, {"X-Server-Slug": server_slug})
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{_BACKEND_URL}/api/mcp-connector/internal/enabled-tools/",
                headers=headers,
                params={"server_slug": server_slug},
            )
            if resp.status_code != 200:
                LOG.warning(
                    "Enabled-tools lookup failed (org=%s server=%s): %s",
                    org_slug,
                    server_slug,
                    _control_error_snippet(resp),
                )
                _enabled_tools_cache[cache_key] = None
                _enabled_tools_ttl[cache_key] = now
                _enabled_tools_ver.pop(cache_key, None)
                return None
            data = resp.json() or {}
            result = {
                "known": set(data.get("known_tools") or []),
                "enabled": set(data.get("enabled_tools") or []),
                "disabled": set(data.get("disabled_tools") or []),
                # Propagate scan enforcement controls so _effective_scan_action
                # (tool, enabled_info) can resolve the per-tool override and the
                # server default without an extra backend round-trip per call.
                # Dual-read: prefer the new ``scan_action`` keys, fall back to the
                # legacy ``presidio_action`` keys during the deprecation window so a
                # stale (<=30s) cache never silently fails open to "tag".
                "default_scan_action": (
                    data.get("default_scan_action")
                    or data.get("default_presidio_action")
                    or "tag"
                ),
                "tool_scan_actions": (
                    data.get("tool_scan_actions")
                    or data.get("tool_presidio_actions")
                    or {}
                ),
                "server_id": data.get("server_id"),
                "scan_controls_configured": True,
                "effective_scan_controls": data.get("effective_scan_controls") or {},
                "effective_scan_controls_by_tool": data.get("effective_scan_controls_by_tool") or {},
                "mcp_tier2_enabled": data.get("mcp_tier2_enabled"),
                "tier2_strict": data.get("tier2_strict", True),
            }
            _enabled_tools_cache[cache_key] = result
            _enabled_tools_ttl[cache_key] = now
            if fetch_ver is not None:
                _enabled_tools_ver[cache_key] = fetch_ver
            else:
                # Version unknown at fetch time (Redis was unavailable): drop
                # any stored version so the next versioned check refetches
                # once instead of trusting a stale association.
                _enabled_tools_ver.pop(cache_key, None)
            return result
    except Exception as exc:
        LOG.warning("Enabled-tools lookup error (org=%s server=%s): %s",
                    org_slug, server_slug, exc)
        _enabled_tools_cache[cache_key] = None
        _enabled_tools_ttl[cache_key] = now
        _enabled_tools_ver.pop(cache_key, None)
        return None


def _filter_tools_by_enabled(tools: list, enabled_info: dict | None) -> list:
    """Drop entries whose name is in disabled set. Unknown tools pass through."""
    if not enabled_info or not isinstance(tools, list):
        return tools
    disabled = enabled_info.get("disabled") or set()
    if not disabled:
        return tools
    out = []
    for t in tools:
        if not isinstance(t, dict):
            out.append(t)
            continue
        name = t.get("name") or t.get("tool_name") or ""
        if name in disabled:
            continue
        out.append(t)
    return out


def _is_tool_disabled(tool_name: str, enabled_info: dict | None) -> bool:
    """True iff backend explicitly marked this tool disabled. Unknowns -> False."""
    if not enabled_info or not tool_name:
        return False
    return tool_name in (enabled_info.get("disabled") or set())


def _filter_tools_by_key_allowlist(tools: list, auth) -> list:
    """Drop tools NOT permitted by the caller's per-key ``mcp_allowed_tools``.

    CHG-0038: least-privilege VISIBILITY parity with the call-time authz
    (``_tool_allowed_by_key``, CHG-0006). tools/list previously filtered only by the
    server-level ``disabled`` set, so a restricted key could SEE tools it would be
    403'd on at call time (info disclosure + authz inconsistency). Empty/absent
    allowlist = all tools visible (no filtering, mirroring ``_tool_allowed_by_key``);
    non-dict / name-less entries pass through (same as ``_filter_tools_by_enabled``).
    """
    if not isinstance(tools, list) or auth is None:
        return tools
    allowed = list(getattr(auth, "mcp_allowed_tools", None) or [])
    if not allowed:
        return tools
    allowed_set = set(allowed)
    out = []
    for t in tools:
        if not isinstance(t, dict):
            out.append(t)
            continue
        name = t.get("name") or t.get("tool_name") or ""
        if name and name not in allowed_set:
            continue
        out.append(t)
    return out


def _mcp_request_correlation_id(request, msg_id=None) -> str:
    """Stable per-request correlation id for MCP audit events + cross-service tracing.

    CHG-0050: prefer the inbound ``X-Request-ID`` header, so a caller / upstream trace
    id flows into every MCPEvent for the request and can be correlated across
    gateway → broker → sandbox. Falls back to the JSON-RPC ``id`` (JSON-RPC route),
    else ``""`` (which ``_record_gateway_event`` turns into a generated ``mcp-<ms>``
    id). Bounded so a hostile header can't bloat the audit record.
    """
    try:
        hdr = request.headers.get("x-request-id")
    except Exception:
        hdr = None
    if hdr:
        return str(hdr)[:200]
    return str(msg_id) if msg_id is not None else ""


async def _record_gateway_event(
    org_slug: str,
    server_slug: str,
    tool_name: str,
    decision: str,
    reason: str = "",
    request_id: str = "",
    latency_ms: int = 0,
    metadata: dict | None = None,
    compliance_tags: list | None = None,
    scan_findings: list | None = None,
) -> None:
    """Best-effort record of MCP events via async queue or legacy HTTP path."""
    if not org_slug:
        return

    # CHG-0087/0088: meter the decision + compliance tags + latency so the 1.4 guardrails
    # are visible in Prometheus (not only the MCPEvent audit trail). Fail-safe: never
    # break the call.
    try:
        import metrics as _metrics
        _metrics.record_mcp_scan_decision(org_slug, decision, compliance_tags, latency_ms)
    except Exception:
        pass

    if _GATEWAY_ASYNC_MCP_AUDIT:
        await enqueue_job(
            job_type="mcp_audit",
            request_id=request_id or f"mcp-{int(time.time() * 1000)}",
            org_id=None,
            payload={
                "organization_id": None,
                "org_slug": org_slug,
                "server_slug": server_slug,
                "tool_name": tool_name,
                "decision": decision,
                "policy_reason": reason,
                "request_id": request_id,
                "latency_ms": latency_ms,
                "metadata": metadata or {},
                "compliance_tags": compliance_tags or [],
                "scan_findings": scan_findings or [],
                # legacy alias retained during the presidio->scan rename window
                "presidio_findings": scan_findings or [],
            },
        )
        return

    headers = _control_request_headers(
        org_slug,
        {"X-Server-Slug": server_slug or ""},
    )
    payload = {
        "server_slug": server_slug,
        "tool_name": tool_name,
        "decision": decision,
        "reason": reason,
        "request_id": request_id,
        "latency_ms": latency_ms,
        "metadata": metadata or {},
        "compliance_tags": compliance_tags or [],
        "scan_findings": scan_findings or [],
        # legacy alias retained during the presidio->scan rename window
        "presidio_findings": scan_findings or [],
    }
    # CP49: fire-and-forget (pooled, bounded) — do NOT await a per-call daphne
    # round-trip on the hot path; that inline await was the CP48 ~47 RPS ceiling.
    # Same endpoint + same payload → identical persistence (_record_event + bridge).
    _spawn_audit_event(headers, payload)


# ── Scan enforcement helpers ─────────────────────────────────────────


def _effective_scan_action(tool_name: str, enabled_info: dict | None) -> str:
    """Resolve the effective scan enforcement action for a tool call.

    Order of precedence:
      1. Per-tool override (``MCPToolRegistration.scan_action``)
      2. Server default (``MCPServerRegistration.default_scan_action``)
      3. ``"tag"`` (safe default — observe only, never mutate)

    The ``enabled_info`` dict comes from ``_get_enabled_tools`` and is
    populated by the control plane (``MCPGatewayEnabledToolsView``).
    Missing / stale info degrades to ``"tag"`` so the gateway never
    blocks traffic based on a partial cache. Each lookup falls back to the
    legacy ``presidio_action`` keys so a stale cache during the rename
    deprecation window resolves correctly instead of failing open.
    """
    if not enabled_info:
        return "tag"
    per_tool_map = (
        enabled_info.get("tool_scan_actions")
        or enabled_info.get("tool_presidio_actions")
        or {}
    )
    per_tool = per_tool_map.get(tool_name)
    if per_tool and per_tool != "inherit":
        return per_tool
    return (
        enabled_info.get("default_scan_action")
        or enabled_info.get("default_presidio_action")
        or "tag"
    )


def _mcp_block_on_credential_enabled() -> bool:
    """E12 FIX 1: whether a credential in tool ARGS force-blocks the call.

    Prefers the live gateway CONFIG (populated from ``load_config``); falls back
    to reading the env var directly so the gate still resolves in unit tests /
    early startup before ``main.CONFIG`` is set. Default ON.
    """
    try:
        import main as gateway_main

        cfg = getattr(gateway_main, "CONFIG", None)
        if isinstance(cfg, dict) and "mcp_block_on_credential" in cfg:
            return bool(cfg.get("mcp_block_on_credential"))
    except Exception:
        pass
    return os.environ.get("GATEWAY_MCP_BLOCK_ON_CREDENTIAL", "true").lower() in (
        "true", "1", "yes",
    )


def _findings_have_credential(
    findings: list[dict] | None,
    tags: list[str] | None = None,
) -> bool:
    """E12 FIX 1: True if a scan finding/tag indicates a secret/credential.

    A credential is identified by ANY of:
      * a finding with ``threat_type == "secret"`` (clean secret-only match), OR
      * the ``SECRET`` compliance tag (credential types map to it via
        ``COMPLIANCE_TAG_MAP``; generic PII maps to PII/GDPR/HIPAA, never
        SECRET), OR
      * a finding whose ``entity_type`` is a known ``SECRET_PATTERNS`` kind
        (covers a credential that the orchestrator collapsed under a combined
        pii+secret finding tagged ``"pii"``, e.g. github_token, or a kind with
        no compliance-tag mapping such as slack_token).

    Generic PII alone is intentionally NOT treated as a credential, so the
    default arg-redaction still applies to PII without a hard block.
    """
    if tags and "SECRET" in tags:
        return True
    if not findings:
        return False
    try:
        from patterns import SECRET_PATTERNS as _SECRET_KINDS
    except Exception:
        _SECRET_KINDS = {}
    for f in findings:
        if not isinstance(f, dict):
            continue
        if f.get("threat_type") == "secret":
            return True
        ent = f.get("entity_type") or ""
        if ent in _SECRET_KINDS:
            return True
        # The combined-finding ``detail`` is "Matched: <kind>, <kind>".
        detail = str(f.get("detail") or "")
        if detail.startswith("Matched:"):
            kinds = [k.strip() for k in detail[len("Matched:"):].split(",")]
            if any(k in _SECRET_KINDS for k in kinds):
                return True
    return False


def _mcp_redact_result_on_detect_enabled() -> bool:
    """E12: whether a secret/PII detected in a tool RESULT force-redacts the
    result even when the resolved scan_action defaults to "tag"/"monitor".

    Prefers the live gateway CONFIG (populated from ``load_config``); falls back
    to reading the env var directly so the gate still resolves in unit tests /
    early startup before ``main.CONFIG`` is set. Default ON. Mirrors
    ``_mcp_block_on_credential_enabled`` exactly.
    """
    try:
        import main as gateway_main

        cfg = getattr(gateway_main, "CONFIG", None)
        if isinstance(cfg, dict) and "mcp_redact_result_on_detect" in cfg:
            return bool(cfg.get("mcp_redact_result_on_detect"))
    except Exception:
        pass
    return os.environ.get("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "true").lower() in (
        "true", "1", "yes",
    )


def _findings_have_secret_or_pii(findings: list[dict] | None) -> bool:
    """E12: True if an OUTPUT scan finding indicates a secret/credential OR PII.

    The result-redaction floor applies whenever sensitive data is detected, not
    just credentials (symmetric arg-block is credential-only to limit FPs, but a
    raw PII tool RESULT reaching the LLM/client is the same fail-open class).
    A finding's ``threat_type`` is ``"pii"`` or ``"secret"`` for the tier-1
    detectors; ``_findings_have_credential`` additionally catches a credential
    collapsed under a combined ``"pii"``-tagged finding via SECRET_PATTERNS.
    """
    if not findings:
        return False
    for f in findings:
        if isinstance(f, dict) and f.get("threat_type") in ("pii", "secret"):
            return True
    return _findings_have_credential(findings)


def _findings_have_infra_network_leak(findings: list[dict] | None) -> bool:
    """CHG-0074: True if an OUTPUT scan finding is an ``ip_leakage`` whose matched
    detector keys include an ENFORCEABLE internal NETWORK address (private/link-
    local/CGNAT IPv4, internal IPv6, internal hostname, internal URL) — i.e. a key
    ``redact_all`` actually masks (``_INFRA_NETWORK_KEYS``).

    WHY: the E12 result-redaction floor only fired for secret/PII
    (``_findings_have_secret_or_pii``), so an internal-network address DETECTED +
    TAGGED (``INFRA``) but under the default ``tag`` posture egressed RAW — a
    fail-OPEN asymmetric with PII/secret (an internal / cloud-metadata IP in a tool
    RESULT is the same infra-disclosure class the mandate's "no PII/IP/regulated
    escape" forbids). Scoped to NETWORK keys ONLY so a flag-tier private FILE PATH
    (which ``redact_all`` cannot mask) never triggers a floor re-scan that would
    then force-BLOCK a benign code/file tool result (file paths stay flag-tier).
    """
    if not findings:
        return False
    from patterns import _INFRA_NETWORK_KEYS
    network_keys = set(_INFRA_NETWORK_KEYS)
    for f in findings:
        if not isinstance(f, dict) or f.get("threat_type") != "ip_leakage":
            continue
        if set(f.get("matched_kinds") or ()) & network_keys:
            return True
    return False


def _findings_have_exfil(findings: list[dict] | None) -> bool:
    """CHG-0096: True if an OUTPUT scan finding is a defanged EXFIL beacon
    (``threat_type == "exfil"``). The orchestrator detects a zero-click auto-render
    exfil beacon (markdown-image / HTML img / srcset) under any posture but, like
    PII/secret/infra, only APPLIES the defang mutation under a ``redact`` action — so
    the E12 result-redaction floor must ALSO fire for an exfil finding to force the
    defang under the default ``tag`` posture (else the beacon egressed raw)."""
    if not findings:
        return False
    return any(
        isinstance(f, dict) and f.get("threat_type") == "exfil" for f in findings
    )


async def _scan_tool_args_block(
    arguments,
    *,
    tool_name: str,
    enabled_info: dict | None,
    org_slug: str = "",
    server_slug: str = "",
    actor: dict | None = None,
) -> tuple[object, bool, list[str], list[dict], dict]:
    """Inbound tool-ARG scan + credential hard-block, mirroring the main path.

    Reused by the bare REST / internal / external proxy paths so they reach
    PARITY with ``org_mcp_jsonrpc``'s inbound enforcement (mcp_proxy.py ~1676).

    Returns ``(scanned_args, blocked, tags, findings, meta)``:
      * ``scanned_args`` — arguments after any per-tier inbound redaction the
        orchestrator applied (``is arguments`` when untouched).
      * ``blocked`` — True if the call MUST NOT be forwarded. This is the
        orchestrator's own block OR the E12 credential force-block (a credential
        in args even when the resolved action only "tags"). A per-tool
        ``"monitor"`` action is an explicit observe-only override and wins
        (never force-blocks), exactly like the main path.

    Fail-safe: if the scan helper itself errors, do NOT raise (callers must not
    500). We return ``blocked=True`` with a synthetic credential-detect meta so
    an arg-scan failure prefers blocking over silently egressing raw arguments.
    """
    scan_action = _effective_scan_action(tool_name, enabled_info)
    # CHG-0116: proactive depth cap on inbound ARGS (input-side twin of CHG-0115). A deeply
    # -nested attacker-controlled args payload would RecursionError the recursive scan (caught
    # below as a generic ``arg_scan_error``, AND under "monitor" that fail-closed block
    # violates the observe-only contract). Iterative guard (own stack, can't itself be DoS'd),
    # action-aware: real action → fail CLOSED (RESOURCE_LIMIT); monitor → forward unchanged.
    if isinstance(arguments, (dict, list)) and _exceeds_nesting_depth(arguments, _MCP_MAX_ARG_DEPTH):
        LOG.warning(
            "mcp_proxy.args_too_deeply_nested org=%s server=%s tool=%s (>%d) action=%s",
            org_slug, server_slug, tool_name, _MCP_MAX_ARG_DEPTH, scan_action,
        )
        if scan_action == "monitor":
            return arguments, False, [], [], {
                "args_too_deeply_nested": True, "monitor_scan_skipped": True,
            }
        return arguments, True, ["RESOURCE_LIMIT"], [], {
            "args_too_deeply_nested": True, "max_arg_depth": _MCP_MAX_ARG_DEPTH,
        }
    try:
        scanned, blocked, tags, findings, meta = await _mcp_security_scan(
            arguments,
            scan_direction="input",
            tool_name=tool_name,
            enabled_info=enabled_info,
            org_slug=org_slug,
            server_slug=server_slug,
            actor=actor,
        )
    except Exception as exc:  # pragma: no cover - defensive; scan never 500s
        LOG.warning(
            "mcp_proxy.arg_scan_failed org=%s server=%s tool=%s: %s (fail-closed: blocking)",
            org_slug, server_slug, tool_name, exc,
        )
        # Prefer blocking on an arg-scan failure: a credential in args that we
        # could not inspect must not egress to the backend / upstream MCP server.
        return arguments, True, [], [], {"arg_scan_error": True, "credential_force_block": True}

    if (
        not blocked
        and _mcp_block_on_credential_enabled()
        and scan_action != "monitor"
        and _findings_have_credential(findings, tags)
    ):
        blocked = True
        meta = {**meta, "credential_force_block": True}
    return scanned, blocked, tags, findings, meta


def _result_content_texts(result_content) -> list[str]:
    """CHG-0100: the ``text`` of each content block in a tool result (for cross-block
    split-secret detection). Handles a ``{"content":[…]}`` dict or a bare block list."""
    blocks = None
    if isinstance(result_content, dict):
        blocks = result_content.get("content")
    elif isinstance(result_content, list):
        blocks = result_content
    if not isinstance(blocks, list):
        return []
    return [b["text"] for b in blocks
            if isinstance(b, dict) and isinstance(b.get("text"), str)]


# CHG-0102: a secret/credential token is short, so a cross-block split only spans a
# BLOCK BOUNDARY within this many chars. The split scan trims each block to its boundary
# regions (below), bounding its cost to O(num_blocks * span) instead of O(total_text) —
# a LONG block's interior is already covered for CONTIGUOUS secrets by the full-text scan.
_MCP_SPLIT_SECRET_SPAN = int(os.environ.get("MCP_SPLIT_SECRET_SPAN", "512"))


def _boundary_concat(texts: list[str]) -> str:
    """CHG-0102: concatenate blocks keeping only their BOUNDARY regions, so a secret that
    spans block boundaries stays contiguous while a long block's interior (covered by the
    full-text scan) is dropped — bounds the split-scan cost. A sentinel breaks a long
    block's own first/last halves so they cannot form a false cross-boundary span."""
    span = _MCP_SPLIT_SECRET_SPAN
    parts: list[str] = []
    for t in texts:
        if len(t) <= 2 * span:
            parts.append(t)
        else:
            parts.append(t[:span] + "\n\x00\n" + t[-span:])
    return "".join(parts)


def _result_has_split_secret(result_content) -> tuple[bool, list[str]]:
    """CHG-0100: detect a HIGH-CONFIDENCE secret SPLIT across content-array items.

    A malicious upstream can split a secret so each half is a benign sub-pattern
    (``…AKIAIOSFOD`` / ``NN7EXAMPLE…``) in adjacent content blocks. The whole-payload
    scan never sees the value contiguous (the blocks are separated by JSON structure),
    yet a client that CONCATENATES the text blocks reconstructs it. This scans the
    block-boundary concatenation (CHG-0102) for secrets/credentials and returns the kinds
    that appear there but NOT wholly inside any single block (i.e. reconstructed only by
    the join). Scoped to secrets/credentials (not generic PII) so the join can't
    false-fire on two adjacent benign blocks — a real AWS key / token forming across a
    boundary from legit text is astronomically unlikely."""
    texts = _result_content_texts(result_content)
    if len(texts) < 2:
        return False, []  # need >=2 blocks to split a value across
    from patterns import (
        detect_credential_exposure,
        detect_pii,
        detect_secrets,
        get_compliance_tags,
    )
    concat = _boundary_concat(texts)
    found: dict[str, str] = {}
    found.update(detect_secrets(concat))
    found.update(detect_credential_exposure(concat))
    # credentials misfiled in PII_PATTERNS (aws_access_key etc.) — SECRET-tagged only.
    found.update({
        k: v for k, v in detect_pii(concat).items()
        if "SECRET" in get_compliance_tags([k])
    })
    split_kinds = [
        kind for kind, value in found.items()
        if value and not any(str(value) in t for t in texts)
    ]
    return bool(split_kinds), split_kinds


async def _scan_tool_result_floor(
    result_content,
    *,
    tool_name: str,
    enabled_info: dict | None,
    org_slug: str = "",
    server_slug: str = "",
    actor: dict | None = None,
) -> tuple[object, bool, list[str], list[dict], dict]:
    """Outbound tool-RESULT scan + redaction FLOOR, mirroring the main path.

    Reused by the bare REST / internal / external proxy paths so they reach
    PARITY with ``org_mcp_jsonrpc``'s outbound enforcement (mcp_proxy.py ~1941).

    Returns ``(scanned_content, blocked, tags, findings, meta)``:
      * ``scanned_content`` — result content after per-tier output redaction
        AND, when the resolved action would otherwise only "tag" a detected
        secret/PII, the E12 ``"redact"`` floor re-scan (mask, never block).
        ``is result_content`` when nothing changed.
      * ``blocked`` — orchestrator output block (a hard ``"block"`` action on a
        detected result). Callers map this to their own block response shape.
      * ``meta`` carries ``result_redaction_floor=True`` when the floor masked.

    A per-tool ``"monitor"`` action wins (no floor), exactly like the main path.

    Fail-CLOSED: if the scan helper errors we cannot know whether the result
    carries PII/secret, so we MUST NOT egress it raw. We return ``blocked=True``
    (a sentinel ``SCAN_ERROR`` tag + ``result_scan_failclosed`` meta) so every
    caller withholds the result via its normal block-response shape — mirroring
    the inbound twin ``_scan_tool_args_block`` which blocks on ``arg_scan_error``.
    This is a graceful JSON-RPC/HTTP block (never a 500): the transport stays up,
    only the specific unscannable result is withheld. Availability yields to
    confidentiality — the previous behavior forwarded the RAW result on any
    scanner hiccup, a silent fail-OPEN leak (BACKSTOP_FINDINGS G2 item 2).
    """
    scan_action = _effective_scan_action(tool_name, enabled_info)
    # CHG-0104: fail CLOSED on a many-block resource bomb. The byte cap (10MB) does not
    # stop ~50k tiny blocks (~3-10MB, under the byte cap) that amplify per-block loop cost
    # (scan / JSON serialize / filter) and stall the event loop. A per-tool "monitor" is
    # observe-only and does not block. Cheap O(1) length check before the expensive scan.
    if scan_action != "monitor" and isinstance(result_content, (dict, list)):
        _blocks = result_content.get("content") if isinstance(result_content, dict) else result_content
        if isinstance(_blocks, list) and len(_blocks) > _MCP_MAX_CONTENT_BLOCKS:
            LOG.warning(
                "mcp_proxy.result_too_many_content_blocks org=%s server=%s tool=%s count=%s (FAIL-CLOSED)",
                org_slug, server_slug, tool_name, len(_blocks),
            )
            return result_content, True, ["RESOURCE_LIMIT"], [], {
                "result_too_many_content_blocks": True, "content_block_count": len(_blocks),
            }
    # CHG-0115: guard an excessively-DEEP result BEFORE the recursive scan. A structure
    # nested past the recursion limit would otherwise RecursionError mid-scan (the except
    # below fail-closes on that, but only as a generic SCAN_ERROR — AND under "monitor" that
    # fail-closed block VIOLATES the observe-only contract). This applies to BOTH actions
    # (the point is to avoid the recursion CRASH), branching on enforcement: under a real
    # action → fail CLOSED with a clear RESOURCE_LIMIT reason; under "monitor" → forward the
    # result UNSCANNED (never block), noting the skip. The check is ITERATIVE so the guard
    # itself can't be DoS'd by the deep payload.
    if isinstance(result_content, (dict, list)) and _exceeds_nesting_depth(
        result_content, _MCP_MAX_RESULT_DEPTH
    ):
        LOG.warning(
            "mcp_proxy.result_too_deeply_nested org=%s server=%s tool=%s (>%d) action=%s",
            org_slug, server_slug, tool_name, _MCP_MAX_RESULT_DEPTH, scan_action,
        )
        if scan_action == "monitor":
            # Observe-only: never block; forward unscanned (the recursive scan would crash).
            return result_content, False, [], [], {
                "result_too_deeply_nested": True, "monitor_scan_skipped": True,
            }
        return result_content, True, ["RESOURCE_LIMIT"], [], {
            "result_too_deeply_nested": True, "max_result_depth": _MCP_MAX_RESULT_DEPTH,
        }
    try:
        scanned, blocked, tags, findings, meta = await _mcp_security_scan(
            result_content,
            scan_direction="output",
            tool_name=tool_name,
            enabled_info=enabled_info,
            org_slug=org_slug,
            server_slug=server_slug,
            actor=actor,
        )
    except Exception as exc:  # pragma: no cover - defensive; scan never 500s
        LOG.warning(
            "mcp_proxy.result_scan_failed org=%s server=%s tool=%s: %s (FAIL-CLOSED: blocking)",
            org_slug, server_slug, tool_name, exc,
        )
        # Fail closed: block rather than forward an un-inspected result.
        return result_content, True, ["SCAN_ERROR"], [], {
            "result_scan_error": True,
            "result_scan_failclosed": True,
        }

    if blocked:
        return scanned, True, tags, findings, meta

    # CHG-0100: a HIGH-CONFIDENCE secret SPLIT ACROSS content-array items (each half a
    # benign sub-pattern) evades the scan above — the items are separated by JSON
    # structure so the value is never contiguous — but a client that concatenates the
    # text blocks reconstructs it. Fail CLOSED (a cross-block split cannot be masked in
    # place). A per-tool "monitor" still wins (observe-only), like the redaction floor.
    if scan_action != "monitor":
        _split, _split_kinds = _result_has_split_secret(result_content)
        if _split:
            LOG.warning(
                "mcp_proxy.cross_block_split_secret org=%s server=%s tool=%s kinds=%s (FAIL-CLOSED)",
                org_slug, server_slug, tool_name, _split_kinds,
            )
            return result_content, True, list(dict.fromkeys(list(tags) + ["SECRET"])), findings, {
                **meta, "cross_block_split_secret": True,
            }

    # E12 result-REDACTION floor: detected secret/PII but the resolved action did
    # not redact, so the result would egress RAW. Re-scan with a "redact" floor.
    if (
        scanned is result_content
        and _mcp_redact_result_on_detect_enabled()
        and scan_action != "monitor"
        and (
            _findings_have_secret_or_pii(findings)
            or _findings_have_infra_network_leak(findings)  # CHG-0074
            or _findings_have_exfil(findings)  # CHG-0096
        )
    ):
        try:
            floor_content, _fb, _ft, _ff, _fmeta = await _mcp_security_scan(
                result_content,
                scan_direction="output",
                tool_name=tool_name,
                enabled_info=enabled_info,
                org_slug=org_slug,
                server_slug=server_slug,
                actor=actor,
                enforcement_override="redact",
            )
            if _fb:
                # CHG-0074: the redact re-scan itself BLOCKED — a detected value
                # redact_all cannot mask survived the scrub (e.g. a private file path
                # alongside the network/PII leak, caught by the orchestrator's
                # egress-byte verify). Forwarding here would egress that value RAW, so
                # fail CLOSED (block) — consistent with the except-handler below and
                # the "mask, else block; never forward raw" invariant. Previously this
                # block was swallowed and the raw result was returned.
                return result_content, True, (_ft or tags), (_ff or findings), {
                    **meta, "result_redaction_floor_block": True,
                }
            if floor_content is not result_content:
                scanned = floor_content
                meta = {**meta, "result_redaction_floor": True}
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning(
                "mcp_proxy.result_floor_failed org=%s server=%s tool=%s: %s "
                "(FAIL-CLOSED: blocking — PII/secret detected but masking errored)",
                org_slug, server_slug, tool_name, exc,
            )
            # We are in this branch only because PII/secret WAS detected and the
            # resolved action did not already redact. The masking re-scan failed,
            # so ``scanned`` is still the RAW result — blocking is the only safe
            # exit (forwarding raw here would leak the detected sensitive data).
            return result_content, True, (tags or ["SCAN_ERROR"]), findings, {
                **meta, "result_scan_error": True, "result_scan_failclosed": True,
            }
    return scanned, False, tags, findings, meta


async def _scan_reframe_sse_tool_result(
    sse_text: str,
    *,
    tool_name: str,
    org_slug: str = "",
    server_slug: str = "",
    enabled_info: dict | None = None,
    actor: dict | None = None,
    scan_notifications: bool = False,
) -> tuple[str, dict | None]:
    """Scan the tool RESULT(s) inside a BUFFERED SSE (text/event-stream) body.

    MCP Streamable-HTTP delivers a tools/call result as one or more SSE EVENTS,
    each carrying a JSON-RPC message. Per the SSE spec an event's data may span
    several ``data:`` lines (concatenated with "\n"), so this parses the buffered
    SSE per EVENT — reassembling every event's ``data:`` values BEFORE json-parsing
    — and for every event that is a JSON-RPC response with a ``result``/``error`` it
    runs the outbound result floor (mask/redact via ``_scan_tool_result_floor``,
    which fails CLOSED on scan error). Non-JSON events, keep-alives, and non-result
    events pass through verbatim so the SSE framing is preserved. Reassembling per
    event (not per line) closes CHG-0093: a structural multi-line ``data:`` split
    that would otherwise slip a secret past a per-line scan.

    Returns ``(reframed_sse_text, block_info)``. ``block_info`` is None when
    nothing was hard-blocked; otherwise ``{"tags", "id", "jsonrpc"}`` which the
    caller turns into a JSON-RPC block error — the RAW result never egresses.

    Only call this for a FINITE tools/call SSE response that is already buffered;
    never for a long-lived notification stream (that would need unbounded
    buffering and could hang). Closes the SSE raw-egress leak
    (BACKSTOP_FINDINGS G2 item 2 — the previous branch forwarded SSE verbatim).
    """
    # CHG-0093: parse the buffered SSE per EVENT, not per line. Per the SSE spec
    # (WHATWG), an event's data is the concatenation of ALL its ``data:`` field
    # values joined by "\n". An untrusted upstream can therefore SPLIT a JSON-RPC
    # result across several ``data:`` lines at a structural point (JSON whitespace
    # between tokens) so each fragment is INVALID JSON on its own — evading the
    # old per-line ``json.loads`` (each fragment fell through to "not JSON → verbatim")
    # — yet a spec-compliant client reassembles the fragments into the COMPLETE
    # result, leaking the secret. Reassembling per event BEFORE scanning closes it.
    async def _scan_event(evt_lines: list[str]) -> tuple[list[str] | None, dict | None]:
        """Scan one SSE event. Returns ``(emit_lines, block_info)``: ``emit_lines`` are
        the SSE lines to append (verbatim, or reframed to a single masked ``data:``
        line); a non-None ``block_info`` means withhold the entire result."""
        if not evt_lines:
            return [], None
        data_pos = [i for i, ln in enumerate(evt_lines) if ln.strip().startswith("data:")]
        if not data_pos:
            return list(evt_lines), None
        # SSE reassembly: join every data: field value with "\n" (the wire value a
        # compliant client sees). ``strip()[5:].strip()`` mirrors the prior extraction.
        data_val = "\n".join(evt_lines[i].strip()[5:].strip() for i in data_pos)
        if not data_val:
            return list(evt_lines), None
        try:
            obj = json.loads(data_val)
        except Exception:
            return list(evt_lines), None  # not JSON (keep-alive / partial) — verbatim
        if not isinstance(obj, dict):
            return list(evt_lines), None
        # An ERROR frame (no result) can still carry a secret in its message/data from
        # an untrusted server — scan + mask it too (CHG-0043; fail CLOSED on scan error).
        target_key = "result"
        target_obj = obj.get("result")
        if target_obj is None:
            target_obj = obj.get("error")
            target_key = "error"
            if target_obj is None:
                # A NOTIFICATION frame (method+params, no result/error). On a buffered
                # tools/call result stream we pass it through; on a long-lived NON-finite
                # stream (``scan_notifications=True``, CHG-0098) an untrusted upstream can
                # smuggle sensitive data in the ``params`` of a notifications/message
                # frame, so scan the WHOLE message (params + any data field).
                if not (scan_notifications and obj.get("params") is not None):
                    return list(evt_lines), None  # notification / keep-alive — verbatim
                target_obj = obj
                target_key = None
        # Scan the ENTIRE result/error/notification (dict content/structuredContent,
        # list, or str).
        scanned, blocked, tags, _findings, _meta = await _scan_tool_result_floor(
            target_obj,
            tool_name=tool_name,
            enabled_info=enabled_info,
            org_slug=org_slug,
            server_slug=server_slug,
            actor=actor,
        )
        if blocked:
            # Hard block (policy block OR fail-closed scan error): withhold everything.
            return None, {
                "tags": list(tags),
                "id": obj.get("id"),
                "jsonrpc": obj.get("jsonrpc", "2.0"),
            }
        if scanned is not target_obj:
            if target_key is None:  # CHG-0098: whole-message (notification) scan
                obj = scanned if isinstance(scanned, dict) else obj
            else:
                obj[target_key] = scanned
            # Re-emit any non-data field lines (event:/id:/comments) verbatim, then the
            # masked payload as a SINGLE ``data:`` line (json.dumps is newline-free).
            non_data = [ln for i, ln in enumerate(evt_lines) if i not in data_pos]
            return non_data + [f"data: {json.dumps(obj)}"], None
        return list(evt_lines), None

    out_lines: list[str] = []
    event_lines: list[str] = []
    for raw_line in sse_text.split("\n"):
        if raw_line.strip() == "":
            emit, block = await _scan_event(event_lines)
            if block is not None:
                return "", block
            out_lines.extend(emit)
            out_lines.append(raw_line)  # preserve the blank event separator
            event_lines = []
        else:
            event_lines.append(raw_line)
    # Trailing event with no terminating blank line.
    emit, block = await _scan_event(event_lines)
    if block is not None:
        return "", block
    out_lines.extend(emit)
    return "\n".join(out_lines), None


def _tool_allowed_by_key(tool_name: str, auth) -> bool:
    """E12 FIX 3: enforce per-key ``mcp_allowed_tools`` allowlist.

    EMPTY list = all tools allowed (must NOT block). A non-empty list blocks any
    tool not present in it.
    """
    allowed = list(getattr(auth, "mcp_allowed_tools", None) or []) if auth else []
    if not allowed:
        return True
    return tool_name in allowed


# Tool-call counter window (seconds). A "turn" is approximated as a sliding
# per-key window so the cap survives across the separate JSON-RPC requests of a
# single conversation turn (one tool/call per request in Streamable HTTP).
_MCP_TOOL_CALL_WINDOW_SEC = 60


def _tool_call_cap_exceeded(count: int, cap: int) -> bool:
    """E12 FIX 3: pure decision for ``mcp_max_tool_calls``.

    ``cap`` of 0 (or negative) = unlimited (never blocks). Otherwise the call is
    blocked once the post-increment ``count`` exceeds ``cap``.
    """
    if cap is None or cap <= 0:
        return False
    return count > cap


async def _incr_tool_call_count(auth) -> int:
    """Increment + return this key's tool-call count in the current window.

    Redis-backed so the cap holds across the per-request JSON-RPC calls of one
    turn and across gateway workers. Fail-open (returns 0 = "no cap pressure")
    when Redis is unavailable so availability is preserved.
    """
    key_id = getattr(auth, "key_hash", None) or getattr(auth, "key_id", None) if auth else None
    if not key_id:
        return 0
    try:
        client = _get_scan_ver_redis()
        rk = f"mcp:toolcalls:{key_id}"
        # CHG-0048: INCR + set-TTL-if-missing ATOMICALLY (MULTI/EXEC pipeline).
        # The old `count = INCR; if count == 1: EXPIRE` set the window TTL ONLY on
        # the first increment, so a crash / dropped EXPIRE at that moment left the
        # key with NO TTL forever — subsequent calls (count>1) skipped the EXPIRE, so
        # the counter never reset and the key was PERMANENTLY capped once it crossed
        # mcp_max_tool_calls (a Redis-correctness / availability bug). Now the EXPIRE
        # runs on EVERY increment inside the same transaction, using `NX` (Redis 7+)
        # so it only sets the TTL when absent — preserving the FIXED 60s window
        # (never extending an existing TTL) while HEALING a missing TTL on the next
        # call. Fail-open on any Redis error is unchanged (the cap is a soft limit).
        async with client.pipeline(transaction=True) as pipe:
            pipe.incr(rk)
            pipe.expire(rk, _MCP_TOOL_CALL_WINDOW_SEC, nx=True)
            res = await pipe.execute()
        return int(res[0])
    except Exception as exc:
        LOG.warning("mcp_proxy.tool_call_count_unavailable key=%s: %s",
                    str(key_id)[:12], exc)
        return 0


def _effective_scan_controls_for_tool(
    enabled_info: dict | None,
    tool_name: str,
) -> dict:
    if not enabled_info:
        return {}
    by_tool = enabled_info.get("effective_scan_controls_by_tool") or {}
    if tool_name and tool_name in by_tool:
        return by_tool[tool_name]
    return enabled_info.get("effective_scan_controls") or {}


async def _mcp_security_scan(
    payload,
    *,
    scan_direction: str,
    tool_name: str,
    enabled_info: dict | None,
    org_slug: str = "",
    server_slug: str = "",
    actor: dict | None = None,
    enforcement_override: str | None = None,
    extra_redaction_fields: list | None = None,
) -> tuple[object, bool, list[str], list[dict], dict]:
    """Scan MCP payload via two-tier orchestrator. Returns (payload, blocked, tags, findings, metadata).

    ``extra_redaction_fields`` (3b cross-stage): named result fields to mask on an
    OUTPUT scan in addition to this scan's own matches — the caller threads the
    INPUT scan's ``policy_redaction_fields`` (from the returned meta) so an
    input-stage policy match projects those fields out of the RESPONSE.

    ``enforcement_override`` (E12 result-redaction floor): when set, it replaces
    the resolved per-tool ``scan_action`` as the enforcement passed to the
    orchestrator, so a re-scan can apply a ``"redact"`` floor to a tool RESULT
    whose default action would only tag. Per-tier ``inherit`` rows then resolve
    to the override; rows with an explicit per-tier action are unaffected.
    """
    action = enforcement_override or _effective_scan_action(tool_name, enabled_info)
    mcp_direction = "inbound" if scan_direction == "input" else "outbound"
    effective = _effective_scan_controls_for_tool(enabled_info, tool_name)
    if not effective:
        effective = {
            "scan_controls_configured": True,
            "tier1_input": {"enabled": True, "target_mode": "entire", "key_path": "", "strict_mode": "fail_open"},
            "tier1_output": {"enabled": True, "target_mode": "entire", "key_path": "", "strict_mode": "fail_open"},
            "tier2_input": {"enabled": False, "target_mode": "entire", "key_path": "", "strict_mode": "strict"},
            "tier2_output": {"enabled": False, "target_mode": "entire", "key_path": "", "strict_mode": "strict"},
        }

    scanned, result = await scan_mcp_payload(
        payload,
        scan_direction=scan_direction,
        enforcement=action,
        effective_controls=effective,
        enabled_info=enabled_info,
        org_slug=org_slug,
        server_slug=server_slug,
        tool_name=tool_name,
        actor=actor,
        extra_redaction_fields=extra_redaction_fields,
    )
    meta = {
        "scan_trace": result.scan_trace,
        "scan_direction": mcp_direction,
        "scan_action": action,
        "scan_pipeline": "two_tier",
        "monitored": result.monitored,
        # 3b: named response fields masked via per-policy RBAC field redaction
        # (empty unless a matched policy declared redaction_fields on an output
        # scan under a mutating posture). Mirrors the control HTTP path's
        # metadata.redacted_field_names so both transports audit identically.
        "redacted_fields": list(result.redacted_fields),
        # 3b cross-stage: fields THIS scan's matched policies declared (both
        # directions). The caller threads an INPUT scan's value back as
        # ``extra_redaction_fields`` on the paired OUTPUT scan.
        "policy_redaction_fields": list(result.policy_redaction_fields),
    }
    return (
        scanned,
        result.blocked,
        result.compliance_tags,
        [f.to_finding_dict() for f in result.findings],
        meta,
    )


# ── Health (aggregated) ──────────────────────────────────────────────

@router.get("/health", summary="MCP services health check")
async def mcp_health():
    """Check health of MCP infrastructure services.

    - MCP-Firewall: CLI tool, not a standalone service — reports not_configured if unreachable.
    """
    results = {}
    async with httpx.AsyncClient(timeout=5) as client:
        # MCP-Firewall — CLI tool, may not be running as a service
        try:
            resp = await client.get(f"{_MCP_FIREWALL_URL}/health")
            results["mcp_firewall"] = {"status": "healthy" if resp.is_success else "unhealthy"}
        except httpx.ConnectError:
            results["mcp_firewall"] = {
                "status": "not_configured",
                "detail": "mcp-firewall is a CLI wrapper tool, not a standalone HTTP service.",
            }
        except httpx.RequestError:
            results["mcp_firewall"] = {"status": "unreachable"}
    return results


# ── External MCP Server Proxy ────────────────────────────────────────
# Allows internal containers with older OpenSSL stacks to reach external
# MCP servers through the gateway's working TLS stack.


# CHG-0033: hop-by-hop + credential/identity headers that must NOT be forwarded
# from the caller to a third-party external MCP server. The caller authenticates
# to the GATEWAY (Authorization/Cookie/X-Api-Key); forwarding those verbatim would
# LEAK the caller's gateway credential (replayable against the gateway) to the
# upstream. Mirrors the sandbox-routed path (broker_send_rpc builds a clean header
# set + injects only the server's own OAuth token).
_EXT_HOP_BY_HOP_HEADERS = frozenset({"host", "content-length", "transfer-encoding"})
_EXT_CREDENTIAL_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key",
})
# CHG-0110: least-privilege egress context-minimization. Stripping only credentials
# still forwarded request-ROUTING / client-IDENTITY / topology headers to the untrusted
# third-party external server — leaking the client's real IP (x-forwarded-for / x-real-ip),
# the internal gateway host + proxy chain (x-forwarded-host / forwarded / via), and the
# internal URL + ORG/TENANT slug (referer, e.g. https://gw.internal/org/<slug>/chat). An
# MCP server needs NONE of these; drop them so a third party sees neither the caller's IP
# nor the internal topology/tenant. (``x-forwarded-*`` handled by prefix below.)
_EXT_ROUTING_HEADERS = frozenset({"x-real-ip", "forwarded", "via", "referer", "referrer"})

# CHG-0039: MCP request/response methods whose result is FINITE (bounded) and can
# carry content the 1.4 result scan must inspect. An SSE response for one of these
# is buffered + scanned like tools/call (the buffer is bounded by the httpx
# timeout). Genuinely-streaming methods (notifications/*, *subscribe) are NOT here
# — they can be long-lived, so they stream through unscanned (no result to scan).
_EXT_FINITE_RESULT_METHODS = frozenset({
    "tools/call", "tools/list", "resources/list", "resources/read",
    "prompts/list", "prompts/get",
    # CHG-0080: the MCP `initialize` result carries an `instructions` field that the
    # spec treats as model-facing guidance ("analogous to a system prompt" / a hint
    # added to the LLM context) plus serverInfo — a tool-poisoning / indirect-prompt-
    # injection + metadata-leak surface exactly like tool descriptions (CHG-0077). On
    # the transparent EXTERNAL proxy it was forwarded RAW (initialize wasn't in this
    # set), so a malicious upstream's initialize instructions/serverInfo reached the
    # model unscanned. The handshake result is finite → safe to buffer + scan.
    "initialize",
    # CHG-0118: two more finite, server-controlled, model/user-facing result methods
    # were forwarded RAW (unscanned) on the external proxy — the same leak/tool-poisoning
    # class as tools/list (CHG-0077) and initialize (CHG-0080):
    #  · completion/complete → result.completion.values[] (autocompletion strings the
    #    client shows to the user/model) — a secret/PII/beacon in a suggested value leaked.
    #  · resources/templates/list → result.resourceTemplates[].{name,description,uriTemplate}
    #    (server metadata, model-facing like resources/list, which IS scanned).
    # Both results are finite → safe to buffer + scan via the result floor.
    "completion/complete", "resources/templates/list",
})

# CHG-0041: methods whose params carry an ``arguments`` object that must be
# credential-scanned before egress to the external server (an accidental
# credential in tool args OR prompt-template args should not leak upstream).
# resources/read is deliberately EXCLUDED — its param is a URI, and blocking a
# legitimate ``https://user:token@host`` auth-in-URL would break authed reads.
_EXT_ARG_SCAN_METHODS = frozenset({"tools/call", "prompts/get"})


def _ext_proxy_forward_headers(inbound, *, oauth_token: str | None = None) -> dict:
    """Least-privilege outbound header set for the external MCP proxy forward.

    Drops hop-by-hop headers, the caller's credential/identity headers, and any
    gateway-internal ``X-Gateway-*`` header, so the caller's gateway credential
    never egresses to the external server. Injects the gateway's stored OAuth
    bearer for the upstream (if provided) as the sole ``Authorization``.
    """
    out: dict = {}
    for k, v in inbound.items():
        kl = str(k).lower()
        if kl in _EXT_HOP_BY_HOP_HEADERS or kl in _EXT_CREDENTIAL_HEADERS:
            continue
        if kl in _EXT_ROUTING_HEADERS:  # CHG-0110: client-IP / topology / tenant leak
            continue
        if kl.startswith("x-gateway-") or kl.startswith("x-forwarded-"):
            continue
        out[k] = v
    if oauth_token:
        out["Authorization"] = f"Bearer {oauth_token}"
    return out


# CHG-0061: content-types whose bodies are text and therefore scannable for
# secret/PII/infra egress. Binary types (image/*, audio/*, application/octet-stream,
# …) are NOT text-scanned — masking would corrupt them and they are not a text-leak
# vector. A missing content-type defaults to application/json upstream, so it is
# treated as text (scannable), which is the safe default.
_TEXT_CONTENT_TYPE_RE = re.compile(
    r"^\s*(?:text/|application/(?:json|xml|javascript|x-ndjson|graphql|[\w.+-]*\+(?:json|xml)))",
    re.IGNORECASE,
)


def _is_text_content_type(content_type: str) -> bool:
    """True if a response body of this content-type is text and thus scannable."""
    return bool(content_type and _TEXT_CONTENT_TYPE_RE.match(content_type))


@router.api_route(
    "/ext-proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Proxy to external MCP servers",
)
async def ext_mcp_proxy(path: str, request: Request):
    """Transparent proxy to external MCP servers.

    The path must start with the target hostname, e.g.:
    /v1/mcp/ext-proxy/mcp.context7.com/mcp

    Only domains in the allowlist are proxied.  Handles both JSON and
    SSE streaming responses (required for MCP Streamable HTTP protocol).
    """
    parts = path.split("/", 1)
    hostname = parts[0]
    remaining = parts[1] if len(parts) > 1 else ""

    if hostname not in _ALLOWED_MCP_DOMAINS:
        return JSONResponse(
            content={"error": f"Domain {hostname} not in MCP proxy allowlist"},
            status_code=403,
        )

    # CHG-0068: audit the ext-proxy ENFORCEMENT decisions (item 9 — the external
    # passthrough previously recorded NONE of its blocks/redactions, so external tool
    # usage + thwarted attacks [credential blocks, PII redaction, SSRF blocks] were
    # invisible in the MCPEvent audit trail, unlike every other MCP path). Best-effort /
    # fire-and-forget (no latency); no-op if unauthenticated (_record_gateway_event
    # returns early on an empty org). server_slug is ``ext:<host>`` since the external
    # host is not a registered org server.
    _ext_org = getattr(_get_auth_context(request), "org_slug", "") or ""
    _ext_t0 = time.time()

    async def _ext_audit(decision, reason, *, tool="", tags=None, findings=None):
        await _record_gateway_event(
            org_slug=_ext_org,
            server_slug=f"ext:{hostname}",
            tool_name=tool,
            decision=decision,
            reason=reason,
            latency_ms=int((time.time() - _ext_t0) * 1000),
            metadata={"transport": "ext_proxy", "enforced_at": "gateway", "host": hostname},
            compliance_tags=list(tags or []),
            scan_findings=list(findings or []),
        )

    # S12 (CHG-0032): per-org rate limit on the authenticated external MCP proxy —
    # parity with org_mcp_jsonrpc / org_mcp_tool_call. This route sits behind the
    # auth middleware (not in EXCLUDED_PATHS), so the caller's org context is
    # available; the handler is transport-level (no org-scoping) but the per-org
    # TPM/burst/RPM ceiling still applies to the CALLER's org. Returns a plain 429
    # (before any scan/forward work). Fail-open + no-op if unauthenticated.
    _ext_rl = await _mcp_org_rate_limit_raw(_get_auth_context(request))
    if _ext_rl is not None:
        return _ext_rl

    # CHG-0034: reject an oversized body before buffering it (DoS guard).
    if _mcp_body_too_large(request):
        await _ext_audit("block", "request_too_large")  # CHG-0095: audit the DoS-guard reject
        return _mcp_body_too_large_response()

    target_url = f"https://{hostname}/{remaining}"

    # CHG-0065: SSRF guard — parity with internal_tools_call / internal_discover_tools
    # ("finding mcp#1"). The allowlist above matches the hostname STRING only; it does
    # NOT catch an allowlisted domain that RESOLVES to an internal / loopback /
    # link-local / cloud-metadata address (DNS rebinding, DNS hijack, or a
    # misconfigured/future allowlist entry) — which would let a caller reach internal
    # services or the cloud-metadata endpoint (169.254.169.254 → credential theft).
    # Resolve + block before forwarding (fail-closed; MCP_ALLOW_INTERNAL_HOSTS overrides
    # for dev, same as the internal paths).
    _ssrf_ok, _ssrf_reason = is_safe_outbound_url(target_url)
    if not _ssrf_ok:
        LOG.warning(
            "ext_mcp_proxy.ssrf_blocked host=%s: %s", hostname, _ssrf_reason,
        )
        await _ext_audit("block", "ssrf_blocked")  # CHG-0068
        return JSONResponse(
            content={"error": f"Upstream URL rejected by SSRF guard: {_ssrf_reason}"},
            status_code=400,
        )

    # CHG-0033: strip the caller's gateway credentials before forwarding to the
    # third-party external server (least-privilege / no credential leak). Inject
    # the upstream's OWN stored OAuth token if the gateway holds one for this
    # domain — parity with the sandbox-routed path (broker_send_rpc).
    _ext_oauth = None
    try:
        from mcp_oauth_proxy import get_stored_token
        _ext_org = getattr(_get_auth_context(request), "org_slug", "") or ""
        if _ext_org:
            _ext_oauth = await get_stored_token(_ext_org, target_url)
    except Exception:  # noqa: BLE001 — no token store / not authed → no injection
        _ext_oauth = None
    headers = _ext_proxy_forward_headers(
        dict(request.headers.items()), oauth_token=_ext_oauth,
    )
    try:  # CHG-0063: cap the ACTUAL bytes (chunked/no-Content-Length DoS guard)
        body = await _mcp_read_body_capped(request)
    except _MCPBodyTooLarge:
        return _mcp_body_too_large_response()

    # ── Inbound credential hard-block on the transparent external proxy.
    # This path is transport-level (no org/server/tool scoping), so the
    # resolved scan_action defaults to "tag" (enabled_info=None) and the E12
    # credential force-block still fires: a credential in tools/call arguments
    # is blocked before it egresses to the external MCP server. Best-effort —
    # if the body is not a tools/call JSON-RPC, this is a no-op. ──
    _ext_tool_name = ""
    # CHG-0039: True when the response should be result-scanned (finite methods:
    # tools/call + resources/* + prompts/*), so the SSE branch buffers+scans them
    # too — not just tools/call. Notifications/subscriptions stay pass-through.
    _ext_scan_result = False
    if body:
        try:
            _ext_req = json.loads(body)
        except Exception:
            _ext_req = None
        if isinstance(_ext_req, dict):
            _ext_scan_result = str(_ext_req.get("method") or "") in _EXT_FINITE_RESULT_METHODS
        # CHG-0041: credential-scan the args of any method carrying params.arguments
        # (tools/call AND prompts/get) — not only tools/call. Same params shape.
        if isinstance(_ext_req, dict) and str(_ext_req.get("method") or "") in _EXT_ARG_SCAN_METHODS:
            _ext_params = _ext_req.get("params") or {}
            if isinstance(_ext_params, dict):
                _ext_tool_name = str(_ext_params.get("name") or "")
                _ext_args = _ext_params.get("arguments")
                if _ext_args is not None:
                    _scanned_args, _in_blocked, _in_tags, _in_findings, _scan_meta_in = (
                        await _scan_tool_args_block(
                            _ext_args,
                            tool_name=_ext_tool_name,
                            enabled_info=None,
                            org_slug="",
                            server_slug="",
                            actor=None,
                        )
                    )
                    if _in_blocked:
                        LOG.warning(
                            "ext_mcp_proxy.credential_blocked host=%s tool=%s tags=%s",
                            hostname, _ext_tool_name, _in_tags,
                        )
                        await _ext_audit(  # CHG-0068
                            "block", "credential_blocked_inbound",
                            tool=_ext_tool_name, tags=_in_tags, findings=_in_findings,
                        )
                        return JSONResponse(
                            content={
                                "jsonrpc": _ext_req.get("jsonrpc", "2.0"),
                                "id": _ext_req.get("id"),
                                "error": {
                                    "code": -32000,
                                    "message": (
                                        f"Tool '{_ext_tool_name or 'call'}' arguments matched "
                                        f"compliance tags: {', '.join(_in_tags) or 'credential/PII'}."
                                    ),
                                },
                            },
                            status_code=200,
                        )
                    # Forward any per-tier inbound redaction the orchestrator applied.
                    if _scanned_args is not _ext_args:
                        _ext_params["arguments"] = _scanned_args
                        body = json.dumps(_ext_req).encode()
                        # CHG-0109: audit the inbound ARG redaction (parity with the
                        # credential-block branch above; inbound twin of CHG-0081/0106).
                        await _ext_audit(
                            "redact", "pii_redacted_inbound",
                            tool=_ext_tool_name, tags=_in_tags, findings=_in_findings,
                        )

        # CHG-0119: credential/PII-scan the completion/complete CLIENT INPUT before it
        # egresses to the untrusted external server. Its input is params.argument.value
        # (the partial value the user is typing) + params.context.arguments — a DIFFERENT
        # shape than params.arguments, so the _EXT_ARG_SCAN_METHODS logic above misses it.
        # A credential/secret in that input would otherwise leak to a third-party server —
        # the input-side twin of CHG-0118 (which scans the completion RESULT). Block on a
        # credential; write back any inbound redaction.
        if isinstance(_ext_req, dict) and str(_ext_req.get("method") or "") == "completion/complete":
            _cparams = _ext_req.get("params") or {}
            if isinstance(_cparams, dict):
                _carg = _cparams.get("argument")
                _cctx = _cparams.get("context")
                _cin: dict = {}
                if isinstance(_carg, dict) and _carg.get("value") is not None:
                    _cin["argument_value"] = _carg.get("value")
                if isinstance(_cctx, dict) and isinstance(_cctx.get("arguments"), dict):
                    _cin["context_arguments"] = _cctx.get("arguments")
                if _cin:
                    _cs, _cblk, _ctags, _cfind, _cmeta = await _scan_tool_args_block(
                        _cin, tool_name="completion/complete", enabled_info=None,
                        org_slug="", server_slug="", actor=None,
                    )
                    if _cblk:
                        LOG.warning(
                            "ext_mcp_proxy.completion_input_blocked host=%s tags=%s",
                            hostname, _ctags,
                        )
                        await _ext_audit(
                            "block", "credential_blocked_inbound",
                            tool="completion/complete", tags=_ctags, findings=_cfind,
                        )
                        return JSONResponse(
                            content={
                                "jsonrpc": _ext_req.get("jsonrpc", "2.0"),
                                "id": _ext_req.get("id"),
                                "error": {
                                    "code": -32000,
                                    "message": (
                                        "completion input matched compliance tags: "
                                        f"{', '.join(_ctags) or 'credential/PII'}."
                                    ),
                                },
                            },
                            status_code=200,
                        )
                    if _cs is not _cin and isinstance(_cs, dict):
                        if "argument_value" in _cs and isinstance(_carg, dict):
                            _carg["value"] = _cs["argument_value"]
                        if "context_arguments" in _cs and isinstance(_cctx, dict):
                            _cctx["arguments"] = _cs["context_arguments"]
                        body = json.dumps(_ext_req).encode()
                        await _ext_audit(
                            "redact", "pii_redacted_inbound",
                            tool="completion/complete", tags=_ctags, findings=_cfind,
                        )

    client = httpx.AsyncClient(timeout=httpx.Timeout(max(_TIMEOUT, 120)), verify=True)
    try:
        resp = await client.send(
            client.build_request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body if body else None,
                params=dict(request.query_params),
            ),
            stream=True,
        )
        content_type = resp.headers.get("content-type", "application/json")

        # For SSE / streaming responses, stream through
        if "text/event-stream" in content_type:
            if _ext_scan_result:
                # Finite request/response SSE result (tools/call OR resources/* OR
                # prompts/*, CHG-0039) — BUFFER, scan/redact each data frame, and
                # re-emit as SSE (parity with the non-streaming JSON branch and
                # internal_tools_call). Closes the raw-egress leak
                # (BACKSTOP_FINDINGS G2 item 2 — previously only tools/call SSE was
                # scanned, so a resources/read result egressed raw). CHG-0064: cap the
                # buffered bytes (a timeout bounds TIME, not SIZE — an untrusted upstream
                # could stream a huge SSE fast and OOM the gateway).
                try:
                    sse_bytes = await _read_response_capped(resp)
                except _MCPBodyTooLarge:
                    await resp.aclose()
                    await client.aclose()
                    LOG.warning(
                        "ext_mcp_proxy.sse_response_too_large host=%s tool=%s",
                        hostname, _ext_tool_name or "?",
                    )
                    await _ext_audit(  # CHG-0095: audit the fail-closed SSE too-large withhold
                        "block", "response_too_large", tool=_ext_tool_name)
                    return _mcp_upstream_too_large_response()
                await resp.aclose()
                await client.aclose()
                _reframed, _block_info = await _scan_reframe_sse_tool_result(
                    sse_bytes.decode("utf-8", "replace"),
                    tool_name=_ext_tool_name,
                    org_slug="",
                    server_slug="",
                    enabled_info=None,
                    actor=None,
                )
                _sse_headers = {
                    k: v for k, v in resp.headers.items()
                    if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
                }
                if _block_info is not None:
                    LOG.warning(
                        "ext_mcp_proxy.sse_result_blocked host=%s tool=%s tags=%s",
                        hostname, _ext_tool_name or "?", _block_info.get("tags"),
                    )
                    await _ext_audit(  # CHG-0070: SSE result block (missed by CHG-0068)
                        "block", "pii_blocked_outbound",
                        tool=_ext_tool_name, tags=_block_info.get("tags"),
                    )
                    return JSONResponse(
                        content={
                            "jsonrpc": _block_info.get("jsonrpc", "2.0"),
                            "id": _block_info.get("id"),
                            "error": {
                                "code": -32000,
                                "message": (
                                    f"Response from '{_ext_tool_name or 'call'}' matched compliance "
                                    f"tags: {', '.join(_block_info.get('tags') or []) or 'PII'}."
                                ),
                            },
                        },
                        status_code=200,
                    )
                if _ext_tool_name:  # CHG-0070: successful tool-call result (usage audit)
                    await _ext_audit("allow", "ok", tool=_ext_tool_name)
                from starlette.responses import Response as _SSEResponse
                return _SSEResponse(
                    content=_reframed.encode("utf-8"),
                    status_code=resp.status_code,
                    media_type=content_type,
                    headers=_sse_headers,
                )

            # Non-finite SSE (notifications / *subscribe / long-lived streams). CHG-0098:
            # previously streamed through RAW (unscanned) because buffering the whole
            # open stream could hang / OOM. But an untrusted upstream can push sensitive
            # data in a server notification (notifications/message params), so the raw
            # passthrough was a real egress leak. Scan PER EVENT instead: buffer only up
            # to one SSE event (bounded by _MCP_SSE_EVENT_MAX_BYTES — memory-safe, no
            # whole-stream buffering), reassemble + scan each event's JSON-RPC message
            # (result/error AND notification params) via the result floor, and re-emit.
            # An event exceeding the cap without a boundary is withheld (fail-closed).
            await _ext_audit("allow", "sse_stream_scanned", tool=_ext_tool_name)

            async def stream_gen():
                buf = ""
                total_bytes = 0   # CHG-0117: total-stream resource-bomb counters
                event_count = 0
                try:
                    async for chunk in resp.aiter_bytes():
                        total_bytes += len(chunk)
                        buf += chunk.decode("utf-8", "replace").replace("\r\n", "\n")
                        while "\n\n" in buf:
                            raw_event, buf = buf.split("\n\n", 1)
                            event_count += 1
                            reframed, block = await _scan_reframe_sse_tool_result(
                                raw_event, tool_name=_ext_tool_name, scan_notifications=True,
                            )
                            if block is not None:
                                LOG.warning(
                                    "ext_mcp_proxy.sse_stream_event_withheld host=%s tags=%s",
                                    hostname, block.get("tags"),
                                )
                                await _ext_audit(
                                    "block", "sse_stream_event_withheld",
                                    tool=_ext_tool_name, tags=block.get("tags"),
                                )
                                yield b": [event withheld: matched compliance policy]\n\n"
                            else:
                                yield (reframed + "\n\n").encode("utf-8")
                        if len(buf) > _MCP_SSE_EVENT_MAX_BYTES:
                            LOG.warning(
                                "ext_mcp_proxy.sse_stream_event_too_large host=%s — "
                                "withholding oversized unterminated event", hostname,
                            )
                            await _ext_audit(
                                "block", "sse_stream_event_too_large", tool=_ext_tool_name)
                            yield b": [event withheld: oversized]\n\n"
                            buf = ""
                        # CHG-0117: contain a runaway/infinite stream (total bytes or events).
                        # A slow-but-steady infinite feed of small events would otherwise hold
                        # the connection + burn CPU forever (httpx per-read timeout won't stop
                        # it). Close the stream fail-closed once either total bound is crossed.
                        if total_bytes > _MCP_SSE_STREAM_MAX_BYTES or event_count > _MCP_SSE_STREAM_MAX_EVENTS:
                            LOG.warning(
                                "ext_mcp_proxy.sse_stream_limit host=%s bytes=%d events=%d (CLOSING)",
                                hostname, total_bytes, event_count,
                            )
                            await _ext_audit(
                                "block", "sse_stream_limit_exceeded", tool=_ext_tool_name)
                            yield b": [stream closed: resource limit]\n\n"
                            return
                    # Flush a trailing partial event (no terminating blank line).
                    if buf.strip():
                        reframed, block = await _scan_reframe_sse_tool_result(
                            buf, tool_name=_ext_tool_name, scan_notifications=True,
                        )
                        if block is None:
                            yield reframed.encode("utf-8")
                        else:
                            yield b": [event withheld: matched compliance policy]\n"
                finally:
                    await resp.aclose()
                    await client.aclose()

            resp_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
            }
            return StreamingResponse(
                stream_gen(),
                status_code=resp.status_code,
                media_type=content_type,
                headers=resp_headers,
            )

        # For normal JSON / text / binary responses, read fully and close.
        # CHG-0064: cap the buffered bytes — a timeout bounds TIME, not SIZE, so an
        # untrusted upstream could stream a huge fast body and OOM the (shared) gateway.
        try:
            body_bytes = await _read_response_capped(resp)
        except _MCPBodyTooLarge:
            await resp.aclose()
            await client.aclose()
            LOG.warning("ext_mcp_proxy.response_too_large host=%s", hostname)
            await _ext_audit(  # CHG-0095: audit the fail-closed too-large withhold
                "block", "response_too_large", tool=_ext_tool_name)
            return _mcp_upstream_too_large_response()
        await resp.aclose()
        await client.aclose()

        # Forward response headers relevant to MCP session tracking
        resp_headers = {}
        for h in ("mcp-session-id", "x-request-id"):
            if h in resp.headers:
                resp_headers[h] = resp.headers[h]

        try:
            data = resp.json()
        except Exception:
            from starlette.responses import Response
            # CHG-0061: a NON-JSON body (an HTML error page, a plain-text error, an
            # XML fault) can STILL carry a secret/PII/infra string from an untrusted
            # external server (parity with the JSON error-body scan below and the SSE
            # error-frame scan). Scan text-like bodies via the fail-closed floor before
            # forwarding; binary bodies (images/octet-stream) pass through unscanned
            # (text-masking would corrupt them and they are not a text-leak vector).
            if _is_text_content_type(content_type):
                _raw_text = body_bytes.decode("utf-8", errors="replace")
                (
                    _txt_scanned, _txt_blocked, _txt_tags, _tf, _tm
                ) = await _scan_tool_result_floor(
                    _raw_text, tool_name=_ext_tool_name, enabled_info=None,
                    org_slug="", server_slug="", actor=None,
                )
                if _txt_blocked:
                    LOG.warning(
                        "ext_mcp_proxy.text_body_withheld host=%s tool=%s tags=%s — "
                        "non-JSON body could not be safely inspected",
                        hostname, _ext_tool_name or "?", _txt_tags,
                    )
                    await _ext_audit(  # CHG-0095: audit the non-JSON body withhold
                        "block", "text_body_withheld",
                        tool=_ext_tool_name, tags=_txt_tags, findings=_tf)
                    return Response(
                        content="Response withheld: body could not be safely inspected.",
                        status_code=resp.status_code, media_type="text/plain",
                        headers=resp_headers,
                    )
                if _txt_scanned is not _raw_text:  # CHG-0095: audit the non-JSON body redaction
                    await _ext_audit(
                        "redact", "text_body_redacted",
                        tool=_ext_tool_name, tags=_txt_tags, findings=_tf)
                _out_text = _txt_scanned if isinstance(_txt_scanned, str) else _raw_text
                return Response(
                    content=_out_text.encode("utf-8"),
                    status_code=resp.status_code,
                    media_type=content_type,
                    headers=resp_headers,
                )
            return Response(
                content=body_bytes,
                status_code=resp.status_code,
                media_type=content_type,
                headers=resp_headers,
            )

        # ── Outbound result scan + redaction floor on NON-streaming JSON
        # responses (parity with org_mcp_jsonrpc). Scans result.content; on an
        # output block returns a JSON-RPC error, otherwise swaps masked content
        # in. enabled_info=None → action defaults to "tag", so the floor applies
        # (never "monitor"). Best-effort — only when a tools/call result is
        # present; never raises (the scan helper is fail-safe). ──
        # Scan the ENTIRE ``result`` (whatever shape) — not just dict
        # ``result.content``. MCP tool output can also live in ``structuredContent``
        # or be a plain string, and older code that only scanned dict
        # ``result.content`` let those shapes egress unscanned (BACKSTOP_FINDINGS
        # G2 item 2). ``_scan_tool_result_floor`` recursively walks any payload
        # (dict/list/str) and fails CLOSED on scan error; parity with the org path.
        # CHG-0061: scan result/error content on ANY status (a non-200 JSON error body
        # can carry a secret/PII/infra string just as a 200 one can — the old
        # `status_code == 200` gate forwarded non-200 bodies raw).
        if isinstance(data, dict) and data.get("result") is not None:
            _ext_result = data["result"]
            (
                _scanned_content, _out_blocked, _out_tags, _out_findings, _scan_meta_out
            ) = await _scan_tool_result_floor(
                _ext_result,
                tool_name=_ext_tool_name,
                enabled_info=None,
                org_slug="",
                server_slug="",
                actor=None,
            )
            if _out_blocked:
                LOG.warning(
                    "ext_mcp_proxy.result_blocked host=%s tool=%s tags=%s",
                    hostname, _ext_tool_name or "?", _out_tags,
                )
                await _ext_audit(  # CHG-0068
                    "block", "pii_blocked_outbound",
                    tool=_ext_tool_name, tags=_out_tags, findings=_out_findings,
                )
                return JSONResponse(
                    content={
                        "jsonrpc": data.get("jsonrpc", "2.0"),
                        "id": data.get("id"),
                        "error": {
                            "code": -32000,
                            "message": (
                                f"Response from '{_ext_tool_name or 'call'}' matched "
                                f"compliance tags: {', '.join(_out_tags) or 'PII'}."
                            ),
                        },
                    },
                    status_code=200,
                    headers=resp_headers,
                )
            if _scanned_content is not _ext_result:
                data["result"] = _scanned_content
                await _ext_audit(  # CHG-0068: result PII/secret masked before egress
                    "redact", "pii_redacted_outbound",
                    tool=_ext_tool_name, tags=_out_tags, findings=_out_findings,
                )
            elif _ext_tool_name:  # CHG-0070: clean successful tool-call result (usage audit)
                await _ext_audit("allow", "ok", tool=_ext_tool_name)

        # CHG-0043 (was CHG-0040; renumbered — id collided w/ P4.13 ws:// change):
        # a JSON-RPC error response (no result) can STILL leak a secret in
        # its message/data from an untrusted external server (e.g. a connection
        # string in "connect failed: postgres://user:pass@host"). Scan + mask it
        # (redact-only — it is already an error); fail CLOSED (withhold) on a scan
        # error so un-inspected error content never egresses raw.
        elif isinstance(data, dict) and data.get("error") is not None:
            _ext_err = data["error"]
            (
                _scanned_err, _err_blocked, _err_tags, _err_findings, _err_meta
            ) = await _scan_tool_result_floor(
                _ext_err,
                tool_name=_ext_tool_name,
                enabled_info=None,
                org_slug="",
                server_slug="",
                actor=None,
            )
            if _err_blocked:
                LOG.warning(
                    "ext_mcp_proxy.error_content_withheld host=%s — error content "
                    "could not be safely inspected",
                    hostname,
                )
                await _ext_audit(  # CHG-0095: audit the error-content withhold
                    "block", "error_content_withheld",
                    tool=_ext_tool_name, tags=_err_tags, findings=_err_findings)
                return JSONResponse(
                    content={
                        "jsonrpc": data.get("jsonrpc", "2.0"),
                        "id": data.get("id"),
                        "error": {
                            "code": -32000,
                            "message": "Response withheld: error content could not be safely inspected.",
                        },
                    },
                    status_code=200,
                    headers=resp_headers,
                )
            if _scanned_err is not _ext_err:
                data["error"] = _scanned_err
                await _ext_audit(  # CHG-0095: audit the error-content redaction
                    "redact", "error_content_redacted",
                    tool=_ext_tool_name, tags=_err_tags, findings=_err_findings)

        elif resp.status_code != 200 and data is not None:
            # CHG-0061: a non-200 body WITHOUT a JSON-RPC result/error (e.g.
            # ``{"detail": "user john@example.com not found on db.internal"}``, a bare
            # list, or a scalar) still egressed raw. Scan the whole body via the
            # fail-closed floor. (A 200 body without result/error is a benign
            # session/notification shape and is left untouched to preserve the
            # established path's behaviour.)
            (
                _whole_scanned, _whole_blocked, _whole_tags, _wf, _wm
            ) = await _scan_tool_result_floor(
                data, tool_name=_ext_tool_name, enabled_info=None,
                org_slug="", server_slug="", actor=None,
            )
            if _whole_blocked:
                LOG.warning(
                    "ext_mcp_proxy.nonok_body_withheld host=%s status=%s tags=%s — "
                    "non-200 body could not be safely inspected",
                    hostname, resp.status_code, _whole_tags,
                )
                await _ext_audit(  # CHG-0095: audit the non-200 body withhold
                    "block", "nonok_body_withheld",
                    tool=_ext_tool_name, tags=_whole_tags, findings=_wf)
                return JSONResponse(
                    content={"error": "Response withheld: body could not be safely inspected."},
                    status_code=resp.status_code, headers=resp_headers,
                )
            if _whole_scanned is not data:
                data = _whole_scanned
                await _ext_audit(  # CHG-0095: audit the non-200 body redaction
                    "redact", "nonok_body_redacted",
                    tool=_ext_tool_name, tags=_whole_tags, findings=_wf)

        # CLEANUP-04: an upstream auth failure surfaces as a clean re-authentication
        # prompt — NOT the raw (even if scanned) upstream 401/403 body, which can hint
        # at token/endpoint internals. The action ("re-authorize") is in the message.
        if resp.status_code in (401, 403):
            clean = await sanitize_mcp_error(status=resp.status_code, server_slug=f"ext:{hostname}")
            return JSONResponse(content=clean, status_code=resp.status_code, headers=resp_headers)
        return JSONResponse(content=data, status_code=resp.status_code, headers=resp_headers)
    except httpx.RequestError as exc:
        await client.aclose()
        # CLEANUP-04: classify the transport failure into a clean, non-revealing
        # message. NEVER leak the internal hostname or the raw exception (both can
        # expose the operator-configured host / connection internals). The raw cause
        # (with the hostname) goes ONLY to the log + dev diagnostic keyed by ref.
        LOG.error("External MCP proxy error [%s] → %s: %s", type(exc).__name__, target_url, exc)
        clean = await sanitize_mcp_error(exc=exc, server_slug=f"ext:{hostname}")
        return JSONResponse(content=clean, status_code=502)


# ── Internal MCP Tool Discovery ──────────────────────────────────────
# Called by the backend during tool sync for every transport (stdio,
# websocket, streamable-http, sse). Replaces the previous ContextForge-based
# discovery path that was removed in DECISION-D Phase 0.
# Auth: validated via X-Gateway-Internal-Key (same shared secret as backend).


async def _scan_internal_tools_list(
    payload,
    *,
    jsonrpc,
    msg_id,
    enabled_info,
    org_slug: str,
    server_slug: str,
    transport: str,
    request_id: str = "",  # CHG-0120: correlation id for the discovery audit trail
):
    """CHG-0108: scan a DISCOVERED tools/list payload before returning it to the
    backend tool-sync / chat pipeline.

    The internal discovery route (``internal_discover_tools``) returned upstream tool
    METADATA (descriptions / names / inputSchema) RAW — a parity gap vs the org path
    (``org_mcp_jsonrpc`` tools/list, CHG-0077/0092), the REST list (CHG-0079), and the
    external proxy, all of which scan tool metadata. Tool descriptions come LIVE from an
    UNTRUSTED upstream MCP server and are synced into the catalog + shown to the model:
    a classic tool-poisoning / metadata-leak surface. A secret / PII / internal-IP (or a
    CHG-0076 encoded-exfil payload) in a description therefore reached the backend/LLM
    unredacted on the discovery path.

    Tools-shaped result → reuse ``_scanned_tools_list_response`` (masks a maskable leak;
    blocks fail-closed on poisoned/unmaskable metadata; audits). A bare ERROR ENVELOPE
    (a tools/list auth-failure error can echo a token/URL) → scan the whole payload via
    the result floor (CHG-0092 parity). Descriptions are not actor-scoped → ``actor=None``
    (matches the org tools/list scan)."""
    if not isinstance(payload, dict):
        return JSONResponse(content=payload, status_code=200)
    if isinstance(payload.get("result"), dict):
        return await _scanned_tools_list_response(
            payload, jsonrpc=jsonrpc, msg_id=msg_id, enabled_info=enabled_info,
            org_slug=org_slug, server_slug=server_slug, actor=None, request_id=request_id,
        )
    # Non-tools-shaped (bare error envelope / malformed result): scan the whole payload
    # so a secret/PII/internal-IP in an error message is masked or fail-closed blocked.
    _s, _blk, _tags, _find, _meta = await _scan_tool_result_floor(
        payload, tool_name="tools/list", enabled_info=enabled_info,
        org_slug=org_slug, server_slug=server_slug, actor=None,
    )
    if _blk or (_s is not payload):
        await _record_gateway_event(
            org_slug=org_slug, server_slug=server_slug, tool_name="tools/list",
            decision="block" if _blk else "redact", reason="tools_list_error_scan",
            request_id=request_id,
            metadata={"transport": transport, "enforced_at": "gateway_internal_discover",
                      "scan_pipeline": "two_tier"},
            compliance_tags=list(_tags), scan_findings=_find,
        )
    if _blk:
        return JSONResponse(
            content={
                "jsonrpc": jsonrpc, "id": msg_id,
                "error": {
                    "code": -32000,
                    "message": "tools/list withheld: server response matched sensitive content",
                },
            },
            status_code=200,
        )
    if _s is not payload:
        return JSONResponse(content=_s, status_code=200)
    return JSONResponse(content=payload, status_code=200)


@router.post(
    "/internal/discover-tools",
    summary="Internal tool discovery for backend sync",
)
async def internal_discover_tools(request: Request):
    """Discover tools from an MCP server for backend tool sync.

    For stdio/websocket: uses the gateway adapter to spawn process / connect
    and send tools/list JSON-RPC.
    For streamable-http/sse: sends JSON-RPC directly to the upstream server URL.
    """
    internal_key = (request.headers.get("X-Gateway-Internal-Key") or "").strip()
    if not _valid_internal_key(internal_key):
        return JSONResponse(content={"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"error": "Invalid JSON body"}, status_code=400)

    org_slug = (body.get("org_slug") or "").strip()
    server_slug = (body.get("server_slug") or "").strip()
    if not org_slug or not server_slug:
        return JSONResponse(
            content={"error": "org_slug and server_slug are required"},
            status_code=400,
        )

    config = await _get_server_config(org_slug, server_slug)
    config = _apply_fresh_config_overrides(config, body, org_slug, server_slug)
    if not config:
        return JSONResponse(content={"error": "Server not found"}, status_code=404)

    transport = config.get("transport", "streamable-http")
    # CHG-0120: propagate the X-Request-ID correlation id into the discovery audit trail
    # + the broker (adapter) hop, so a tool-sync is traceable gateway → broker → sandbox.
    _disc_req_id = _mcp_request_correlation_id(request, 1)
    LOG.info(
        "Internal discover-tools: org=%s server=%s transport=%s request_id=%s",
        org_slug, server_slug, transport, _disc_req_id or "-",
    )

    # CHG-0108: metadata scan needs the server's scan action (respects a monitor override).
    enabled_info = await _get_enabled_tools(org_slug, server_slug)

    tools_list_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }

    if _is_sandbox_routed(transport):
        # CHG-0108: scan the sandbox (stdio/ws) upstream tool metadata before returning
        # it to the backend tool-sync — was returned RAW (tool-poisoning / metadata-leak).
        _adapter_resp = await _adapter_forward(
            transport, config, org_slug, server_slug,
            tools_list_body, "2.0", 1, correlation_id=_disc_req_id,  # CHG-0120
        )
        try:
            _payload = json.loads(_adapter_resp.body.decode("utf-8")) if _adapter_resp.body else None
        except Exception:
            _payload = None
        if not isinstance(_payload, dict):
            return _adapter_resp
        return await _scan_internal_tools_list(
            _payload, jsonrpc="2.0", msg_id=1, enabled_info=enabled_info,
            org_slug=org_slug, server_slug=server_slug, transport=transport,
            request_id=_disc_req_id,
        )

    # For streamable-http / sse: call the upstream MCP server directly
    upstream_url = config.get("url", "")
    if not upstream_url:
        return JSONResponse(
            content={"error": "No upstream URL configured for server"},
            status_code=400,
        )

    # SSRF guard (finding mcp#1): the upstream URL is operator-supplied. Reject
    # internal / loopback / link-local / cloud-metadata targets before fetching.
    _ok, _reason = is_safe_outbound_url(upstream_url)
    if not _ok:
        LOG.warning(
            "Blocked discover-tools to unsafe upstream URL (org=%s server=%s): %s",
            org_slug, server_slug, _reason,
        )
        return JSONResponse(
            content={"error": f"Upstream URL rejected by SSRF guard: {_reason}"},
            status_code=400,
        )

    # Build auth headers from the request body (backend passes auth info)
    upstream_auth_headers = {}
    req_auth_type = body.get("auth_type", "none")
    if req_auth_type == "bearer" and body.get("auth_token"):
        upstream_auth_headers["Authorization"] = f"Bearer {body['auth_token']}"
    elif req_auth_type == "basic" and body.get("auth_username"):
        import base64 as b64
        cred = b64.b64encode(
            f"{body['auth_username']}:{body.get('auth_password', '')}".encode()
        ).decode()
        upstream_auth_headers["Authorization"] = f"Basic {cred}"
    elif req_auth_type == "authheaders" and body.get("auth_header_key"):
        upstream_auth_headers[body["auth_header_key"]] = body.get("auth_header_value", "")

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **upstream_auth_headers,
            }
            # Step 1: MCP initialize
            init_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ZeroShield Gateway", "version": "1.0.0"},
                },
            }
            init_resp = await client.post(upstream_url, json=init_body, headers=headers)
            mcp_session = init_resp.headers.get("mcp-session-id")
            if mcp_session:
                headers["Mcp-Session-Id"] = mcp_session

            # Step 2: notifications/initialized
            await client.post(
                upstream_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )

            # Step 3: tools/list
            tools_resp = await client.post(upstream_url, json=tools_list_body, headers=headers)
            content_type = tools_resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                for line in tools_resp.text.split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str:
                            try:
                                _payload = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            # CHG-0108: scan upstream tool metadata before returning.
                            return await _scan_internal_tools_list(
                                _payload, jsonrpc="2.0", msg_id=1, enabled_info=enabled_info,
                                org_slug=org_slug, server_slug=server_slug, transport=transport,
                                request_id=_disc_req_id,  # CHG-0120
                            )
                return JSONResponse(
                    content={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
                    status_code=200,
                )
            else:
                # CLEANUP-02: an upstream that returns a non-2xx OR a non-JSON body
                # (e.g. a 405 HTML error page) must be classified by STATUS and
                # returned CLEAN — never dump the raw body/exc. Guard tools_resp.json().
                if tools_resp.status_code >= 400:
                    clean = await sanitize_mcp_error(
                        status=tools_resp.status_code,
                        org_slug=org_slug, server_slug=server_slug)
                    return _discovery_error_response(clean)
                try:
                    _tools_payload = tools_resp.json()
                except Exception:
                    clean = await sanitize_mcp_error(
                        status=tools_resp.status_code,
                        raw="upstream returned a non-JSON response (likely an HTML error page)",
                        org_slug=org_slug, server_slug=server_slug)
                    return _discovery_error_response(clean)
                # CHG-0108: scan upstream tool metadata before returning.
                return await _scan_internal_tools_list(
                    _tools_payload, jsonrpc="2.0", msg_id=1, enabled_info=enabled_info,
                    org_slug=org_slug, server_slug=server_slug, transport=transport,
                    request_id=_disc_req_id,  # CHG-0120
                )
    except Exception as exc:
        # CLEANUP-02: classify the failure (DNS / conn-refused / timeout / etc.) into a
        # clean, non-revealing message — the raw exc (which can carry an internal
        # hostname or an upstream HTML snippet) goes ONLY to the log + dev diagnostic.
        LOG.error("Internal discover-tools upstream error [%s]: %s", type(exc).__name__, exc)
        clean = await sanitize_mcp_error(exc=exc, org_slug=org_slug, server_slug=server_slug)
        return _discovery_error_response(clean)


@router.post(
    "/internal/tools-call",
    summary="Internal tool execution for backend requests",
)
async def internal_tools_call(request: Request):
    """Execute a tool on an MCP server via the appropriate gateway transport path."""
    internal_key = (request.headers.get("X-Gateway-Internal-Key") or "").strip()
    if not _valid_internal_key(internal_key):
        return JSONResponse(content={"error": "Unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"error": "Invalid JSON body"}, status_code=400)

    org_slug = (body.get("org_slug") or "").strip()
    server_slug = (body.get("server_slug") or "").strip()
    tool_name = (body.get("tool_name") or "").strip()
    arguments = body.get("arguments") or {}
    if not org_slug or not server_slug or not tool_name:
        return JSONResponse(
            content={"error": "org_slug, server_slug, and tool_name are required"},
            status_code=400,
        )

    config = await _get_server_config(org_slug, server_slug)
    config = _apply_fresh_config_overrides(config, body, org_slug, server_slug)
    if not config:
        return JSONResponse(content={"error": "Server not found"}, status_code=404)

    transport = config.get("transport", "streamable-http")
    # CHG-0120: propagate the X-Request-ID correlation id into every audit event + the
    # broker (adapter) hop, so a chat-pipeline tool call is traceable gateway → broker →
    # sandbox (the internal route previously recorded events with NO request_id — the trace
    # ended at the internal boundary, unlike org_mcp_jsonrpc / the bare REST route CHG-0050).
    _int_req_id = _mcp_request_correlation_id(request, 1)
    LOG.info(
        "Internal tools-call: org=%s server=%s transport=%s tool=%s request_id=%s",
        org_slug, server_slug, transport, tool_name, _int_req_id or "-",
    )

    enabled_info = await _get_enabled_tools(org_slug, server_slug)
    if _is_tool_disabled(tool_name, enabled_info):
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": f"Tool '{tool_name}' is disabled for this server."},
            },
            status_code=200,
        )

    # ── Defense-in-depth scan parity (this internal route previously forwarded
    # arguments RAW). Run the SAME inbound arg scan + credential hard-block as
    # org_mcp_jsonrpc before forwarding, even though the caller is internal. ──
    _internal_call_t0 = time.time()
    _scanned_args, _in_blocked, _in_tags, _in_findings, _scan_meta_in = await _scan_tool_args_block(
        arguments,
        tool_name=tool_name,
        enabled_info=enabled_info,
        org_slug=org_slug,
        server_slug=server_slug,
        actor=None,
    )
    if _in_blocked:
        await _record_gateway_event(
            org_slug=org_slug,
            server_slug=server_slug,
            tool_name=tool_name,
            decision="block",
            reason="pii_blocked_inbound",
            request_id=_int_req_id,  # CHG-0120
            latency_ms=int((time.time() - _internal_call_t0) * 1000),
            metadata={"transport": "internal", "enforced_at": "gateway", **_scan_meta_in},
            compliance_tags=list(_in_tags),
            scan_findings=list(_in_findings),
        )
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32000,
                    "message": (
                        f"Tool '{tool_name}' arguments matched compliance tags: "
                        f"{', '.join(_in_tags) or 'credential/PII'}."
                    ),
                },
            },
            status_code=200,
        )
    # Forward any per-tier inbound redaction the orchestrator applied.
    if _scanned_args is not arguments:
        arguments = _scanned_args
        # CHG-0109: audit the inbound ARG redaction (PII/IP masked in args, not blocked).
        # Previously swapped silently → a compliance-relevant input redaction (the gateway
        # masked a user's PII before it egressed to the MCP server) was INVISIBLE to the
        # audit/SIEM trail, unlike the block branch above AND the main org_mcp_jsonrpc path
        # (which folds inbound redaction into its per-call _was_redacted event). Inbound
        # twin of the result-side redact audits (CHG-0081 REST / CHG-0106 internal).
        await _record_gateway_event(
            org_slug=org_slug,
            server_slug=server_slug,
            tool_name=tool_name,
            decision="redact",
            reason="pii_redacted_inbound",
            request_id=_int_req_id,  # CHG-0120
            latency_ms=int((time.time() - _internal_call_t0) * 1000),
            metadata={"transport": "internal", "enforced_at": "gateway", **_scan_meta_in},
            compliance_tags=list(_in_tags),
            scan_findings=list(_in_findings),
        )

    call_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    if _is_sandbox_routed(transport):
        # CHG-0105: scan the sandbox (stdio/websocket) tool RESULT before returning. This
        # path previously returned the raw adapter response UNSCANNED, so a secret / PII /
        # exfil-beacon in a stdio/ws tool result egressed to the chat pipeline / LLM
        # unredacted — while the streamable-http internal path (below) AND org_mcp_jsonrpc
        # both scan it. Error-envelope aware (CHG-0091): scans the whole result OR a bare
        # error envelope via the shared floor (render-leak neutralization + split-check +
        # block-count cap all included); masks/blocks + audits, parity with the org path.
        _adapter_resp = await _adapter_forward(
            transport, config, org_slug, server_slug, call_body, "2.0", 1,
            correlation_id=_int_req_id,  # CHG-0120
        )
        try:
            _adapter_obj = json.loads(_adapter_resp.body.decode("utf-8")) if _adapter_resp.body else None
        except Exception:
            _adapter_obj = None
        if not isinstance(_adapter_obj, dict):
            return _adapter_resp
        _scan_target = _adapter_obj.get("result") if "result" in _adapter_obj else _adapter_obj
        _sc, _blk, _tg, _fn, _mt = await _scan_tool_result_floor(
            _scan_target, tool_name=tool_name, enabled_info=enabled_info,
            org_slug=org_slug, server_slug=server_slug, actor=None,
        )
        if _blk:
            await _record_gateway_event(
                org_slug=org_slug, server_slug=server_slug, tool_name=tool_name,
                decision="block", reason="pii_blocked_outbound",
                request_id=_int_req_id,  # CHG-0120
                latency_ms=int((time.time() - _internal_call_t0) * 1000),
                metadata={"transport": "internal_sandbox", "enforced_at": "gateway", **_mt},
                compliance_tags=list(_tg), scan_findings=list(_fn),
            )
            return JSONResponse(
                content={
                    "jsonrpc": "2.0", "id": _adapter_obj.get("id", 1),
                    "error": {
                        "code": -32000,
                        "message": (
                            f"Response from '{tool_name}' matched compliance tags: "
                            f"{', '.join(_tg) or 'PII'}."
                        ),
                    },
                },
                status_code=200,
            )
        if _sc is not _scan_target:
            if "result" in _adapter_obj:
                _adapter_obj["result"] = _sc
            elif isinstance(_sc, dict):
                _adapter_obj = _sc
            await _record_gateway_event(
                org_slug=org_slug, server_slug=server_slug, tool_name=tool_name,
                decision="redact", reason="pii_redacted_outbound",
                request_id=_int_req_id,  # CHG-0120
                latency_ms=int((time.time() - _internal_call_t0) * 1000),
                metadata={"transport": "internal_sandbox", "enforced_at": "gateway", **_mt},
                compliance_tags=list(_tg), scan_findings=list(_fn),
            )
        return JSONResponse(content=_adapter_obj, status_code=200)

    upstream_url = config.get("url", "")
    if not upstream_url:
        return JSONResponse(
            content={"error": "No upstream URL configured for server"},
            status_code=400,
        )

    # SSRF guard (finding mcp#1): the upstream URL is operator-supplied. Reject
    # internal / loopback / link-local / cloud-metadata targets before fetching.
    _ok, _reason = is_safe_outbound_url(upstream_url)
    if not _ok:
        LOG.warning(
            "Blocked tools-call to unsafe upstream URL (org=%s server=%s): %s",
            org_slug, server_slug, _reason,
        )
        return JSONResponse(
            content={"error": f"Upstream URL rejected by SSRF guard: {_reason}"},
            status_code=400,
        )

    upstream_auth_headers = {}
    req_auth_type = body.get("auth_type", "none")
    if req_auth_type == "bearer" and body.get("auth_token"):
        upstream_auth_headers["Authorization"] = f"Bearer {body['auth_token']}"
    elif req_auth_type == "basic" and body.get("auth_username"):
        import base64 as b64
        cred = b64.b64encode(
            f"{body['auth_username']}:{body.get('auth_password', '')}".encode()
        ).decode()
        upstream_auth_headers["Authorization"] = f"Basic {cred}"
    elif req_auth_type == "authheaders" and body.get("auth_header_key"):
        upstream_auth_headers[body["auth_header_key"]] = body.get("auth_header_value", "")

    async def _scan_internal_result(resp_obj):
        """Outbound result scan + redaction floor on an upstream JSON-RPC reply.

        Mirrors org_mcp_jsonrpc's outbound enforcement for this internal route.
        CHG-0106: error-envelope aware WHOLE-result scan (parity with the sandbox
        branch CHG-0105 + org path CHG-0091). Scans the whole ``result`` when
        present — so ``content`` AND ``structuredContent`` AND a bare string/list
        result are ALL covered — else the bare error envelope. Previously this
        legacy ``MCP_HTTP_VIA_SANDBOX=0`` direct-httpx path scanned only
        ``result.content``, so a secret / PII / internal-IP in an upstream ERROR
        FRAME (``error.message``) or a ``structuredContent``-only result egressed
        RAW to the chat pipeline / LLM — a weaker leak posture than the default
        sandbox path. On an output block returns a JSON-RPC error; otherwise swaps
        in the masked payload (and AUDITS the redact, closing a prior audit
        omission). Never raises (fail-safe inside the scan helper) so the response
        still flows.
        """
        if not isinstance(resp_obj, dict):
            return JSONResponse(content=resp_obj, status_code=200)
        scan_target = resp_obj.get("result") if "result" in resp_obj else resp_obj
        (
            scanned, out_blocked, out_tags, out_findings, scan_meta_out
        ) = await _scan_tool_result_floor(
            scan_target,
            tool_name=tool_name,
            enabled_info=enabled_info,
            org_slug=org_slug,
            server_slug=server_slug,
            actor=None,
        )
        if out_blocked:
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision="block",
                reason="pii_blocked_outbound",
                request_id=_int_req_id,  # CHG-0120
                latency_ms=int((time.time() - _internal_call_t0) * 1000),
                metadata={"transport": "internal", "enforced_at": "gateway", **scan_meta_out},
                compliance_tags=list(out_tags),
                scan_findings=list(out_findings),
            )
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": resp_obj.get("id", 1),
                    "error": {
                        "code": -32000,
                        "message": (
                            f"Response from '{tool_name}' matched compliance tags: "
                            f"{', '.join(out_tags) or 'PII'}."
                        ),
                    },
                },
                status_code=200,
            )
        if scanned is not scan_target:
            if "result" in resp_obj:
                resp_obj["result"] = scanned
            elif isinstance(scanned, dict):
                resp_obj = scanned
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision="redact",
                reason="pii_redacted_outbound",
                request_id=_int_req_id,  # CHG-0120
                latency_ms=int((time.time() - _internal_call_t0) * 1000),
                metadata={"transport": "internal", "enforced_at": "gateway", **scan_meta_out},
                compliance_tags=list(out_tags),
                scan_findings=list(out_findings),
            )
        return JSONResponse(content=resp_obj, status_code=200)

    try:
        async with httpx.AsyncClient(timeout=max(_TIMEOUT, 60)) as client:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                **upstream_auth_headers,
            }
            init_body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ZeroShield Gateway", "version": "1.0.0"},
                },
            }
            init_resp = await client.post(upstream_url, json=init_body, headers=headers)
            init_resp.raise_for_status()
            mcp_session = init_resp.headers.get("mcp-session-id")
            if mcp_session:
                headers["Mcp-Session-Id"] = mcp_session

            notif_resp = await client.post(
                upstream_url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=headers,
            )
            notif_resp.raise_for_status()

            call_resp = await client.post(upstream_url, json=call_body, headers=headers)
            call_resp.raise_for_status()
            content_type = call_resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                for line in call_resp.text.split("\n"):
                    line = line.strip()
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        if data_str:
                            try:
                                return await _scan_internal_result(json.loads(data_str))
                            except json.JSONDecodeError:
                                pass
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "error": {"code": -32000, "message": "Empty SSE response from upstream tools/call"},
                    },
                    status_code=200,
                )

            return await _scan_internal_result(call_resp.json())
    except Exception as exc:
        LOG.error("Internal tools-call upstream error: %s", exc)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": f"Upstream tool call failed: {exc}"},
            },
            status_code=200,
        )


# ── Org-Scoped External Gateway Routes ──────────────────────────────
# These routes are the external product surface:
#   /gateway/{org_slug}/mcp/{server_slug}/tools/call
#   /gateway/{org_slug}/mcp/{server_slug}/tools
#   /gateway/{org_slug}/mcp/{server_slug}/health
#
# Auth IS enforced — requests must carry a valid GatewayAPIKey via
# Authorization: Bearer header; the middleware's AuthContext must match
# the org_slug in the URL path.


def _get_auth_context(request: Request):
    """Extract auth context from request state, or None."""
    return getattr(getattr(request, "state", None), "auth_context", None)


def _validate_org_scope(request: Request, org_slug: str):
    """Validate auth context org matches URL org. Returns error JSONResponse or None."""
    auth = _get_auth_context(request)
    if not auth:
        return JSONResponse(
            content={"error": "unauthorized", "message": "Missing authentication."},
            status_code=401,
        )
    if auth.org_slug != org_slug:
        LOG.warning(
            "Org scope mismatch: auth org=%s, url org=%s, key=%s",
            auth.org_slug, org_slug, auth.prefix,
        )
        return JSONResponse(
            content={"error": "org_scope_violation", "message": "API key organization does not match URL."},
            status_code=403,
        )
    return None


async def _audit_and_return_scope_error(
    request: Request, org_slug: str, server_slug: str = ""
):
    """``_validate_org_scope`` + audit the cross-tenant 403 (CHG-0045, item 9 audit-
    completeness).

    ``_validate_org_scope`` only ``LOG.warning``'d a 403 ``org_scope_violation`` (an
    authenticated tenant using its key against ANOTHER org's endpoint) — the single
    most forensically important MCP security event left NO record in the MCPEvent
    audit/SIEM trail (unlike the per-key authz denials, which all call
    ``_record_gateway_event``). Record it as a ``decision=block`` event attributed to
    the CALLER's real org (never the target's, so the block stays inside the caller's
    tenant boundary), with the target org + key prefix in metadata. The 401
    (unauthenticated) has no org to attribute and is left to the auth middleware /
    access logs. Wrapper is additive and keeps ``_validate_org_scope`` sync so the
    ~13 test patch sites + the direct-call unit tests are unaffected.
    """
    err = _validate_org_scope(request, org_slug)
    if err is not None and getattr(err, "status_code", None) == 403:
        auth = _get_auth_context(request)
        caller_org = getattr(auth, "org_slug", "") or ""
        await _record_gateway_event(
            org_slug=caller_org,
            server_slug=server_slug,
            tool_name="",
            decision="block",
            reason="org_scope_violation",
            metadata={
                "transport": "http",
                "enforced_at": "gateway",
                "target_org": org_slug,
                "key_prefix": getattr(auth, "prefix", "") or "",
            },
        )
    return err


# JSON-RPC server-error band; MCP Streamable HTTP returns HTTP 200 + error object.
_JSONRPC_RATE_LIMIT_CODE = -32000


def _rate_limit_response_to_jsonrpc(
    rl_resp: JSONResponse,
    *,
    jsonrpc: str,
    msg_id,
) -> JSONResponse:
    """Map a plain HTTP 429 rate-limit response to the MCP JSON-RPC envelope."""
    try:
        content = rl_resp.body.decode("utf-8") if rl_resp.body else "{}"
        payload = json.loads(content) if content else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    message = str(
        payload.get("message")
        or payload.get("error")
        or "Rate limit exceeded"
    )
    error_data = {
        k: payload[k]
        for k in ("error", "code", "scope")
        if payload.get(k) is not None
    }
    headers = {}
    retry_after = rl_resp.headers.get("Retry-After")
    if retry_after:
        headers["Retry-After"] = retry_after
    return JSONResponse(
        content={
            "jsonrpc": jsonrpc,
            "id": msg_id,
            "error": {
                "code": _JSONRPC_RATE_LIMIT_CODE,
                "message": message,
                **({"data": error_data} if error_data else {}),
            },
        },
        status_code=200,
        headers=headers or None,
    )


async def _mcp_org_rate_limit_raw(auth_ctx) -> JSONResponse | None:
    """Raw per-org TPM + burst/RPM check → a plain 429 ``JSONResponse`` (or None).

    Shared by the JSON-RPC route (which JSON-RPC-wraps this) and the bare REST
    route (which returns it as-is), so BOTH MCP tool-call entry points enforce the
    same per-org capacity ceilings. Extracted for CHG-0031: ``org_mcp_tool_call``
    (the bare REST route) had the per-key tool-call CAP but NOT the per-org
    TPM/burst/RPM rate limit that ``org_mcp_jsonrpc`` applies — a parity gap.
    """
    if auth_ctx is None:
        return None
    try:
        import main as gateway_main
    except Exception:
        return None

    user_id = getattr(auth_ctx, "user_id", None)
    project_id = str(getattr(auth_ctx, "project_id", "") or "")

    rl_resp = await gateway_main._enforce_org_tpm_rate_limit(
        auth_ctx,
        event_type="mcp_blocked",
        user_id=user_id,
        project_id=project_id,
        estimated_tokens=20,
    )
    if rl_resp is None:
        rl_resp = await gateway_main._enforce_org_burst_rpm(
            auth_ctx,
            event_type="mcp_blocked",
            user_id=user_id,
            project_id=project_id,
        )

    # CHG-0089: meter MCP throttling (per-org TPM/burst/RPM 429) so backpressure is
    # visible in Prometheus — the chat per-model limiter was metered (main.py) but the
    # MCP per-org limiter was not, so MCP 429 storms under load were invisible to
    # dashboards/alerting (item 13/20). Recorded as a `rate_limited` MCP decision, in the
    # same amf_gateway_mcp_scan_decisions_total metric as block/redact/allow/monitor.
    if rl_resp is not None:
        try:
            import metrics as _metrics
            _metrics.record_mcp_scan_decision(
                str(getattr(auth_ctx, "org_slug", "") or ""), "rate_limited")
        except Exception:
            pass

    return rl_resp


async def _enforce_mcp_org_rate_limits(
    auth_ctx,
    *,
    jsonrpc: str = "2.0",
    msg_id,
) -> JSONResponse | None:
    """Apply per-org TPM + burst/RPM ceilings (parity with chat/embeddings paths).

    Returns a JSON-RPC error envelope (HTTP 200) when limited, else None.
    """
    raw = await _mcp_org_rate_limit_raw(auth_ctx)
    if raw is not None:
        return _rate_limit_response_to_jsonrpc(raw, jsonrpc=jsonrpc, msg_id=msg_id)
    return None


def _backend_proxy_headers(request: Request, org_slug: str, server_slug: str = "") -> dict:
    """Headers for trusted gateway->backend MCP proxy requests."""
    extra: dict[str, str] = {}
    if server_slug:
        extra["X-Server-Slug"] = server_slug
    headers = _control_request_headers(org_slug, extra)

    auth = _get_auth_context(request)
    if auth is not None:
        if getattr(auth, "user_id", None) is not None:
            headers["X-Gateway-User-Id"] = str(auth.user_id)
        if getattr(auth, "prefix", None):
            headers["X-Gateway-Key-Prefix"] = str(auth.prefix)
        if getattr(auth, "project_id", None):
            headers["X-Gateway-Project-Id"] = str(auth.project_id)
        # G8: forward role names so backend policy engine can evaluate
        # Policy.allowed_roles. Comma-separated, URL-quoted to survive
        # exotic role names (spaces, commas) — backend splits + unquotes.
        roles = getattr(auth, "roles", None) or []
        if roles:
            from urllib.parse import quote as _q
            headers["X-Gateway-Roles"] = ",".join(_q(str(r), safe="") for r in roles)

    return headers


async def _notify_control_needs_reauth(org_slug: str, server_slug: str, reason: str) -> None:
    """Tell the control plane an org's stdio OAuth token can't be refreshed.

    Closes the Flow-2 gap: for mcp-remote (stdio) servers the gateway holds the
    OAuth token in Redis and the control plane has no visibility into refresh
    failures, so an expired token surfaced only as an opaque upstream
    ``invalid_token``. This best-effort backprop lets control set
    ``needs_reauth`` and prompt the operator. Failures here must NEVER break
    the hot path.
    """
    if not server_slug or not _GATEWAY_INTERNAL_API_KEY:
        return
    headers = _control_request_headers(
        org_slug,
        {"X-Server-Slug": server_slug},
    )
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{_BACKEND_URL}/api/mcp-connector/internal/needs-reauth/",
                headers=headers,
                json={"org_slug": org_slug, "server_slug": server_slug, "reason": reason},
            )
            if resp.status_code >= 400:
                LOG.warning(
                    "needs-reauth backprop HTTP %s: %s",
                    resp.status_code,
                    _control_error_snippet(resp),
                )
    except Exception as exc:
        LOG.warning("needs-reauth backprop failed (org=%s server=%s): %s",
                    org_slug, server_slug, exc)


async def _maybe_inject_oauth_header(args: list[str], org_slug: str, server_slug: str = "") -> None:
    """If *args* invoke mcp-remote and we have a stored OAuth token, append --header."""
    # Check if this is a mcp-remote invocation by scanning args
    mcp_url = None
    for i, a in enumerate(args):
        if a == "mcp-remote" or a.endswith("/mcp-remote"):
            # The URL is the next non-flag argument
            for j in range(i + 1, len(args)):
                if not args[j].startswith("-"):
                    mcp_url = args[j]
                    break
            break
    if not mcp_url:
        return
    try:
        from mcp_oauth_proxy import get_stored_token, has_stored_token
        token = await get_stored_token(org_slug, mcp_url)
    except ImportError:
        return
    except Exception as exc:
        LOG.warning("OAuth token lookup failed for %s: %s", mcp_url, exc)
        return
    if token:
        # Only inject if --header Authorization is not already present
        for k, a in enumerate(args):
            if a == "--header" and k + 1 < len(args) and args[k + 1].lower().startswith("authorization:"):
                return
        args.extend(["--header", f"Authorization: Bearer {token}"])
        LOG.info("Injected OAuth header for mcp-remote %s (org=%s)", mcp_url, org_slug)
        return
    # No usable token. If a token record EXISTS for this org+server it means the
    # token expired and could not be refreshed -> genuine re-auth needed. (A
    # server that was never OAuth-authenticated has no record and is skipped.)
    try:
        if await has_stored_token(org_slug, mcp_url):
            await _notify_control_needs_reauth(
                org_slug, server_slug,
                "OAuth token expired and refresh failed — re-authenticate this server.",
            )
    except Exception as exc:
        LOG.warning("needs-reauth check failed for %s: %s", mcp_url, exc)


def _is_sandbox_routed(transport: str) -> bool:
    """Transports routed through the per-org sandbox (gateway NEVER dials upstream).

    stdio + websocket always route via the sandbox adapter. streamable-http + sse
    route via the sandbox too (P4.13/P6.18 §3) when ``MCP_HTTP_VIA_SANDBOX`` is on
    (default) — the sandbox agent dials the upstream (egress-allowlisted); set the
    flag off to fall back to the legacy direct-httpx path.
    """
    if transport in ("stdio", "websocket"):
        return True
    if transport in ("streamable-http", "sse"):
        # Default ON (CHG-0026 / P4.13 rollout complete): remote transports route
        # through the per-org sandbox agent so the gateway never dials the upstream
        # MCP host — isolation holds for every transport by default. Set
        # MCP_HTTP_VIA_SANDBOX=0 only to fall back to the legacy direct-httpx path
        # (still SSRF-guarded) for debugging.
        return os.environ.get("MCP_HTTP_VIA_SANDBOX", "true").strip().lower() in (
            "1", "true", "yes", "on",
        )
    return False


async def _adapter_forward(
    transport: str,
    server_config: dict,
    org_slug: str,
    server_slug: str,
    body: dict,
    jsonrpc: str,
    msg_id,
    *,
    correlation_id: str = "",
) -> JSONResponse:
    """Forward a JSON-RPC message to the per-org sandbox for a non-backend transport.

    ALL remote transports (streamable-http, sse, websocket) go through the broker →
    sandbox → upstream; stdio runs inside the sandbox too. The gateway NEVER dials an
    upstream MCP host itself — closing the last isolation residual (CHG-0026:
    websocket previously connected in-gateway via mcp_ws_adapter despite
    _is_sandbox_routed already declaring ws sandbox-routed)."""
    method = body.get("method", "")
    params = body.get("params", {})
    try:
        if transport in ("streamable-http", "sse", "websocket"):
            # P4.13/P6.18 §3 (+CHG-0026 for websocket): route remote transports
            # through the sandbox agent (gateway → broker → sandbox → upstream). The
            # gateway builds the upstream block (url + egress allowlist + injected
            # Bearer) and the sandbox does the dial — the gateway never connects to
            # the MCP host. The sandbox agent + broker support all three remote
            # transports (upstream_manager handles websocket sessions).
            from mcp_sandbox_client import broker_send_rpc

            upstream_url = server_config.get("url", "")
            oauth_token = None
            try:
                from mcp_oauth_proxy import get_stored_token
                oauth_token = await get_stored_token(org_slug, upstream_url)
            except Exception:  # noqa: BLE001 — no token store / not authed → None
                oauth_token = None
            up_config = {
                "server_slug": server_slug,
                "transport": transport,
                "url": upstream_url,
                "allowed_hosts": server_config.get("allowed_hosts") or [],
                "headers": dict(server_config.get("upstream_headers") or {}),
            }
            result = await broker_send_rpc(
                org_slug, up_config, method, params if params else None,
                msg_id=msg_id, oauth_token=oauth_token, correlation_id=correlation_id,
            )
            return JSONResponse(content=result, status_code=200)
        if transport == "stdio":
            from mcp_stdio_adapter import send_jsonrpc as stdio_send

            # Inject stored OAuth token as --header for mcp-remote servers
            args = list(server_config.get("args", []))
            await _maybe_inject_oauth_header(args, org_slug, server_slug)

            stdio_cfg = dict(server_config)
            stdio_cfg["org_slug"] = org_slug
            stdio_cfg["server_slug"] = server_slug
            result = await stdio_send(
                org_slug=org_slug,
                server_slug=server_slug,
                command=server_config["command"],
                args=args,
                env=server_config.get("env_vars") or None,
                method=method,
                params=params if params else None,
                msg_id=msg_id,
                server_config=stdio_cfg,
            )
        else:
            return JSONResponse(
                content={
                    "jsonrpc": jsonrpc,
                    "id": msg_id,
                    "error": {"code": -32000, "message": f"Unsupported adapter transport: {transport}"},
                },
                status_code=200,
            )

        # result is the full JSON-RPC response dict from the adapter
        return JSONResponse(content=result, status_code=200)
    except Exception as exc:
        LOG.error("Adapter forward error (%s/%s, %s): %s", org_slug, server_slug, transport, exc)
        return JSONResponse(
            content={
                "jsonrpc": jsonrpc,
                "id": msg_id,
                "error": {"code": -32000, "message": f"Adapter error: {exc}"},
            },
            status_code=200,
        )


async def _scanned_tools_list_response(
    payload,
    *,
    jsonrpc,
    msg_id,
    enabled_info,
    org_slug: str,
    server_slug: str,
    actor,
    request_id: str = "",
):
    """CHG-0077: scan tools/list tool DESCRIPTIONS / metadata from an untrusted upstream
    before returning them to the client / LLM.

    GAP: the org (sandbox-routed + backend) tools/list path forwarded upstream tool
    descriptions RAW, while the EXTERNAL proxy path already scans tools/list (it is in
    ``_EXT_FINITE_RESULT_METHODS``). Tool descriptions come LIVE from the untrusted
    upstream MCP server and are shown to the model — a classic tool-poisoning /
    metadata-leak surface. So a secret / PII / internal-IP (or a CHG-0076 encoded-exfil
    payload) embedded in a tool description leaked to the model on the org path.

    Reuses the result-redaction floor: masks a maskable leak in the metadata, and blocks
    (fail-closed) a poisoned metadata block that cannot be safely masked (e.g. an encoded
    exfil payload) — parity with tool RESULT scanning. NOTE: this masks
    secret/PII/internal-IP; prompt-injection text in a description is a SEPARATE detection
    concern (``_INJECTION_KEYWORDS`` is narrow — documented follow-up)."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return JSONResponse(content=payload, status_code=200)
    scanned, blocked, tags, _find, _meta = await _scan_tool_result_floor(
        result,
        tool_name="tools/list",
        enabled_info=enabled_info,
        org_slug=org_slug,
        server_slug=server_slug,
        actor=actor,
    )
    # CHG-0081: audit the tools/list metadata-scan decision. CHG-0077 masked/blocked a
    # poisoned tool-description leak but recorded NO gateway event — so a tool-poisoning
    # block or a secret/PII/IP redaction on the discovery path was INVISIBLE to the
    # MCPEvent audit/SIEM trail (breaks the ...→tag→AUDIT chain for tools/list, which the
    # tools/call path already audits). Record block XOR redact (best-effort, no-op without
    # org); a fully-clean list is not audited to avoid per-discovery noise.
    if blocked or (scanned is not result):
        await _record_gateway_event(
            org_slug=org_slug,
            server_slug=server_slug,
            tool_name="tools/list",
            decision="block" if blocked else "redact",
            reason="tools_list_metadata_scan",
            request_id=request_id,
            metadata={"enforced_at": "gateway_tools_list", "scan_pipeline": "two_tier"},
            compliance_tags=list(tags),
            scan_findings=_find,
        )
    if blocked:
        return JSONResponse(
            content={
                "jsonrpc": jsonrpc,
                "id": msg_id,
                "error": {
                    "code": -32000,
                    "message": "tools/list withheld: server tool metadata matched sensitive content",
                },
            },
            status_code=200,
        )
    if scanned is not result:
        payload["result"] = scanned
    return JSONResponse(content=payload, status_code=200)


@org_gateway_router.post(
    "/{org_slug}/mcp/{server_slug}",
    summary="MCP Streamable HTTP endpoint (JSON-RPC)",
)
async def org_mcp_jsonrpc(org_slug: str, server_slug: str, request: Request):
    """Handle MCP Streamable HTTP protocol messages (JSON-RPC).

    VS Code sends all MCP messages (initialize, tools/list, tools/call, etc.)
    as JSON-RPC POST requests to the base MCP server URL. This handler
    dispatches each method to the appropriate backend endpoint.
    """
    err = await _audit_and_return_scope_error(request, org_slug, server_slug)
    if err:
        return err

    # CHG-0034: reject an oversized body before buffering it (DoS guard).
    if _mcp_body_too_large(request):
        return _mcp_body_too_large_response()

    # M-04: actor identity ({user_id, agent_id, roles}) for actor-scoped MCP
    # policies, derived from the authenticated API key's context.
    _mcp_auth = _get_auth_context(request)
    mcp_actor = None
    if _mcp_auth is not None:
        mcp_actor = {
            "user_id": getattr(_mcp_auth, "user_id", None),
            "agent_id": getattr(_mcp_auth, "prefix", None) or "",
            "roles": list(getattr(_mcp_auth, "roles", None) or []),
        }

    # CHG-0063: cap the ACTUAL streamed bytes so a chunked / no-Content-Length body
    # cannot buffer unbounded (the Content-Length pre-check above only catches an
    # honestly-declared oversize). This buffers + caches into request._body, so the
    # request.json() below reuses the already-capped bytes.
    try:
        await _mcp_read_body_capped(request)
    except _MCPBodyTooLarge:
        return _mcp_body_too_large_response()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            },
            status_code=200,
        )

    method = body.get("method", "")
    params = body.get("params", {})
    msg_id = body.get("id")
    jsonrpc = body.get("jsonrpc", "2.0")

    # S12: per-org TPM + burst/RPM (parity with chat/embeddings hot paths).
    _rl_block = await _enforce_mcp_org_rate_limits(
        _mcp_auth, jsonrpc=jsonrpc, msg_id=msg_id,
    )
    if _rl_block is not None:
        return _rl_block

    LOG.info("MCP JSON-RPC method=%s org=%s server=%s id=%s", method, org_slug, server_slug, msg_id)

    # ── Resolve server transport for routing ──
    server_config = await _get_server_config(org_slug, server_slug)
    transport = (server_config or {}).get("transport", "streamable-http")
    is_adapter_transport = _is_sandbox_routed(transport)

    # ── initialize: respond locally as the MCP server ──
    if method == "initialize":
        return JSONResponse(
            content={
                "jsonrpc": jsonrpc,
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": f"ZeroShield Gateway — {server_slug}",
                        "version": "1.0.0",
                    },
                },
            },
            status_code=200,
        )

    # ── notifications/initialized: acknowledge ──
    if method == "notifications/initialized":
        # Notification — no response needed per JSON-RPC spec.
        # Return 200 with empty body for Streamable HTTP.
        return JSONResponse(content=None, status_code=200)

    # ── tools/list: proxy to backend or forward to adapter ──
    if method == "tools/list":
        # Resolve enable/disable info up-front (used to filter ALL paths).
        enabled_info = await _get_enabled_tools(org_slug, server_slug)

        if is_adapter_transport and server_config:
            adapter_resp = await _adapter_forward(
                transport, server_config, org_slug, server_slug, body, jsonrpc, msg_id,
            )
            # Filter the adapter response in-place to drop disabled tools.
            try:
                payload = json.loads(adapter_resp.body.decode("utf-8")) if adapter_resp.body else None
            except Exception:
                payload = None
            if isinstance(payload, dict):
                result = payload.get("result")
                if isinstance(result, dict) and isinstance(result.get("tools"), list):
                    result["tools"] = _filter_tools_by_key_allowlist(
                        _filter_tools_by_enabled(result["tools"], enabled_info), _mcp_auth
                    )
                    # CHG-0077: scan the upstream tool DESCRIPTIONS/metadata (leak surface)
                    return await _scanned_tools_list_response(
                        payload, jsonrpc=jsonrpc, msg_id=msg_id, enabled_info=enabled_info,
                        org_slug=org_slug, server_slug=server_slug, actor=mcp_actor,
                        request_id=_mcp_request_correlation_id(request, msg_id),  # CHG-0081
                    )
                # CHG-0092: the adapter payload was NOT tools-shaped — e.g. a bare
                # JSON-RPC ERROR envelope from a stdio/ws upstream (an auth-failure
                # tools/list error can echo a token/URL), or a malformed result. It used
                # to return RAW. Scan the whole envelope through the result floor so a
                # secret/PII/internal-IP in an error message is masked (or fail-closed
                # blocked) — the tools/list twin of the CHG-0091 tools/call fix.
                _tl_s, _tl_blocked, _tl_tags, _tl_find, _tl_meta = await _scan_tool_result_floor(
                    payload,
                    tool_name="tools/list",
                    enabled_info=enabled_info,
                    org_slug=org_slug,
                    server_slug=server_slug,
                    actor=mcp_actor,
                )
                if _tl_blocked or (_tl_s is not payload):
                    await _record_gateway_event(
                        org_slug=org_slug,
                        server_slug=server_slug,
                        tool_name="tools/list",
                        decision="block" if _tl_blocked else "redact",
                        reason="tools_list_error_scan",
                        request_id=_mcp_request_correlation_id(request, msg_id),
                        metadata={"transport": transport, "enforced_at": "gateway_adapter",
                                  "scan_pipeline": "two_tier"},
                        compliance_tags=list(_tl_tags),
                        scan_findings=_tl_find,
                    )
                if _tl_blocked:
                    return JSONResponse(
                        content={
                            "jsonrpc": jsonrpc,
                            "id": msg_id,
                            "error": {
                                "code": -32000,
                                "message": "tools/list withheld: server response matched sensitive content",
                            },
                        },
                        status_code=200,
                    )
                if _tl_s is not payload:
                    return JSONResponse(content=_tl_s, status_code=200)
            return adapter_resp

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                resp = await client.get(
                    f"{_BACKEND_URL}/api/mcp-connector/tools/",
                    headers=_backend_proxy_headers(request, org_slug, server_slug),
                )
                data = resp.json()
                # Backend returns list or paginated dict; normalize to MCP format
                if isinstance(data, list):
                    tools_list = data
                elif isinstance(data, dict):
                    tools_list = data.get("results", data.get("tools", []))
                else:
                    tools_list = []

                # Convert backend tool format to MCP tool format
                mcp_tools = []
                for t in tools_list:
                    tool_name = t.get("tool_name") or t.get("name", "")
                    mcp_tool = {
                        "name": tool_name,
                        "description": t.get("description", ""),
                    }
                    schema = t.get("input_schema") or t.get("inputSchema")
                    if schema:
                        mcp_tool["inputSchema"] = schema
                    mcp_tools.append(mcp_tool)

                # Drop tools explicitly marked disabled in backend, then drop tools
                # this key's allowlist forbids (CHG-0038 visibility parity).
                mcp_tools = _filter_tools_by_key_allowlist(
                    _filter_tools_by_enabled(mcp_tools, enabled_info), _mcp_auth
                )

                # CHG-0077: scan tool DESCRIPTIONS/metadata before returning (leak surface).
                return await _scanned_tools_list_response(
                    {"jsonrpc": jsonrpc, "id": msg_id, "result": {"tools": mcp_tools}},
                    jsonrpc=jsonrpc, msg_id=msg_id, enabled_info=enabled_info,
                    org_slug=org_slug, server_slug=server_slug, actor=mcp_actor,
                    request_id=_mcp_request_correlation_id(request, msg_id),  # CHG-0081
                )
            except httpx.TimeoutException:
                return JSONResponse(
                    content={
                        "jsonrpc": jsonrpc,
                        "id": msg_id,
                        "error": {"code": -32000, "message": "Backend timeout"},
                    },
                    status_code=200,
                )
            except httpx.RequestError as exc:
                return JSONResponse(
                    content={
                        "jsonrpc": jsonrpc,
                        "id": msg_id,
                        "error": {"code": -32000, "message": f"Backend unreachable: {exc}"},
                    },
                    status_code=200,
                )

    # ── tools/call: proxy to backend with policy enforcement, or forward to adapter ──
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        call_t0 = time.time()

        # ── E12 FIX 3: least-privilege key controls (mcp_allowed_tools /
        # mcp_max_tool_calls). These sync to Redis from the GatewayAPIKey but
        # were never enforced. Run BEFORE forwarding so a disallowed tool /
        # over-cap call never reaches the MCP server. EMPTY allowlist = all
        # tools; cap of 0 = unlimited. ──
        _mcp_key = _get_auth_context(request)
        if not _tool_allowed_by_key(tool_name, _mcp_key):
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision="block",
                reason="tool_not_allowed_for_key",
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={"transport": transport, "enforced_at": "gateway"},
            )
            return JSONResponse(
                content={
                    "jsonrpc": jsonrpc,
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": "[BLOCKED] tool not allowed for this key",
                            }
                        ],
                        "isError": True,
                    },
                },
                status_code=200,
            )
        _mcp_cap = int(getattr(_mcp_key, "mcp_max_tool_calls", 0) or 0)
        if _mcp_cap > 0:
            _call_n = await _incr_tool_call_count(_mcp_key)
            if _tool_call_cap_exceeded(_call_n, _mcp_cap):
                await _record_gateway_event(
                    org_slug=org_slug,
                    server_slug=server_slug,
                    tool_name=tool_name,
                    decision="block",
                    reason="tool_call_cap_exceeded",
                    latency_ms=int((time.time() - call_t0) * 1000),
                    metadata={
                        "transport": transport,
                        "enforced_at": "gateway",
                        "tool_call_count": _call_n,
                        "tool_call_cap": _mcp_cap,
                    },
                )
                return JSONResponse(
                    content={
                        "jsonrpc": jsonrpc,
                        "id": msg_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "[BLOCKED] tool-call limit exceeded for this key "
                                        f"({_mcp_cap} per turn)."
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    },
                    status_code=200,
                )

        # Enforce per-tool enable/disable for ALL transports BEFORE forwarding.
        # Backend's MCPToolCallView enforces too for HTTP, but for stdio/websocket
        # the adapter path bypasses it entirely — this is the security gap.
        enabled_info = await _get_enabled_tools(org_slug, server_slug)
        if _is_tool_disabled(tool_name, enabled_info):
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision="block",
                reason="tool_disabled",
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={"transport": transport, "enforced_at": "gateway"},
            )
            return JSONResponse(
                content={
                    "jsonrpc": jsonrpc,
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"[BLOCKED] Tool '{tool_name}' is disabled for this server.",
                            }
                        ],
                        "isError": True,
                    },
                },
                status_code=200,
            )

        # ── MCP security scan (two-tier policy + optional Bedrock) ──
        _scan_action = _effective_scan_action(tool_name, enabled_info)
        # Stable per-call id for audit correlation + gateway/backend de-dup.
        # CHG-0050: honor an inbound X-Request-ID (cross-service trace) over the
        # repeatable JSON-RPC id.
        _req_id = _mcp_request_correlation_id(request, msg_id)
        _in_redacted = False
        _inbound_tags: list[str] = []
        _inbound_findings: list[dict] = []
        _scan_meta_in: dict = {}
        _scanned_args, _in_blocked, _in_tags, _in_findings, _scan_meta_in = await _mcp_security_scan(
            arguments,
            scan_direction="input",
            tool_name=tool_name,
            enabled_info=enabled_info,
            org_slug=org_slug,
            server_slug=server_slug,
            actor=mcp_actor,
        )
        # ── E12 FIX 1: hard-block a credential/secret in tool ARGUMENTS even when
        # the resolved scan_action defaults to "tag" (detect-but-allow). Without
        # this, an AWS key / sk- / github / bearer token in args egresses to the
        # MCP server. Only credentials force-block here (not generic PII). A
        # per-tool MCPScanControl set to "monitor" is an explicit operator
        # observe-only override and still wins. ──
        if (
            not _in_blocked
            and _mcp_block_on_credential_enabled()
            and _scan_action != "monitor"
            and _findings_have_credential(_in_findings, _in_tags)
        ):
            _in_blocked = True
            _scan_meta_in = {**_scan_meta_in, "credential_force_block": True}
        if _in_tags or _in_findings:
            _inbound_tags = list(_in_tags)
            _inbound_findings = list(_in_findings)
            if _in_blocked:
                await _record_gateway_event(
                    org_slug=org_slug,
                    server_slug=server_slug,
                    tool_name=tool_name,
                    decision="block",
                    reason="pii_blocked_inbound",
                    request_id=_req_id,
                    latency_ms=int((time.time() - call_t0) * 1000),
                    metadata={
                        "transport": transport,
                        "enforced_at": "gateway",
                        **_scan_meta_in,
                    },
                    compliance_tags=_inbound_tags,
                    scan_findings=_inbound_findings,
                )
                return JSONResponse(
                    content={
                        "jsonrpc": jsonrpc,
                        "id": msg_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"[BLOCKED] Tool '{tool_name}' arguments matched "
                                        f"compliance tags: {', '.join(_inbound_tags) or 'PII'}."
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    },
                    status_code=200,
                )
            if _scanned_args is not arguments:
                # orchestrator applied per-tier inbound redaction
                arguments = _scanned_args
                params["arguments"] = arguments
                _in_redacted = True

        # 3b cross-stage: fields the INPUT-stage policy matches declared — threaded
        # into the OUTBOUND scan so an input-stage RBAC policy match projects those
        # fields out of the RESPONSE (control HTTP-path parity for "role X never
        # sees field F", where the rule fires on the call, not the response).
        _in_rfields = list((_scan_meta_in or {}).get("policy_redaction_fields") or [])

        if is_adapter_transport and server_config:
            # ── D5 (E12 FIX 2): the adapter transport path (stdio / websocket)
            # calls the MCP server directly and bypasses the backend
            # MCPToolCallView. Inbound arg-scanning already ran above; the
            # OUTBOUND tool-result scan + redaction (the same two-tier pipeline
            # the streamable-http path uses) now runs below before returning, so
            # secrets/PII in stdio/ws tool RESULTS are redacted (or blocked) and
            # never returned raw. G8 per-user/role field-level policy filtering
            # (the MCPToolCallView RBAC field masks) now ALSO fires here: CHG-0024
            # masks fields whose policy matches the RESULT, and CHG-0025 threads the
            # INPUT-stage policy's redaction_fields (``_in_rfields``) into the
            # outbound scan so an input-matched RBAC policy projects those fields
            # out of the RESPONSE — control HTTP-path parity on the adapter path.
            LOG.info(
                "mcp_proxy.adapter_transport_gateway_scan org=%s server=%s tool=%s transport=%s",
                org_slug, server_slug, tool_name, transport,
            )
            adapter_resp = await _adapter_forward(
                transport, server_config, org_slug, server_slug, body, jsonrpc, msg_id,
                correlation_id=_req_id,
            )
            # Best-effort audit: record allow (or error) for stdio/websocket calls
            # since these never hit the backend's MCPToolCallView audit path.
            try:
                payload = json.loads(adapter_resp.body.decode("utf-8")) if adapter_resp.body else None
            except Exception:
                payload = None
            decision = "allow"
            reason = ""
            _scan_meta_out: dict = {}
            if isinstance(payload, dict) and payload.get("error"):
                decision = "error"
                reason = str(payload["error"].get("message", ""))[:255]
            # ── Outbound two-tier scan on adapter response ──
            _out_tags = list(_inbound_tags)
            _out_findings = list(_inbound_findings)
            if isinstance(payload, dict):
                _scan_target = payload.get("result") if "result" in payload else payload
                _scanned_out, _out_blocked, _out_tags_new, _out_find_new, _scan_meta_out = (
                    await _mcp_security_scan(
                        _scan_target,
                        scan_direction="output",
                        tool_name=tool_name,
                        enabled_info=enabled_info,
                        org_slug=org_slug,
                        server_slug=server_slug,
                        actor=mcp_actor,
                        extra_redaction_fields=_in_rfields,
                    )
                )
                # CHG-0025: a field-projection redaction can mask named result
                # fields with NO PII/secret finding (the input-stage RBAC policy
                # fired on the call, not the response). Enter the swap block on that
                # signal too, else the masked ``_scanned_out`` would be discarded
                # and the field would leak.
                if _out_tags_new or _out_find_new or (_scan_meta_out or {}).get("redacted_fields"):
                    for t in _out_tags_new:
                        if t not in _out_tags:
                            _out_tags.append(t)
                    _out_findings.extend(_out_find_new)
                    if _out_blocked:
                        decision = "block"
                        reason = "pii_blocked_outbound"
                        adapter_resp = JSONResponse(
                            content={
                                "jsonrpc": jsonrpc,
                                "id": msg_id,
                                "result": {
                                    "content": [{
                                        "type": "text",
                                        "text": (
                                            f"[BLOCKED] Response from '{tool_name}' matched "
                                            f"compliance tags: {', '.join(_out_tags) or 'PII'}."
                                        ),
                                    }],
                                    "isError": True,
                                },
                            },
                            status_code=200,
                        )
                    elif _scanned_out is not _scan_target:
                        # CHG-0091: swap the redacted scan output back wherever the
                        # scan target lived. Normal result -> payload["result"]; a BARE
                        # ERROR envelope (no "result" key — the standard JSON-RPC error
                        # a stdio/ws upstream returns on tool failure, scanned whole per
                        # ``_scan_target`` above) -> swap the entire redacted envelope.
                        # Previously this branch required ``"result" in payload``, so a
                        # secret/PII in error.message was detected then DISCARDED and
                        # the raw error egressed.
                        decision = "redact"
                        if "result" in payload:
                            payload["result"] = _scanned_out
                        else:
                            payload = _scanned_out
                            if isinstance(payload.get("error"), dict):
                                reason = str(payload["error"].get("message", ""))[:255]
                        adapter_resp = JSONResponse(content=payload, status_code=200)
                    elif (
                        # CHG-0091: dropped the ``"result" in payload`` guard so the
                        # redaction floor ALSO fires for a bare error envelope whose
                        # error.message carries a secret/PII under the default "tag"
                        # posture (the detect-only case no swap branch had covered).
                        _mcp_redact_result_on_detect_enabled()
                        and _scan_action != "monitor"
                        and (
                            _findings_have_secret_or_pii(_out_find_new)
                            or _findings_have_infra_network_leak(_out_find_new)  # CHG-0074
                            or _findings_have_exfil(_out_find_new)  # CHG-0096
                        )
                    ):
                        # ── E12: result-REDACTION floor. The output scan DETECTED a
                        # secret/PII but the resolved scan_action ("tag") did not
                        # redact, so the result would egress RAW. Re-run the output
                        # scan with a "redact" enforcement floor and swap in the
                        # masked result (mask, never block). Symmetric to the arg
                        # credential force-block; a per-tool "monitor" still wins. ──
                        (
                            _scanned_floor, _floor_blocked, _floor_tags, _floor_find, _scan_meta_floor
                        ) = await _mcp_security_scan(
                            _scan_target,
                            scan_direction="output",
                            tool_name=tool_name,
                            enabled_info=enabled_info,
                            org_slug=org_slug,
                            server_slug=server_slug,
                            actor=mcp_actor,
                            enforcement_override="redact",
                            extra_redaction_fields=_in_rfields,
                        )
                        if _floor_blocked:
                            # CHG-0074: the redact re-scan BLOCKED (a detected value
                            # redact_all cannot mask survived — e.g. a private file
                            # path beside the network/PII leak). Fail CLOSED (block)
                            # rather than swap in / forward the raw result.
                            decision = "block"
                            reason = "pii_blocked_outbound"
                            for t in (_floor_tags or []):
                                if t not in _out_tags:
                                    _out_tags.append(t)
                            _out_findings.extend(_floor_find or [])
                            adapter_resp = JSONResponse(
                                content={
                                    "jsonrpc": jsonrpc,
                                    "id": msg_id,
                                    "result": {
                                        "content": [{
                                            "type": "text",
                                            "text": (
                                                f"[BLOCKED] Response from '{tool_name}' matched "
                                                f"compliance tags: {', '.join(_out_tags) or 'PII'}."
                                            ),
                                        }],
                                        "isError": True,
                                    },
                                },
                                status_code=200,
                            )
                        elif _scanned_floor is not _scan_target:
                            decision = "redact"
                            if "result" in payload:  # CHG-0091: error-envelope parity
                                payload["result"] = _scanned_floor
                            else:
                                payload = _scanned_floor
                                if isinstance(payload.get("error"), dict):
                                    reason = str(payload["error"].get("message", ""))[:255]
                            adapter_resp = JSONResponse(content=payload, status_code=200)
                            _scan_meta_out = {
                                **_scan_meta_out,
                                "result_redaction_floor": True,
                            }
            # Monitor: findings under a 'monitor' action are allowed but audited.
            if decision == "allow" and bool(
                (_scan_meta_in or {}).get("monitored")
                or (_scan_meta_out or {}).get("monitored")
            ):
                decision = "monitor"
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision=decision,
                reason=reason,
                request_id=_req_id,
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={
                    "transport": transport,
                    "enforced_at": "gateway_adapter",
                    "scan_action": _scan_action,
                    "scan_pipeline": "two_tier",
                    "scan_trace": (
                        list(_scan_meta_in.get("scan_trace") or [])
                        + (list(_scan_meta_out.get("scan_trace") or []) if isinstance(payload, dict) else [])
                    ),
                },
                compliance_tags=_out_tags,
                scan_findings=_out_findings,
            )
            return adapter_resp

        async with httpx.AsyncClient(timeout=max(_TIMEOUT, 60)) as client:
            try:
                resp = await client.post(
                    f"{_BACKEND_URL}/api/mcp-connector/tools/call/",
                    headers={
                        **_backend_proxy_headers(request, org_slug, server_slug),
                        "X-Request-Id": _req_id,
                    },
                    json={
                        "name": tool_name,
                        "arguments": arguments,
                        "server_slug": server_slug,
                    },
                )
                data = resp.json()

                # Adapt backend response to MCP JSON-RPC response
                if resp.status_code == 200 and isinstance(data, dict):
                    _merged_trace_base = list(_scan_meta_in.get("scan_trace") or [])
                    # Check for policy-blocked responses
                    if data.get("blocked"):
                        await _record_gateway_event(
                            org_slug=org_slug,
                            server_slug=server_slug,
                            tool_name=tool_name,
                            decision="block",
                            reason="backend_policy_blocked",
                            request_id=_req_id,
                            latency_ms=int((time.time() - call_t0) * 1000),
                            metadata={
                                "transport": transport,
                                "enforced_at": "backend",
                                "scan_action": _scan_action,
                                "scan_pipeline": "two_tier",
                                "scan_trace": _merged_trace_base,
                                "backend_detail": str(data.get("detail", ""))[:255],
                            },
                            compliance_tags=_inbound_tags,
                            scan_findings=_inbound_findings,
                        )
                        return JSONResponse(
                            content={
                                "jsonrpc": jsonrpc,
                                "id": msg_id,
                                "result": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": f"[BLOCKED by policy] {data.get('detail', 'Tool call blocked by security policy')}",
                                        }
                                    ],
                                    "isError": True,
                                },
                            },
                            status_code=200,
                        )

                    # Normal successful response — run outbound two-tier scan.
                    result_content = data.get("result") or data.get("content")
                    _out_tags2 = list(_inbound_tags)
                    _out_findings2 = list(_inbound_findings)
                    _scanned_content, _out_blocked2, _out_tags_new2, _out_find_new2, _scan_meta_out2 = (
                        await _mcp_security_scan(
                            result_content,
                            scan_direction="output",
                            tool_name=tool_name,
                            enabled_info=enabled_info,
                            org_slug=org_slug,
                            server_slug=server_slug,
                            actor=mcp_actor,
                        )
                    )
                    for t in _out_tags_new2:
                        if t not in _out_tags2:
                            _out_tags2.append(t)
                    _out_findings2.extend(_out_find_new2)
                    # One merged two-tier trace (inbound + outbound) per call.
                    _monitored = bool(
                        (_scan_meta_in or {}).get("monitored")
                        or (_scan_meta_out2 or {}).get("monitored")
                    )
                    _merged_meta = {
                        "transport": transport,
                        "enforced_at": "gateway",
                        "scan_action": _scan_action,
                        "scan_pipeline": "two_tier",
                        "monitored": _monitored,
                        "scan_trace": _merged_trace_base + list(_scan_meta_out2.get("scan_trace") or []),
                    }
                    if _out_blocked2:
                        await _record_gateway_event(
                            org_slug=org_slug,
                            server_slug=server_slug,
                            tool_name=tool_name,
                            decision="block",
                            reason="pii_blocked_outbound",
                            request_id=_req_id,
                            latency_ms=int((time.time() - call_t0) * 1000),
                            metadata=_merged_meta,
                            compliance_tags=_out_tags2,
                            scan_findings=_out_findings2,
                        )
                        return JSONResponse(
                            content={
                                "jsonrpc": jsonrpc,
                                "id": msg_id,
                                "result": {
                                    "content": [{
                                        "type": "text",
                                        "text": (
                                            f"[BLOCKED] Response from '{tool_name}' matched "
                                            f"compliance tags: {', '.join(_out_tags2) or 'PII'}."
                                        ),
                                    }],
                                    "isError": True,
                                },
                            },
                            status_code=200,
                        )
                    # ── E12: result-REDACTION floor. The output scan DETECTED a
                    # secret/PII but the resolved scan_action ("tag") did not
                    # redact, so the result would egress RAW to the LLM/client.
                    # Re-run the output scan with a "redact" enforcement floor and
                    # swap in the masked content (mask, never block). Symmetric to
                    # the arg credential force-block; a per-tool "monitor" wins. ──
                    if (
                        _scanned_content is result_content
                        and _mcp_redact_result_on_detect_enabled()
                        and _scan_action != "monitor"
                        and (
                            _findings_have_secret_or_pii(_out_find_new2)
                            or _findings_have_infra_network_leak(_out_find_new2)  # CHG-0074
                            or _findings_have_exfil(_out_find_new2)  # CHG-0096
                        )
                    ):
                        (
                            _floor_content, _floor_blocked2, _floor_tags2, _floor_find2, _scan_meta_floor2
                        ) = await _mcp_security_scan(
                            result_content,
                            scan_direction="output",
                            tool_name=tool_name,
                            enabled_info=enabled_info,
                            org_slug=org_slug,
                            server_slug=server_slug,
                            actor=mcp_actor,
                            enforcement_override="redact",
                        )
                        if _floor_blocked2:
                            # CHG-0074: the redact re-scan BLOCKED (a detected value
                            # redact_all cannot mask survived — e.g. a private file
                            # path beside the network/PII leak). Fail CLOSED (block)
                            # rather than forward the raw result. Mirrors the
                            # _out_blocked2 block path above.
                            for t in (_floor_tags2 or []):
                                if t not in _out_tags2:
                                    _out_tags2.append(t)
                            _out_findings2.extend(_floor_find2 or [])
                            _merged_meta["result_redaction_floor_block"] = True
                            await _record_gateway_event(
                                org_slug=org_slug,
                                server_slug=server_slug,
                                tool_name=tool_name,
                                decision="block",
                                reason="pii_blocked_outbound",
                                request_id=_req_id,
                                latency_ms=int((time.time() - call_t0) * 1000),
                                metadata=_merged_meta,
                                compliance_tags=_out_tags2,
                                scan_findings=_out_findings2,
                            )
                            return JSONResponse(
                                content={
                                    "jsonrpc": jsonrpc,
                                    "id": msg_id,
                                    "result": {
                                        "content": [{
                                            "type": "text",
                                            "text": (
                                                f"[BLOCKED] Response from '{tool_name}' matched "
                                                f"compliance tags: {', '.join(_out_tags2) or 'PII'}."
                                            ),
                                        }],
                                        "isError": True,
                                    },
                                },
                                status_code=200,
                            )
                        if _floor_content is not result_content:
                            _scanned_content = _floor_content
                            _merged_meta["result_redaction_floor"] = True
                    # The orchestrator already applied any per-tier redaction and
                    # returns the mutated content; swap it in whenever it changed.
                    _was_redacted = _in_redacted or (_scanned_content is not result_content)
                    if _scanned_content is not result_content:
                        result_content = _scanned_content
                    # Decision precedence: block (handled above) > redact > monitor > allow.
                    _decision = (
                        "redact" if _was_redacted
                        else ("monitor" if _monitored else "allow")
                    )
                    # Always record exactly one audit event per call (incl. clean allow),
                    # so every scanned call is provably auditable, not just findings.
                    await _record_gateway_event(
                        org_slug=org_slug,
                        server_slug=server_slug,
                        tool_name=tool_name,
                        decision=_decision,
                        reason=("scan_findings" if _out_findings2 else "clean"),
                        request_id=_req_id,
                        latency_ms=int((time.time() - call_t0) * 1000),
                        metadata=_merged_meta,
                        compliance_tags=_out_tags2,
                        scan_findings=_out_findings2,
                    )

                    if isinstance(result_content, list):
                        content = result_content
                    elif isinstance(result_content, str):
                        content = [{"type": "text", "text": result_content}]
                    elif isinstance(result_content, dict):
                        content = [{"type": "text", "text": json.dumps(result_content)}]
                    else:
                        content = [{"type": "text", "text": json.dumps(data)}]

                    return JSONResponse(
                        content={
                            "jsonrpc": jsonrpc,
                            "id": msg_id,
                            "result": {"content": content},
                        },
                        status_code=200,
                    )
                else:
                    # Record the failed call too, so audit history captures the
                    # attempt (and any inbound findings) regardless of success.
                    # CP23: a control 4xx enforcement denial (tool disabled / not
                    # registered / schema-invalid / policy block) is a BLOCK, not an
                    # error — classify it so the counters reflect real enforcement.
                    _decision, _reason, _enforced_at = _classify_backend_failure(
                        resp.status_code, data
                    )
                    await _record_gateway_event(
                        org_slug=org_slug,
                        server_slug=server_slug,
                        tool_name=tool_name,
                        decision=_decision,
                        reason=_reason,
                        request_id=_req_id,
                        latency_ms=int((time.time() - call_t0) * 1000),
                        metadata={
                            "transport": transport,
                            "enforced_at": _enforced_at,
                            "backend_status": resp.status_code,
                            "scan_direction": "inbound",
                            "scan_action": _scan_action,
                            "scan_pipeline": "two_tier",
                            "scan_trace": list(_scan_meta_in.get("scan_trace") or []),
                        },
                        compliance_tags=_inbound_tags,
                        scan_findings=_inbound_findings,
                    )
                    return JSONResponse(
                        content={
                            "jsonrpc": jsonrpc,
                            "id": msg_id,
                            "error": {
                                "code": -32000,
                                "message": data.get("error") or data.get("detail") or f"Backend error (HTTP {resp.status_code})",
                            },
                        },
                        status_code=200,
                    )
            except httpx.TimeoutException:
                return JSONResponse(
                    content={
                        "jsonrpc": jsonrpc,
                        "id": msg_id,
                        "error": {"code": -32000, "message": "Tool call timed out"},
                    },
                    status_code=200,
                )
            except httpx.RequestError as exc:
                return JSONResponse(
                    content={
                        "jsonrpc": jsonrpc,
                        "id": msg_id,
                        "error": {"code": -32000, "message": f"Backend unreachable: {exc}"},
                    },
                    status_code=200,
                )

    # ── ping: respond locally ──
    if method == "ping":
        return JSONResponse(
            content={"jsonrpc": jsonrpc, "id": msg_id, "result": {}},
            status_code=200,
        )

    # ── Unknown method ──
    return JSONResponse(
        content={
            "jsonrpc": jsonrpc,
            "id": msg_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        },
        status_code=200,
    )


@org_gateway_router.post(
    "/{org_slug}/mcp/{server_slug}/tools/call",
    summary="Execute MCP tool via org-scoped gateway",
)
async def org_mcp_tool_call(org_slug: str, server_slug: str, request: Request):
    """Execute a tool call routed through the org's MCP gateway endpoint.

    Proxies to the backend's tool call API which handles policy enforcement,
    tool controls, and observability recording.

    INVARIANT 4: Error responses are deterministic and structured.
    - Transport failures → 502 with error_code=backend_unreachable
    - Timeouts → 504 with error_code=backend_timeout
    - Backend errors → pass-through with original status code
    """
    err = await _audit_and_return_scope_error(request, org_slug, server_slug)
    if err:
        return err

    # CHG-0034: reject an oversized body before buffering it (DoS guard).
    if _mcp_body_too_large(request):
        return _mcp_body_too_large_response()

    try:  # CHG-0063: cap the ACTUAL bytes (chunked/no-Content-Length DoS guard)
        body = await _mcp_read_body_capped(request)
    except _MCPBodyTooLarge:
        return _mcp_body_too_large_response()

    # ── Scan parity with org_mcp_jsonrpc (the bare REST route previously
    # forwarded verbatim with NO scan). Extract the tool args from the JSON body
    # and run the SAME inbound arg scan + credential hard-block before forwarding;
    # on the response run the SAME outbound result scan + redaction floor. ──
    _mcp_auth = _get_auth_context(request)
    mcp_actor = None
    if _mcp_auth is not None:
        mcp_actor = {
            "user_id": getattr(_mcp_auth, "user_id", None),
            "agent_id": getattr(_mcp_auth, "prefix", None) or "",
            "roles": list(getattr(_mcp_auth, "roles", None) or []),
        }
    # CHG-0050: per-request correlation id (honors an inbound X-Request-ID) threaded
    # into every audit event below, so a bare-REST tool call is traceable end to end
    # (this route previously recorded audit events with NO correlation id at all).
    _req_id = _mcp_request_correlation_id(request)

    # S12 (CHG-0031): per-org TPM + burst/RPM rate limit — parity with
    # org_mcp_jsonrpc. This bare REST route enforced the per-key tool-call CAP but
    # NOT the per-org rate limit, so a tenant could exceed org burst/RPM ceilings
    # via /tools/call while the JSON-RPC route capped them. Returns a plain 429.
    _rl_rest = await _mcp_org_rate_limit_raw(_mcp_auth)
    if _rl_rest is not None:
        return _rl_rest

    try:
        parsed = json.loads(body) if body else {}
    except Exception:
        parsed = {}
    tool_name = ""
    arguments = {}
    if isinstance(parsed, dict):
        tool_name = str(parsed.get("name") or parsed.get("tool_name") or "")
        arguments = parsed.get("arguments")
        if arguments is None:
            arguments = {}

    enabled_info = await _get_enabled_tools(org_slug, server_slug)
    call_t0 = time.time()
    if tool_name:
        # ── Per-key authorization parity with org_mcp_jsonrpc (BACKSTOP_FINDINGS
        # G2 item 3). This bare REST route previously enforced NONE of the key
        # controls, so a caller could invoke a tool outside its key allowlist,
        # exceed the per-turn call cap, or call a disabled tool — all of which the
        # JSON-RPC route blocks. Run these BEFORE the arg scan / forward. ──
        if not _tool_allowed_by_key(tool_name, _mcp_auth):
            await _record_gateway_event(
                org_slug=org_slug, server_slug=server_slug, tool_name=tool_name,
                decision="block", reason="tool_not_allowed_for_key",
                request_id=_req_id,
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={"transport": "rest", "enforced_at": "gateway"},
            )
            return JSONResponse(
                content={"blocked": True, "error": "blocked",
                         "detail": "Tool not allowed for this key.", "compliance_tags": []},
                status_code=403,
            )
        _mcp_cap = int(getattr(_mcp_auth, "mcp_max_tool_calls", 0) or 0)
        if _mcp_cap > 0:
            _call_n = await _incr_tool_call_count(_mcp_auth)
            if _tool_call_cap_exceeded(_call_n, _mcp_cap):
                await _record_gateway_event(
                    org_slug=org_slug, server_slug=server_slug, tool_name=tool_name,
                    decision="block", reason="tool_call_cap_exceeded",
                    request_id=_req_id,
                    latency_ms=int((time.time() - call_t0) * 1000),
                    metadata={"transport": "rest", "enforced_at": "gateway",
                              "tool_call_count": _call_n, "tool_call_cap": _mcp_cap},
                )
                return JSONResponse(
                    content={"blocked": True, "error": "blocked",
                             "detail": f"Tool-call limit exceeded for this key ({_mcp_cap} per turn).",
                             "compliance_tags": []},
                    status_code=429,
                )
        if _is_tool_disabled(tool_name, enabled_info):
            await _record_gateway_event(
                org_slug=org_slug, server_slug=server_slug, tool_name=tool_name,
                decision="block", reason="tool_disabled",
                request_id=_req_id,
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={"transport": "rest", "enforced_at": "gateway"},
            )
            return JSONResponse(
                content={"blocked": True, "error": "blocked",
                         "detail": f"Tool '{tool_name}' is disabled for this server.",
                         "compliance_tags": []},
                status_code=403,
            )
        scanned_args, in_blocked, in_tags, in_findings, scan_meta_in = await _scan_tool_args_block(
            arguments,
            tool_name=tool_name,
            enabled_info=enabled_info,
            org_slug=org_slug,
            server_slug=server_slug,
            actor=mcp_actor,
        )
        if in_blocked:
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision="block",
                reason="pii_blocked_inbound",
                request_id=_req_id,
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={"transport": "rest", "enforced_at": "gateway", **scan_meta_in},
                compliance_tags=list(in_tags),
                scan_findings=list(in_findings),
            )
            return JSONResponse(
                content={
                    "blocked": True,
                    "error": "blocked",
                    "detail": (
                        f"Tool '{tool_name}' arguments matched compliance tags: "
                        f"{', '.join(in_tags) or 'credential/PII'}."
                    ),
                    "compliance_tags": list(in_tags),
                },
                status_code=403,
            )
        # Forward any per-tier inbound redaction the orchestrator applied.
        if scanned_args is not arguments and isinstance(parsed, dict):
            parsed["arguments"] = scanned_args
            arguments = scanned_args
            body = json.dumps(parsed).encode()
            # CHG-0109: audit the inbound ARG redaction (parity with the block branch
            # above + the main org path's _was_redacted event; inbound twin of CHG-0081).
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision="redact",
                reason="pii_redacted_inbound",
                request_id=_req_id,
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={"transport": "rest", "enforced_at": "gateway", **scan_meta_in},
                compliance_tags=list(in_tags),
                scan_findings=list(in_findings),
            )

    async with httpx.AsyncClient(timeout=max(_TIMEOUT, 60)) as client:
        try:
            resp = await client.post(
                f"{_BACKEND_URL}/api/mcp-connector/tools/call/",
                content=body,
                headers=_backend_proxy_headers(request, org_slug, server_slug),
            )
            data = resp.json()
            # ── Outbound result scan + redaction floor (parity with main path).
            # Only when we know the tool name, the call succeeded, and the
            # backend did not itself block; otherwise pass through unchanged. ──
            if (
                tool_name
                and resp.status_code == 200
                and isinstance(data, dict)
                and not data.get("blocked")
            ):
                result_content = data.get("result")
                if result_content is None:
                    result_content = data.get("content")
                if result_content is not None:
                    (
                        scanned_content, out_blocked, out_tags, out_findings, scan_meta_out
                    ) = await _scan_tool_result_floor(
                        result_content,
                        tool_name=tool_name,
                        enabled_info=enabled_info,
                        org_slug=org_slug,
                        server_slug=server_slug,
                        actor=mcp_actor,
                    )
                    if out_blocked:
                        await _record_gateway_event(
                            org_slug=org_slug,
                            server_slug=server_slug,
                            tool_name=tool_name,
                            decision="block",
                            reason="pii_blocked_outbound",
                            request_id=_req_id,
                            latency_ms=int((time.time() - call_t0) * 1000),
                            metadata={"transport": "rest", "enforced_at": "gateway", **scan_meta_out},
                            compliance_tags=list(out_tags),
                            scan_findings=list(out_findings),
                        )
                        return JSONResponse(
                            content={
                                "blocked": True,
                                "error": "blocked",
                                "detail": (
                                    f"Response from '{tool_name}' matched compliance "
                                    f"tags: {', '.join(out_tags) or 'PII'}."
                                ),
                                "compliance_tags": list(out_tags),
                            },
                            status_code=403,
                        )
                    if scanned_content is not result_content:
                        # CHG-0082: audit the result REDACTION. The block branch above
                        # audits, but a masked (redacted) result was swapped in SILENTLY
                        # — so a secret/PII/IP masked on the bare-REST tool-call path was
                        # invisible to audit/SIEM, asymmetric with the block path AND with
                        # org_mcp_jsonrpc (which audits redact). Record it before egress.
                        await _record_gateway_event(
                            org_slug=org_slug,
                            server_slug=server_slug,
                            tool_name=tool_name,
                            decision="redact",
                            reason="pii_redacted_outbound",
                            request_id=_req_id,
                            latency_ms=int((time.time() - call_t0) * 1000),
                            metadata={"transport": "rest", "enforced_at": "gateway", **scan_meta_out},
                            compliance_tags=list(out_tags),
                            scan_findings=list(out_findings),
                        )
                        # Swap the masked content back under whichever key carried it.
                        if data.get("result") is not None:
                            data["result"] = scanned_content
                        else:
                            data["content"] = scanned_content
            return JSONResponse(content=data, status_code=resp.status_code)
        except httpx.TimeoutException as exc:
            LOG.error("Org MCP tool call timeout: %s", exc)
            return JSONResponse(
                content={"error_code": "backend_timeout", "error": "Backend request timed out", "org": org_slug, "server": server_slug},
                status_code=504,
            )
        except httpx.RequestError as exc:
            LOG.error("Org MCP tool call proxy error: %s", exc)
            return JSONResponse(
                content={"error_code": "backend_unreachable", "error": "Backend unreachable", "org": org_slug, "server": server_slug},
                status_code=502,
            )


@org_gateway_router.get(
    "/{org_slug}/mcp/{server_slug}/tools",
    summary="List tools for org-scoped MCP server",
)
async def org_mcp_tools_list(org_slug: str, server_slug: str, request: Request):
    """List available tools for a specific org MCP server."""
    err = await _audit_and_return_scope_error(request, org_slug, server_slug)
    if err:
        return err

    _mcp_auth = _get_auth_context(request)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{_BACKEND_URL}/api/mcp-connector/tools/",
                headers=_backend_proxy_headers(request, org_slug, server_slug),
            )
            data = resp.json()
            # Filter disabled tools + per-key allowlist (CHG-0038) so REST clients see
            # the same least-privilege view as JSON-RPC.
            enabled_info = await _get_enabled_tools(org_slug, server_slug)
            if isinstance(data, list):
                data = _filter_tools_by_key_allowlist(
                    _filter_tools_by_enabled(data, enabled_info), _mcp_auth
                )
            elif isinstance(data, dict) and isinstance(data.get("results"), list):
                data["results"] = _filter_tools_by_key_allowlist(
                    _filter_tools_by_enabled(data["results"], enabled_info), _mcp_auth
                )
            # CHG-0082: scan the tool DESCRIPTIONS/metadata (tool-poisoning / secret/PII/IP
            # leak surface) — this REST tools endpoint filtered but did NOT scan, while the
            # JSON-RPC tools/list already scans (CHG-0077). Reuse the result-redaction floor
            # (masks a maskable leak; blocks unmaskable/encoded-exfil metadata) + audit the
            # block/redact decision. Parity + defense-in-depth for a compromised tool
            # registration.
            _tools_ref = data if isinstance(data, list) else (
                data.get("results") if isinstance(data, dict) else None
            )
            if _tools_ref is not None:
                _tl_scanned, _tl_blocked, _tl_tags, _tl_find, _tl_meta = await _scan_tool_result_floor(
                    _tools_ref, tool_name="tools/list", enabled_info=enabled_info,
                    org_slug=org_slug, server_slug=server_slug, actor=None,
                )
                if _tl_blocked or (_tl_scanned is not _tools_ref):
                    await _record_gateway_event(
                        org_slug=org_slug, server_slug=server_slug, tool_name="tools/list",
                        decision="block" if _tl_blocked else "redact",
                        reason="tools_list_metadata_scan",
                        request_id=_mcp_request_correlation_id(request),
                        metadata={"transport": "rest", "enforced_at": "gateway_tools_list"},
                        compliance_tags=list(_tl_tags), scan_findings=_tl_find,
                    )
                if _tl_blocked:
                    return JSONResponse(
                        content={"error": "tools_withheld", "detail": (
                            "tool metadata matched sensitive content"),
                            "compliance_tags": list(_tl_tags)},
                        status_code=403,
                    )
                if _tl_scanned is not _tools_ref:
                    if isinstance(data, list):
                        data = _tl_scanned
                    else:
                        data["results"] = _tl_scanned
            return JSONResponse(content=data, status_code=resp.status_code)
        except httpx.TimeoutException as exc:
            return JSONResponse(
                content={"error_code": "backend_timeout", "error": "Backend request timed out", "org": org_slug, "server": server_slug},
                status_code=504,
            )
        except httpx.RequestError as exc:
            return JSONResponse(
                content={"error_code": "backend_unreachable", "error": "Backend unreachable", "org": org_slug, "server": server_slug},
                status_code=502,
            )


@org_gateway_router.get(
    "/{org_slug}/mcp/{server_slug}/health",
    summary="Health check for org-scoped MCP server",
)
async def org_mcp_server_health(org_slug: str, server_slug: str, request: Request):
    """Check health of a specific org MCP server."""
    err = await _audit_and_return_scope_error(request, org_slug, server_slug)
    if err:
        return err

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{_BACKEND_URL}/api/mcp-connector/health/",
                headers=_backend_proxy_headers(request, org_slug, server_slug),
            )
            data = resp.json()
            return JSONResponse(content=data, status_code=resp.status_code)
        except httpx.TimeoutException as exc:
            return JSONResponse(
                content={"error_code": "backend_timeout", "error": "Backend request timed out", "org": org_slug, "server": server_slug},
                status_code=504,
            )
        except httpx.RequestError as exc:
            return JSONResponse(
                content={"error_code": "backend_unreachable", "error": "Backend unreachable", "org": org_slug, "server": server_slug},
                status_code=502,
            )
