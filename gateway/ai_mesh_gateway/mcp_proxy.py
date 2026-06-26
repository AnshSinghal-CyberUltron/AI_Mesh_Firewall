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
import logging
import os
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

LOG = logging.getLogger("gateway.mcp_proxy")

router = APIRouter(prefix="/v1/mcp", tags=["MCP Proxy"])

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

# Allowlist of external MCP server domains that can be proxied.
# Prevents open-relay abuse while still allowing known MCP endpoints.
_ALLOWED_MCP_DOMAINS = {
    "mcp.context7.com",
    "api.githubcopilot.com",
    "mcp.linear.app",
}

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
            LOG.error("MCP proxy error → %s: %s", url, exc)
            return JSONResponse(
                content={"error": "MCP service unreachable", "detail": str(exc)},
                status_code=502,
            )


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
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{_BACKEND_URL}/api/mcp-connector/internal/record-event/",
                headers=headers,
                json=payload,
            )
    except Exception as exc:
        LOG.warning("Audit event record failed (org=%s tool=%s): %s",
                    org_slug, tool_name, exc)


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

    Fail-safe: if the scan helper errors, do NOT raise and do NOT block — return
    the RAW result unscanned with ``result_scan_error`` in meta so the caller can
    log/flag while preserving availability (results never 500 the request).
    """
    scan_action = _effective_scan_action(tool_name, enabled_info)
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
            "mcp_proxy.result_scan_failed org=%s server=%s tool=%s: %s (returning raw)",
            org_slug, server_slug, tool_name, exc,
        )
        return result_content, False, [], [], {"result_scan_error": True}

    if blocked:
        return scanned, True, tags, findings, meta

    # E12 result-REDACTION floor: detected secret/PII but the resolved action did
    # not redact, so the result would egress RAW. Re-scan with a "redact" floor.
    if (
        scanned is result_content
        and _mcp_redact_result_on_detect_enabled()
        and scan_action != "monitor"
        and _findings_have_secret_or_pii(findings)
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
            if floor_content is not result_content:
                scanned = floor_content
                meta = {**meta, "result_redaction_floor": True}
        except Exception as exc:  # pragma: no cover - defensive
            LOG.warning(
                "mcp_proxy.result_floor_failed org=%s server=%s tool=%s: %s",
                org_slug, server_slug, tool_name, exc,
            )
    return scanned, False, tags, findings, meta


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
        count = await client.incr(rk)
        if count == 1:
            await client.expire(rk, _MCP_TOOL_CALL_WINDOW_SEC)
        return int(count)
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
) -> tuple[object, bool, list[str], list[dict], dict]:
    """Scan MCP payload via two-tier orchestrator. Returns (payload, blocked, tags, findings, metadata).

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
    )
    meta = {
        "scan_trace": result.scan_trace,
        "scan_direction": mcp_direction,
        "scan_action": action,
        "scan_pipeline": "two_tier",
        "monitored": result.monitored,
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

    target_url = f"https://{hostname}/{remaining}"

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }
    body = await request.body()

    # ── Inbound credential hard-block on the transparent external proxy.
    # This path is transport-level (no org/server/tool scoping), so the
    # resolved scan_action defaults to "tag" (enabled_info=None) and the E12
    # credential force-block still fires: a credential in tools/call arguments
    # is blocked before it egresses to the external MCP server. Best-effort —
    # if the body is not a tools/call JSON-RPC, this is a no-op. ──
    _ext_tool_name = ""
    if body:
        try:
            _ext_req = json.loads(body)
        except Exception:
            _ext_req = None
        if isinstance(_ext_req, dict) and _ext_req.get("method") == "tools/call":
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
            # TODO(mcp-egress-scan): streaming (SSE) egress on the transparent
            # external proxy is NOT scanned — buffering the stream to scan it
            # would break MCP Streamable HTTP semantics (progressive delivery,
            # long-lived connections). Inbound request args ARE credential-scanned
            # above; outbound stream content is passed through verbatim. A future
            # streaming-aware scanner (chunk-boundary tolerant) should close this.
            LOG.warning(
                "ext_mcp_proxy.streaming_egress_unscanned host=%s tool=%s — SSE "
                "response passed through without outbound result scan",
                hostname, _ext_tool_name or "?",
            )
            async def stream_gen():
                try:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
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

        # For normal JSON / text / binary responses, read fully and close
        body_bytes = await resp.aread()
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
        if (
            resp.status_code == 200
            and isinstance(data, dict)
            and isinstance(data.get("result"), dict)
            and data["result"].get("content") is not None
        ):
            _ext_result_content = data["result"]["content"]
            (
                _scanned_content, _out_blocked, _out_tags, _out_findings, _scan_meta_out
            ) = await _scan_tool_result_floor(
                _ext_result_content,
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
            if _scanned_content is not _ext_result_content:
                data["result"]["content"] = _scanned_content

        return JSONResponse(content=data, status_code=resp.status_code, headers=resp_headers)
    except httpx.RequestError as exc:
        await client.aclose()
        exc_name = type(exc).__name__
        if "name resolution" in str(exc).lower() or "nodename" in str(exc).lower():
            LOG.error("DNS resolution failed for ext-proxy target %s: %s", target_url, exc)
            return JSONResponse(
                content={"error": f"DNS resolution failed for '{hostname}'", "detail": str(exc)},
                status_code=502,
            )
        LOG.error("External MCP proxy error → %s: %s (%s)", target_url, exc, exc_name)
        return JSONResponse(
            content={"error": "External MCP server unreachable", "detail": str(exc)},
            status_code=502,
        )


# ── Internal MCP Tool Discovery ──────────────────────────────────────
# Called by the backend during tool sync for every transport (stdio,
# websocket, streamable-http, sse). Replaces the previous ContextForge-based
# discovery path that was removed in DECISION-D Phase 0.
# Auth: validated via X-Gateway-Internal-Key (same shared secret as backend).


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
    LOG.info(
        "Internal discover-tools: org=%s server=%s transport=%s",
        org_slug, server_slug, transport,
    )

    tools_list_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }

    if transport in ("stdio", "websocket"):
        return await _adapter_forward(
            transport, config, org_slug, server_slug,
            tools_list_body, "2.0", 1,
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
                                return JSONResponse(content=json.loads(data_str), status_code=200)
                            except json.JSONDecodeError:
                                pass
                return JSONResponse(
                    content={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}},
                    status_code=200,
                )
            else:
                return JSONResponse(content=tools_resp.json(), status_code=200)
    except Exception as exc:
        LOG.error("Internal discover-tools upstream error: %s", exc)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": f"Upstream discovery failed: {exc}"},
            },
            status_code=200,
        )


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
    LOG.info(
        "Internal tools-call: org=%s server=%s transport=%s tool=%s",
        org_slug, server_slug, transport, tool_name,
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

    call_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    if transport in ("stdio", "websocket"):
        return await _adapter_forward(
            transport, config, org_slug, server_slug,
            call_body, "2.0", 1,
        )

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
        Scans ``result.content`` (the MCP tool-output carrier); on an output
        block returns a JSON-RPC error, otherwise swaps in masked content. Never
        raises (fail-safe inside the scan helper) so the response still flows.
        """
        if not isinstance(resp_obj, dict):
            return JSONResponse(content=resp_obj, status_code=200)
        result_obj = resp_obj.get("result")
        result_content = result_obj.get("content") if isinstance(result_obj, dict) else None
        if result_content is None:
            return JSONResponse(content=resp_obj, status_code=200)
        (
            scanned_content, out_blocked, out_tags, out_findings, scan_meta_out
        ) = await _scan_tool_result_floor(
            result_content,
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
        if scanned_content is not result_content and isinstance(result_obj, dict):
            result_obj["content"] = scanned_content
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


async def _adapter_forward(
    transport: str,
    server_config: dict,
    org_slug: str,
    server_slug: str,
    body: dict,
    jsonrpc: str,
    msg_id,
) -> JSONResponse:
    """Forward a JSON-RPC message to a stdio or websocket adapter."""
    method = body.get("method", "")
    params = body.get("params", {})
    try:
        if transport == "stdio":
            from mcp_stdio_adapter import send_jsonrpc as stdio_send

            # Inject stored OAuth token as --header for mcp-remote servers
            args = list(server_config.get("args", []))
            await _maybe_inject_oauth_header(args, org_slug, server_slug)

            result = await stdio_send(
                org_slug=org_slug,
                server_slug=server_slug,
                command=server_config["command"],
                args=args,
                env=server_config.get("env_vars") or None,
                method=method,
                params=params if params else None,
                msg_id=msg_id,
            )
        elif transport == "websocket":
            from mcp_ws_adapter import send_jsonrpc as ws_send
            result = await ws_send(
                org_slug=org_slug,
                server_slug=server_slug,
                url=server_config["url"],
                auth_headers=None,
                method=method,
                params=params if params else None,
                msg_id=msg_id,
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
    err = _validate_org_scope(request, org_slug)
    if err:
        return err

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

    LOG.info("MCP JSON-RPC method=%s org=%s server=%s id=%s", method, org_slug, server_slug, msg_id)

    # ── Resolve server transport for routing ──
    server_config = await _get_server_config(org_slug, server_slug)
    transport = (server_config or {}).get("transport", "streamable-http")
    is_adapter_transport = transport in ("stdio", "websocket")

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
                    result["tools"] = _filter_tools_by_enabled(result["tools"], enabled_info)
                    return JSONResponse(content=payload, status_code=200)
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

                # Drop tools explicitly marked disabled in backend.
                mcp_tools = _filter_tools_by_enabled(mcp_tools, enabled_info)

                return JSONResponse(
                    content={
                        "jsonrpc": jsonrpc,
                        "id": msg_id,
                        "result": {"tools": mcp_tools},
                    },
                    status_code=200,
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
        _req_id = str(msg_id) if msg_id is not None else ""
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

        if is_adapter_transport and server_config:
            # ── D5 (E12 FIX 2): the adapter transport path (stdio / websocket)
            # calls the MCP server directly and bypasses the backend
            # MCPToolCallView. Inbound arg-scanning already ran above; the
            # OUTBOUND tool-result scan + redaction (the same two-tier pipeline
            # the streamable-http path uses) now runs below before returning, so
            # secrets/PII in stdio/ws tool RESULTS are redacted (or blocked) and
            # never returned raw. Residual limitation: G8 per-user/role
            # field-level policy filtering (MCPToolCallView's RBAC field masks)
            # still only fires on the backend HTTP path — tracked as follow-up.
            LOG.info(
                "mcp_proxy.adapter_transport_gateway_scan org=%s server=%s tool=%s transport=%s",
                org_slug, server_slug, tool_name, transport,
            )
            adapter_resp = await _adapter_forward(
                transport, server_config, org_slug, server_slug, body, jsonrpc, msg_id,
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
            _outbound_hit = False
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
                    )
                )
                if _out_tags_new or _out_find_new:
                    _outbound_hit = True
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
                    elif _scanned_out is not _scan_target and "result" in payload:
                        decision = "redact"
                        payload["result"] = _scanned_out
                        adapter_resp = JSONResponse(content=payload, status_code=200)
                    elif (
                        "result" in payload
                        and _mcp_redact_result_on_detect_enabled()
                        and _scan_action != "monitor"
                        and _findings_have_secret_or_pii(_out_find_new)
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
                        )
                        if _scanned_floor is not _scan_target:
                            decision = "redact"
                            payload["result"] = _scanned_floor
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
                    # Direction the violation (if any) occurred in; clean calls
                    # attribute to the inbound argument scan.
                    "scan_direction": "outbound" if _outbound_hit else "inbound",
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
                        # Direction the violation (if any) occurred in: outbound
                        # when the response scan blocked/found/redacted content
                        # (or the backend reported output redaction), otherwise
                        # attributed to the inbound argument scan.
                        "scan_direction": (
                            "outbound"
                            if (
                                _out_blocked2
                                or _out_find_new2
                                or _scanned_content is not result_content
                                or bool(data.get("redacted"))
                            )
                            else "inbound"
                        ),
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
                        and _findings_have_secret_or_pii(_out_find_new2)
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
                        if _floor_content is not result_content:
                            _scanned_content = _floor_content
                            _merged_meta["result_redaction_floor"] = True
                    # The orchestrator already applied any per-tier redaction and
                    # returns the mutated content; swap it in whenever it changed.
                    # The backend signals its own output redaction via the
                    # 'redacted' response flag (its scan sees the raw tool
                    # output; the gateway only ever sees post-redaction text).
                    _was_redacted = (
                        _in_redacted
                        or bool(data.get("redacted"))
                        or (_scanned_content is not result_content)
                    )
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
                    await _record_gateway_event(
                        org_slug=org_slug,
                        server_slug=server_slug,
                        tool_name=tool_name,
                        decision="error",
                        reason=f"backend_error_http_{resp.status_code}",
                        request_id=_req_id,
                        latency_ms=int((time.time() - call_t0) * 1000),
                        metadata={
                            "transport": transport,
                            "enforced_at": "gateway",
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
    err = _validate_org_scope(request, org_slug)
    if err:
        return err

    body = await request.body()

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
    err = _validate_org_scope(request, org_slug)
    if err:
        return err

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{_BACKEND_URL}/api/mcp-connector/tools/",
                headers=_backend_proxy_headers(request, org_slug, server_slug),
            )
            data = resp.json()
            # Filter disabled tools so REST clients see the same view as JSON-RPC.
            enabled_info = await _get_enabled_tools(org_slug, server_slug)
            if enabled_info and isinstance(data, list):
                data = _filter_tools_by_enabled(data, enabled_info)
            elif enabled_info and isinstance(data, dict) and isinstance(data.get("results"), list):
                data["results"] = _filter_tools_by_enabled(data["results"], enabled_info)
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
    err = _validate_org_scope(request, org_slug)
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
