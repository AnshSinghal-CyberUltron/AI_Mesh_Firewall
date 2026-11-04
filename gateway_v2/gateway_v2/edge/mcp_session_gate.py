"""HTTP-facing gates for MCP: `Origin`, `MCP-Protocol-Version`, `Mcp-Session-Id` (GW32).

Pure functions over the ASGI scope headers. `Origin` is only a browser DNS-rebinding defence (it is
compared with the client-supplied `Host`); it is not an authentication control and nothing relies
on it as one (DD-13).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from gateway_v2.edge.asgi_sse import ASGIScope
from gateway_v2.edge.wire.mcp import DEFAULT_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS

__all__ = ("check_origin", "check_protocol_version", "header", "session_header")

_SESSION_HEADER_MAX = 128


def header(scope: ASGIScope, name: str) -> str | None:
    """The first header called ``name`` (lower-case), latin-1 decoded; ``None`` if absent."""
    raw = scope.get("headers")
    if not isinstance(raw, (list, tuple)):
        return None
    wanted = name.encode("latin-1")
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == wanted:
            value = item[1]
            if isinstance(value, (bytes, bytearray)):
                return bytes(value).decode("latin-1")
    return None


def _authority(url: str) -> str | None:
    try:
        parts = urlsplit(url if "//" in url else f"//{url}")
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    return f"{host}:{port}" if port else host


def check_origin(scope: ASGIScope, allowed: frozenset[str]) -> bool:
    """Absent `Origin` passes. Present: same authority as `Host`, or an exact allow-list entry.
    An unparsable value or ``null`` is refused."""
    origin = header(scope, "origin")
    if origin is None:
        return True
    if origin in allowed:
        return True
    origin_auth = _authority(origin) if "//" in origin else None
    host = header(scope, "host")
    host_auth = _authority(host) if host else None
    return origin_auth is not None and origin_auth == host_auth


def check_protocol_version(scope: ASGIScope) -> str | None:
    """The negotiated version, or ``None`` when the header names an unsupported one.
    A missing header means the spec default (DD-12)."""
    value = header(scope, "mcp-protocol-version")
    if value is None:
        return DEFAULT_PROTOCOL_VERSION
    return value if value in SUPPORTED_PROTOCOL_VERSIONS else None


def session_header(scope: ASGIScope) -> str | None:
    """The `Mcp-Session-Id` header if it is visible ASCII within the bound, else ``""`` (invalid).
    ``None`` = no header."""
    value = header(scope, "mcp-session-id")
    if value is None:
        return None
    if not value or len(value) > _SESSION_HEADER_MAX:
        return ""
    return value if all(0x21 <= ord(ch) <= 0x7E for ch in value) else ""
