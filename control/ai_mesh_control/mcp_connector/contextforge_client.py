"""HTTP client for IBM ContextForge (MCP Context Forge) API.

Proxies MCP server registration, tool discovery, and federation management
to the ContextForge service running at CONTEXTFORGE_URL.
"""

import logging
import os
import threading
import time
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("CONTEXTFORGE_URL", "http://contextforge:4444")
_TIMEOUT = int(os.environ.get("CONTEXTFORGE_TIMEOUT", "15"))
_LOGIN_EMAIL = os.environ.get("CONTEXTFORGE_LOGIN_EMAIL", "admin@example.com")
_LOGIN_PASSWORD = os.environ.get("CONTEXTFORGE_LOGIN_PASSWORD", "changeme")
_USE_AUTH = os.environ.get("CONTEXTFORGE_USE_AUTH", "false").lower() in {"1", "true", "yes"}
_TOKEN_CACHE: str = ""
_TOKEN_CACHE_AT: float = 0.0
_TOKEN_CACHE_TTL = max(1, int(os.environ.get("CONTEXTFORGE_TOKEN_CACHE_TTL", "300")))
_TOKEN_CACHE_LOCK = threading.RLock()

# Gateway proxy URL for routing external MCP traffic through the gateway's
# working TLS stack (avoids OpenSSL 3.5.1 BAD_SIGNATURE in ContextForge container).
_GATEWAY_EXT_PROXY_BASE = os.environ.get(
    "GATEWAY_EXT_PROXY_BASE", "http://gateway:8300/v1/mcp/ext-proxy"
)
_GATEWAY_EXT_PROXY_ALLOWED_DOMAINS = {
    host.strip().lower()
    for host in os.environ.get(
        "GATEWAY_EXT_PROXY_ALLOWED_DOMAINS",
        "mcp.context7.com,api.githubcopilot.com,mcp.linear.app",
    ).split(",")
    if host.strip()
}


def _login_token() -> str:
    """Obtain a short-lived JWT from ContextForge email auth."""
    global _TOKEN_CACHE, _TOKEN_CACHE_AT
    with _TOKEN_CACHE_LOCK:
        if _TOKEN_CACHE and (time.time() - _TOKEN_CACHE_AT) < _TOKEN_CACHE_TTL:
            return _TOKEN_CACHE

    login_url = _url("/auth/login")
    payload = {"email": _LOGIN_EMAIL, "password": _LOGIN_PASSWORD}
    resp = requests.post(login_url, json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    token = data.get("access_token", "") if isinstance(data, dict) else ""

    with _TOKEN_CACHE_LOCK:
        if token:
            _TOKEN_CACHE = token
            _TOKEN_CACHE_AT = time.time()
        else:
            _TOKEN_CACHE = ""
            _TOKEN_CACHE_AT = 0.0
    return token


def _clear_cached_token() -> None:
    """Clear cached dynamic login token (used after auth failures)."""
    global _TOKEN_CACHE, _TOKEN_CACHE_AT
    with _TOKEN_CACHE_LOCK:
        _TOKEN_CACHE = ""
        _TOKEN_CACHE_AT = 0.0


def _resolve_authorization_value() -> str:
    """Resolve an Authorization header value for ContextForge, if available."""
    token = os.environ.get("CONTEXTFORGE_BEARER_TOKEN", "")
    if token:
        return f"Bearer {token}"

    # Fall back to runtime login-based JWT when static bearer token is not set.
    # If login is unavailable, we still attempt basic auth as a last resort.
    try:
        dynamic_token = _login_token()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "unknown")
        logger.info(
            "ContextForge dynamic token login unavailable (status=%s, exc=%s); falling back to basic auth",
            status,
            type(exc).__name__,
        )
        dynamic_token = ""

    if dynamic_token:
        return f"Bearer {dynamic_token}"

    user = os.environ.get("CONTEXTFORGE_BASIC_USER", "admin")
    password = os.environ.get("CONTEXTFORGE_BASIC_PASSWORD", "changeme")
    return f"Basic {_b64(user, password)}"


def _headers(*, force_auth: bool = False) -> dict:
    """Build headers for ContextForge API.

    When ``force_auth`` is true, include Authorization even if CONTEXTFORGE_USE_AUTH
    is disabled. This allows recovery from upstream auth-policy drift.
    """
    h = {"Content-Type": "application/json"}
    if not (_USE_AUTH or force_auth):
        return h

    h["Authorization"] = _resolve_authorization_value()
    return h


def _contextforge_request(method: str, path: str, *, timeout: int | None = None, **kwargs):
    """Send a request to ContextForge with an auth-retry fallback.

    If the first request is unauthenticated and ContextForge returns a 401
    "Authorization token required", retry once with forced auth headers.
    """
    request_timeout = _TIMEOUT if timeout is None else timeout
    url = _url(path)
    headers = kwargs.pop("headers", None) or _headers()

    resp = requests.request(method, url, headers=headers, timeout=request_timeout, **kwargs)
    try:
        response_text = (resp.text or "").lower()
    except Exception:
        response_text = ""

    should_retry_auth = (
        resp.status_code == 401
        and "authorization token required" in response_text
        and "Authorization" not in headers
    )
    if should_retry_auth:
        _clear_cached_token()
        retry_headers = _headers(force_auth=True)
        if "Authorization" in retry_headers:
            logger.warning(
                "ContextForge returned 401 without auth header; retrying with configured credentials"
            )
            resp = requests.request(
                method,
                url,
                headers=retry_headers,
                timeout=request_timeout,
                **kwargs,
            )

    return resp


def _b64(user: str, password: str) -> str:
    import base64

    return base64.b64encode(f"{user}:{password}".encode()).decode()


def _url(path: str) -> str:
    return urljoin(_BASE_URL.rstrip("/") + "/", path.lstrip("/"))


def _normalize_transport(transport: str) -> str:
    """Map UI transport values to ContextForge Gateway transport enum values."""
    v = (transport or "").strip().lower()
    if v in {"streamable-http", "streamable_http", "streamablehttp"}:
        return "STREAMABLEHTTP"
    if v == "sse":
        return "SSE"
    raise ValueError(
        "Unsupported transport for ContextForge gateway. Use streamable-http or sse."
    )


def _normalize_auth_headers(payload: dict) -> list[dict[str, str]]:
    """Normalize auth headers into a deduped list of {key, value} maps."""
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in payload.get("auth_headers") or []:
        if not isinstance(item, dict):
            continue
        key = (item.get("key") or "").strip()
        value = item.get("value")
        if not key or value in (None, ""):
            continue
        key_norm = key.lower()
        if key_norm in seen:
            continue
        seen.add(key_norm)
        normalized.append({"key": key, "value": str(value)})

    single_key = (payload.get("auth_header_key") or "").strip()
    single_value = payload.get("auth_header_value")
    if single_key and single_value not in (None, ""):
        key_norm = single_key.lower()
        if key_norm not in seen:
            normalized.insert(0, {"key": single_key, "value": str(single_value)})

    return normalized


def _rewrite_url_for_proxy(url: str) -> str:
    """Rewrite external MCP URLs to route through the gateway ext-proxy.

    The ContextForge container (RHEL10 / OpenSSL 3.5.1) cannot connect to
    some external HTTPS endpoints due to BAD_SIGNATURE errors.  The gateway
    container (OpenSSL 3.5.5) does not have this issue so we route the
    connection through ``/v1/mcp/ext-proxy/{hostname}/{path}``.

    Only rewrites ``https://`` URLs whose hostname is in the gateway's
    allow-list.  Internal / ``http://`` URLs are returned as-is.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return url

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return url

    # Only rewrite hosts explicitly allowlisted by the gateway ext-proxy.
    # For all other hosts, preserve direct URL to avoid guaranteed 403 relays.
    if hostname not in _GATEWAY_EXT_PROXY_ALLOWED_DOMAINS:
        return url

    proxied_path = f"{hostname}{parsed.path}"
    if parsed.query:
        proxied_path += f"?{parsed.query}"
    return f"{_GATEWAY_EXT_PROXY_BASE}/{proxied_path}"


def _to_gateway_create_payload(payload: dict) -> dict:
    """Convert local MCP registration payload into ContextForge GatewayCreate shape."""
    raw_url = payload.get("url", "")
    proxied_url = _rewrite_url_for_proxy(raw_url)
    out = {
        "name": payload.get("name"),
        "url": proxied_url,
        "description": payload.get("description") or "",
        "transport": _normalize_transport(payload.get("transport", "streamable-http")),
    }

    auth_type = (payload.get("auth_type") or "").strip().lower()
    if auth_type and auth_type != "none":
        out["auth_type"] = auth_type
        if auth_type == "bearer" and payload.get("auth_token"):
            out["auth_token"] = payload.get("auth_token")
        elif auth_type == "basic":
            out["auth_username"] = payload.get("auth_username", "")
            out["auth_password"] = payload.get("auth_password", "")
        elif auth_type == "authheaders":
            auth_headers = _normalize_auth_headers(payload)
            if auth_headers:
                out["auth_headers"] = [
                    {"key": h["key"], "value": h["value"]}
                    for h in auth_headers
                ]
                out["auth_header_key"] = auth_headers[0]["key"]
                out["auth_header_value"] = auth_headers[0]["value"]
        elif auth_type == "query_param":
            out["auth_query_param_key"] = payload.get("auth_query_param_key", "")
            out["auth_query_param_value"] = payload.get("auth_query_param_value", "")
    return out


def _normalized_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def resolve_existing_server(payload: dict) -> dict | None:
    """Resolve an existing ContextForge gateway when create returns conflict.

    ContextForge can reject duplicate gateway creation in public scope. In that
    case, we look up a matching existing gateway by normalized URL first, then
    by name/slug to bind local org registrations without failing the flow.
    """
    target = _to_gateway_create_payload(payload)
    target_url = _normalized_url(target.get("url", ""))
    target_name = (target.get("name") or "").strip().lower()

    try:
        servers = list_servers()
    except requests.RequestException as exc:
        logger.warning("ContextForge list_servers failed while resolving duplicate: %s", exc)
        return None

    if not isinstance(servers, list):
        return None

    for server in servers:
        if not isinstance(server, dict):
            continue

        server_url = _normalized_url(server.get("url", ""))
        if target_url and server_url == target_url:
            return server

        server_name = (server.get("name") or "").strip().lower()
        server_slug = (server.get("slug") or "").strip().lower()
        if target_name and (server_name == target_name or server_slug == target_name):
            return server

    return None


def _to_gateway_update_payload(payload: dict) -> dict:
    """Convert partial local update payload into ContextForge GatewayUpdate shape."""
    out = {}
    if "name" in payload:
        out["name"] = payload.get("name")
    if "url" in payload:
        out["url"] = payload.get("url")
    if "description" in payload:
        out["description"] = payload.get("description")
    if "transport" in payload:
        out["transport"] = _normalize_transport(payload.get("transport", ""))

    auth_type = (payload.get("auth_type") or "").strip().lower()
    if auth_type:
        out["auth_type"] = auth_type
    if "auth_token" in payload:
        out["auth_token"] = payload.get("auth_token")
    if "auth_username" in payload:
        out["auth_username"] = payload.get("auth_username")
    if "auth_password" in payload:
        out["auth_password"] = payload.get("auth_password")
    if "auth_header_key" in payload:
        out["auth_header_key"] = payload.get("auth_header_key")
    if "auth_header_value" in payload:
        out["auth_header_value"] = payload.get("auth_header_value")
    if (
        "auth_headers" in payload
        or "auth_header_key" in payload
        or "auth_header_value" in payload
    ):
        auth_headers = _normalize_auth_headers(payload)
        out["auth_headers"] = [
            {"key": h["key"], "value": h["value"]}
            for h in auth_headers
        ] if auth_headers else []
        if auth_headers:
            out["auth_header_key"] = auth_headers[0]["key"]
            out["auth_header_value"] = auth_headers[0]["value"]
    if "auth_query_param_key" in payload:
        out["auth_query_param_key"] = payload.get("auth_query_param_key")
    if "auth_query_param_value" in payload:
        out["auth_query_param_value"] = payload.get("auth_query_param_value")

    # Do not send null fields to ContextForge for partial updates.
    out = {k: v for k, v in out.items() if v is not None}
    return out


# ── Health ────────────────────────────────────────────────────────────
def health() -> dict:
    """Check ContextForge health."""
    try:
        resp = _contextforge_request("GET", "/health")
        return {"status": "healthy" if resp.ok else "unhealthy", "detail": resp.json() if resp.ok else resp.text}
    except requests.RequestException as exc:
        logger.warning("ContextForge health check failed: %s", exc)
        return {"status": "unreachable", "detail": str(exc)}


def version() -> dict:
    """Get ContextForge version info."""
    try:
        resp = _contextforge_request("GET", "/version")
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.warning("ContextForge version check failed: %s", exc)
        return {"error": str(exc)}


# ── Servers ───────────────────────────────────────────────────────────
def list_servers() -> list:
    """List all registered MCP gateways from ContextForge."""
    resp = _contextforge_request("GET", "/gateways")
    resp.raise_for_status()
    return resp.json()


def get_server(server_id: str) -> dict:
    """Get a single MCP gateway by ID."""
    resp = _contextforge_request("GET", f"/gateways/{server_id}")
    resp.raise_for_status()
    return resp.json()


def register_server(payload: dict) -> dict:
    """Register a new MCP gateway with ContextForge."""
    resp = _contextforge_request(
        "POST",
        "/gateways",
        json=_to_gateway_create_payload(payload),
    )
    resp.raise_for_status()
    gateway = resp.json()
    gateway_id = gateway.get("id") if isinstance(gateway, dict) else None
    if gateway_id:
        # Trigger discovery immediately so tools become available in the UI flow.
        refresh_gateway_tools(gateway_id)
    return gateway


def update_server(server_id: str, payload: dict) -> dict:
    """Update an existing MCP gateway registration."""
    resp = _contextforge_request(
        "PUT",
        f"/gateways/{server_id}",
        json=_to_gateway_update_payload(payload),
    )
    resp.raise_for_status()
    gateway = resp.json()
    refresh_gateway_tools(server_id)
    return gateway


def delete_server(server_id: str) -> dict:
    """Delete / unregister an MCP gateway."""
    resp = _contextforge_request("DELETE", f"/gateways/{server_id}")
    resp.raise_for_status()
    return {"deleted": True, "server_id": server_id}


def refresh_gateway_tools(gateway_id: str) -> dict:
    """Refresh discovered tools for a specific gateway."""
    resp = _contextforge_request(
        "POST",
        f"/gateways/{gateway_id}/tools/refresh",
        timeout=max(_TIMEOUT, 45),
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {"ok": True}


# ── Tools ─────────────────────────────────────────────────────────────
def list_tools() -> list:
    """List all available tools across all federated MCP servers."""
    resp = _contextforge_request("GET", "/tools")
    resp.raise_for_status()
    return resp.json()


def call_tool(
    tool_name: str,
    arguments: dict,
    *,
    server_id: str = "",
    server_name: str = "",
) -> dict:
    """Invoke a tool via direct MCP Streamable HTTP through the gateway ext-proxy.

    Resolves the tool's MCP server URL from ContextForge tool metadata,
    then performs the full MCP handshake (initialize → initialized → tools/call)
    against the upstream server via the gateway ext-proxy.
    """
    # Resolve the tool's upstream MCP URL and original name from ContextForge.
    tools = list_tools()
    match = None
    server_id = (server_id or "").strip()
    server_name = (server_name or "").strip().lower()
    if isinstance(tools, list):
        for t in tools:
            if not isinstance(t, dict):
                continue
            if t.get("name") != tool_name:
                continue

            if server_id or server_name:
                t_server_id = (
                    t.get("server_id")
                    or t.get("serverId")
                    or t.get("gateway_id")
                    or t.get("gatewayId")
                    or ""
                )
                t_server_name = (
                    t.get("server_name")
                    or t.get("server")
                    or t.get("gateway_name")
                    or t.get("gateway")
                    or ""
                )
                id_matches = bool(server_id) and str(t_server_id) == server_id
                name_matches = bool(server_name) and str(t_server_name).strip().lower() == server_name
                if not (id_matches or name_matches):
                    continue

            match = t
            break
    if not match:
        raise requests.RequestException(f"Tool '{tool_name}' not found in ContextForge")

    mcp_url = match.get("url", "")
    original_name = match.get("originalName", tool_name)
    if not mcp_url:
        raise requests.RequestException(f"Tool '{tool_name}' has no URL in ContextForge")

    mcp_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    timeout = max(_TIMEOUT, 60)

    # Step 1: MCP initialize
    init_resp = requests.post(mcp_url, headers=mcp_headers, json={
        "jsonrpc": "2.0", "id": "init-1", "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "zeroshield-connector", "version": "1.0"},
        },
    }, timeout=timeout)
    init_resp.raise_for_status()
    session_id = init_resp.headers.get("mcp-session-id", "")

    # Step 2: Send initialized notification
    notif_headers = {**mcp_headers}
    if session_id:
        notif_headers["mcp-session-id"] = session_id
    notif_resp = requests.post(mcp_url, headers=notif_headers, json={
        "jsonrpc": "2.0", "method": "notifications/initialized",
    }, timeout=timeout)
    notif_resp.raise_for_status()

    # Step 3: tools/call with the tool's original name
    call_headers = {**mcp_headers}
    if session_id:
        call_headers["mcp-session-id"] = session_id
    call_resp = requests.post(mcp_url, headers=call_headers, json={
        "jsonrpc": "2.0", "id": "call-1", "method": "tools/call",
        "params": {"name": original_name, "arguments": arguments or {}},
    }, timeout=timeout)
    call_resp.raise_for_status()
    return call_resp.json() if call_resp.content else {"ok": True}
