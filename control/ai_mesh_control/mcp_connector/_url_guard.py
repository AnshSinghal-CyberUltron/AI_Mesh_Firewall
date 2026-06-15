"""SSRF guard for operator-supplied MCP server URLs (finding mcp#1, HIGH).

Self-contained, stdlib-only. Validates that an outbound URL does not point at
an internal/loopback/link-local/cloud-metadata target before the gateway
fetches or connects to it.

Usage:
    ok, reason = is_safe_outbound_url(url)
    if not ok:
        # reject — do NOT fetch
        ...

Escape hatch:
    MCP_ALLOW_INTERNAL_HOSTS — comma-separated ``host`` or ``host:port`` entries
    that are explicitly permitted (for on-prem internal MCP servers). Matching is
    case-insensitive on the hostname; if a port is given it must also match.

Fail-CLOSED: any parse error or DNS (getaddrinfo) failure returns (False, ...).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Cloud metadata endpoints / ranges that must always be blocked even if a
# misconfigured resolver were to claim they are "public".
_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})
_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")


def _allowed_internal_hosts() -> set[str]:
    """Parse MCP_ALLOW_INTERNAL_HOSTS into a normalized set of host[:port]."""
    raw = os.environ.get("MCP_ALLOW_INTERNAL_HOSTS", "") or ""
    out: set[str] = set()
    for entry in raw.split(","):
        e = entry.strip().lower()
        if e:
            out.add(e)
    return out


def _host_is_allowlisted(host: str, port: int | None, allowlist: set[str]) -> bool:
    """True if host (optionally host:port) is explicitly permitted."""
    if not allowlist:
        return False
    h = (host or "").strip().lower()
    if not h:
        return False
    if h in allowlist:
        return True
    if port is not None and f"{h}:{port}" in allowlist:
        return True
    return False


def _ip_is_blocked(ip_str: str) -> tuple[bool, str]:
    """Return (blocked, reason) for a single resolved IP string."""
    if ip_str in _METADATA_IPS:
        return True, f"cloud metadata endpoint ({ip_str})"
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Unparseable address — fail closed.
        return True, f"unparseable resolved address ({ip_str})"

    # Explicit link-local / metadata range guard (covers 169.254.0.0/16).
    if ip.version == 4 and ip in _LINK_LOCAL_V4:
        return True, f"link-local range 169.254.0.0/16 ({ip_str})"

    if ip.is_private:
        return True, f"private address ({ip_str})"
    if ip.is_loopback:
        return True, f"loopback address ({ip_str})"
    if ip.is_link_local:
        return True, f"link-local address ({ip_str})"
    if ip.is_reserved:
        return True, f"reserved address ({ip_str})"
    if ip.is_multicast:
        return True, f"multicast address ({ip_str})"
    if ip.is_unspecified:
        return True, f"unspecified address ({ip_str})"

    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) and re-check.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _ip_is_blocked(str(mapped))

    return False, ""


def is_safe_outbound_url(url: str, allowed_schemes: tuple[str, ...] = ("http", "https")) -> tuple[bool, str]:
    """Validate that ``url`` is safe to fetch/connect to (anti-SSRF).

    Returns ``(ok, reason)``. ``ok`` is True only when:
      * the URL parses and has a scheme in ``allowed_schemes`` and a hostname,
      * every IP the hostname resolves to is a public, routable address,
      * the host is not the cloud-metadata endpoint / 169.254.0.0/16 range.

    Fails CLOSED (returns False) on parse failure or getaddrinfo failure.
    The MCP_ALLOW_INTERNAL_HOSTS env var can explicitly permit internal hosts.
    """
    if not url or not isinstance(url, str):
        return False, "empty or non-string URL"

    try:
        parsed = urlparse(url.strip())
    except Exception as exc:  # noqa: BLE001 - fail closed on any parse error
        return False, f"unparseable URL: {exc}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in {s.lower() for s in allowed_schemes}:
        return False, f"scheme '{scheme or '(none)'}' not allowed (allowed: {', '.join(allowed_schemes)})"

    host = parsed.hostname
    if not host:
        return False, "URL has no host"

    try:
        port = parsed.port
    except ValueError:
        return False, "invalid port in URL"

    allowlist = _allowed_internal_hosts()
    if _host_is_allowlisted(host, port, allowlist):
        return True, "explicitly allowlisted internal host"

    # Literal-IP hosts: check directly (covers bracketed IPv6 too).
    literal_host = host.strip("[]")
    try:
        ipaddress.ip_address(literal_host)
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False

    if is_literal_ip:
        blocked, reason = _ip_is_blocked(literal_host)
        if blocked:
            return False, f"blocked host: {reason}"
        return True, "ok"

    # Resolve the hostname; fail CLOSED on any DNS failure.
    try:
        infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed for '{host}': {exc}"
    except Exception as exc:  # noqa: BLE001 - fail closed
        return False, f"DNS resolution error for '{host}': {exc}"

    if not infos:
        return False, f"no addresses resolved for '{host}'"

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        blocked, reason = _ip_is_blocked(ip_str)
        if blocked:
            return False, f"host '{host}' resolves to blocked address — {reason}"

    return True, "ok"
