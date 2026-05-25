"""Gateway proxy routes for MCP traffic via ContextForge + Secure-MCP-Gateway.

Provides transparent proxying from the gateway's data plane to the two OSS
MCP services so that frontend/clients can reach them through a single endpoint.

Also provides an external MCP proxy so that ContextForge (which may have
OpenSSL compatibility issues) can reach external MCP servers through the
gateway container's working TLS stack.

Additionally provides org-scoped external gateway routes at
/gateway/{org_slug}/mcp/{server_slug}/* for agent/SDK consumption.
"""

import json
import logging
import os
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from jobs import enqueue_job

LOG = logging.getLogger("gateway.mcp_proxy")

router = APIRouter(prefix="/v1/mcp", tags=["MCP Proxy"])

# Org-scoped external gateway router — auth IS enforced on this router.
org_gateway_router = APIRouter(prefix="/gateway", tags=["MCP Org Gateway"])

_CONTEXTFORGE_URL = os.environ.get("CONTEXTFORGE_URL", "http://contextforge:4444")
_SECURE_GW_URL = os.environ.get("SECURE_MCP_GATEWAY_URL", "http://secure-mcp-gateway:8000")
_MCP_FIREWALL_URL = os.environ.get("MCP_FIREWALL_URL", "http://mcp-firewall:8080")
_BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")
_GATEWAY_INTERNAL_API_KEY = os.environ.get(
    "GATEWAY_INTERNAL_API_KEY", os.environ.get("AGENT_API_KEY", "")
)
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
# failure, value is None -> fail-open (allow all) for the TTL window.
_enabled_tools_cache: dict[str, dict | None] = {}
_enabled_tools_ttl: dict[str, float] = {}
_ENABLED_TOOLS_TTL = float(os.environ.get("MCP_ENABLED_TOOLS_TTL", "30"))


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

    headers = {
        "Content-Type": "application/json",
        "X-Org-Slug": org_slug,
        "X-Gateway-Auth": "true",
    }
    if _GATEWAY_INTERNAL_API_KEY:
        headers["X-Gateway-Internal-Key"] = _GATEWAY_INTERNAL_API_KEY

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{_BACKEND_URL}/api/mcp-connector/servers/",
                headers=headers,
            )
            if resp.status_code != 200:
                LOG.warning("Server config lookup failed: HTTP %s", resp.status_code)
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


# ── Per-tool enable/disable enforcement (works for ALL transports) ──

async def _get_enabled_tools(org_slug: str, server_slug: str) -> dict | None:
    """Fetch (and cache) enabled/disabled/known tool sets for a server.

    Returns a dict {"known": set, "enabled": set, "disabled": set} or
    None if the backend lookup failed (caller should fail-open).
    """
    if not org_slug or not server_slug:
        return None
    cache_key = f"{org_slug}/{server_slug}"
    now = time.time()
    if cache_key in _enabled_tools_cache:
        if now - _enabled_tools_ttl.get(cache_key, 0) < _ENABLED_TOOLS_TTL:
            return _enabled_tools_cache[cache_key]

    headers = {
        "Content-Type": "application/json",
        "X-Org-Slug": org_slug,
        "X-Server-Slug": server_slug,
        "X-Gateway-Auth": "true",
    }
    if _GATEWAY_INTERNAL_API_KEY:
        headers["X-Gateway-Internal-Key"] = _GATEWAY_INTERNAL_API_KEY
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{_BACKEND_URL}/api/mcp-connector/internal/enabled-tools/",
                headers=headers,
                params={"server_slug": server_slug},
            )
            if resp.status_code != 200:
                LOG.warning(
                    "Enabled-tools lookup failed (org=%s server=%s): HTTP %s",
                    org_slug, server_slug, resp.status_code,
                )
                _enabled_tools_cache[cache_key] = None
                _enabled_tools_ttl[cache_key] = now
                return None
            data = resp.json() or {}
            result = {
                "known": set(data.get("known_tools") or []),
                "enabled": set(data.get("enabled_tools") or []),
                "disabled": set(data.get("disabled_tools") or []),
            }
            _enabled_tools_cache[cache_key] = result
            _enabled_tools_ttl[cache_key] = now
            return result
    except Exception as exc:
        LOG.warning("Enabled-tools lookup error (org=%s server=%s): %s",
                    org_slug, server_slug, exc)
        _enabled_tools_cache[cache_key] = None
        _enabled_tools_ttl[cache_key] = now
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
            },
        )
        return

    headers = {
        "Content-Type": "application/json",
        "X-Org-Slug": org_slug,
        "X-Server-Slug": server_slug or "",
        "X-Gateway-Auth": "true",
    }
    if _GATEWAY_INTERNAL_API_KEY:
        headers["X-Gateway-Internal-Key"] = _GATEWAY_INTERNAL_API_KEY
    payload = {
        "server_slug": server_slug,
        "tool_name": tool_name,
        "decision": decision,
        "reason": reason,
        "request_id": request_id,
        "latency_ms": latency_ms,
        "metadata": metadata or {},
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


# ── ContextForge proxy ───────────────────────────────────────────────

@router.api_route(
    "/contextforge/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Proxy to ContextForge API",
)
async def contextforge_proxy(path: str, request: Request):
    """Transparent proxy to IBM ContextForge for server/tool management."""
    return await _proxy(_CONTEXTFORGE_URL, path, request)


# ── Secure MCP Gateway proxy ────────────────────────────────────────

@router.api_route(
    "/secure-gateway/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Proxy to Secure MCP Gateway API",
)
async def secure_gateway_proxy(path: str, request: Request):
    """Transparent proxy to Enkrypt Secure MCP Gateway for guardrails."""
    return await _proxy(_SECURE_GW_URL, path, request)


# ── Health (aggregated) ──────────────────────────────────────────────

@router.get("/health", summary="MCP services health check")
async def mcp_health():
    """Check health of all MCP infrastructure services.

    - ContextForge: standard REST /health endpoint.
    - Secure MCP Gateway: MCP Streamable HTTP at /mcp/ (returns 400/406 when alive).
    - MCP-Firewall: CLI tool, not a standalone service — reports not_configured if unreachable.
    """
    results = {}
    async with httpx.AsyncClient(timeout=5) as client:
        # ContextForge — standard REST health endpoint
        try:
            resp = await client.get(f"{_CONTEXTFORGE_URL}/health")
            results["contextforge"] = {"status": "healthy" if resp.is_success else "unhealthy"}
        except httpx.RequestError:
            results["contextforge"] = {"status": "unreachable"}

        # Secure MCP Gateway — probe MCP Streamable HTTP endpoint
        # Any HTTP response (200/400/405/406) means the service is alive
        try:
            resp = await client.get(f"{_SECURE_GW_URL}/mcp/")
            if resp.status_code in (200, 400, 405, 406):
                results["secure_mcp_gateway"] = {"status": "healthy"}
            else:
                results["secure_mcp_gateway"] = {"status": "unhealthy"}
        except httpx.RequestError:
            results["secure_mcp_gateway"] = {"status": "unreachable"}

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
# Allows ContextForge (which may have OpenSSL issues) to reach external
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
            return JSONResponse(content=data, status_code=resp.status_code, headers=resp_headers)
        except Exception:
            from starlette.responses import Response
            return Response(
                content=body_bytes,
                status_code=resp.status_code,
                media_type=content_type,
                headers=resp_headers,
            )
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
# Called by the backend during tool sync for servers that skip ContextForge
# (stdio, websocket, or any server where ContextForge discovery fails).
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
    if not internal_key or not _GATEWAY_INTERNAL_API_KEY or internal_key != _GATEWAY_INTERNAL_API_KEY:
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
    if not internal_key or not _GATEWAY_INTERNAL_API_KEY or internal_key != _GATEWAY_INTERNAL_API_KEY:
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
                                return JSONResponse(content=json.loads(data_str), status_code=200)
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

            return JSONResponse(content=call_resp.json(), status_code=200)
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
    headers = {
        "Content-Type": "application/json",
        "X-Org-Slug": org_slug,
        "X-Gateway-Auth": "true",
    }
    if server_slug:
        headers["X-Server-Slug"] = server_slug
    if _GATEWAY_INTERNAL_API_KEY:
        headers["X-Gateway-Internal-Key"] = _GATEWAY_INTERNAL_API_KEY

    auth = _get_auth_context(request)
    if auth is not None:
        if getattr(auth, "user_id", None) is not None:
            headers["X-Gateway-User-Id"] = str(auth.user_id)
        if getattr(auth, "prefix", None):
            headers["X-Gateway-Key-Prefix"] = str(auth.prefix)
        if getattr(auth, "project_id", None):
            headers["X-Gateway-Project-Id"] = str(auth.project_id)

    return headers


async def _maybe_inject_oauth_header(args: list[str], org_slug: str) -> None:
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
        from mcp_oauth_proxy import get_stored_token
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
            await _maybe_inject_oauth_header(args, org_slug)

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

        if is_adapter_transport and server_config:
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
            if isinstance(payload, dict) and payload.get("error"):
                decision = "error"
                reason = str(payload["error"].get("message", ""))[:255]
            await _record_gateway_event(
                org_slug=org_slug,
                server_slug=server_slug,
                tool_name=tool_name,
                decision=decision,
                reason=reason,
                latency_ms=int((time.time() - call_t0) * 1000),
                metadata={"transport": transport, "enforced_at": "gateway_adapter"},
            )
            return adapter_resp

        async with httpx.AsyncClient(timeout=max(_TIMEOUT, 60)) as client:
            try:
                resp = await client.post(
                    f"{_BACKEND_URL}/api/mcp-connector/tools/call/",
                    headers=_backend_proxy_headers(request, org_slug, server_slug),
                    json={
                        "name": tool_name,
                        "arguments": arguments,
                        "server_slug": server_slug,
                    },
                )
                data = resp.json()

                # Adapt backend response to MCP JSON-RPC response
                if resp.status_code == 200 and isinstance(data, dict):
                    # Check for policy-blocked responses
                    if data.get("blocked"):
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

                    # Normal successful response
                    result_content = data.get("result") or data.get("content")
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
    async with httpx.AsyncClient(timeout=max(_TIMEOUT, 60)) as client:
        try:
            resp = await client.post(
                f"{_BACKEND_URL}/api/mcp-connector/tools/call/",
                content=body,
                headers=_backend_proxy_headers(request, org_slug, server_slug),
            )
            data = resp.json()
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
