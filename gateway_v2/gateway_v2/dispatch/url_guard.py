"""Gateway-side SSRF and stdio checks for MCP upstreams (GW17, task 2.1).

Ported from v1 ``gateway/ai_mesh_gateway/_url_guard.py:59-196``: the same schemes,
the same userinfo rejection, the same blocked address classes, resolve-then-check
on every resolved address, and fail closed on a DNS failure. The sandbox agent runs
its own check as well; this one runs before the broker is called.

Two deliberate differences from v1, both stricter:

* An allowlisted host is still resolved, and still refused if it resolves to a
  metadata, link-local, loopback, reserved, multicast or unspecified address. It is
  only allowed to resolve to a private address. v1 returned OK before any IP check.
* An empty allowlist allows nothing. v1's agent-side flag disabled the check for any
  non-empty value, including ``"false"``.

DNS goes through an injected async resolver, so the event loop is never blocked and
tests need no network. The returned reason is for logs and tests only; a caller
renders a declared value-code, never the reason.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlparse

__all__ = (
    "REMOTE_SCHEMES",
    "Resolver",
    "check_remote",
    "check_stdio",
    "default_resolver",
    "parse_allow_hosts",
)

#: Schemes a remote MCP transport may use (streamable-http / sse / websocket).
REMOTE_SCHEMES = frozenset({"http", "https", "ws", "wss"})

_METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})
_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")

#: ``(host, port) -> resolved address strings``. Raises on resolution failure.
Resolver = Callable[[str, int | None], Awaitable[Sequence[str]]]


async def default_resolver(host: str, port: int | None) -> Sequence[str]:
    """Resolve ``host`` with the running loop's non-blocking ``getaddrinfo``."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return tuple(str(info[4][0]) for info in infos)


def parse_allow_hosts(raw: str) -> frozenset[str]:
    """Exact ``host`` or ``host:port`` entries, lower-cased. Blank entries are ignored."""
    return frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())


def _blocked(ip_str: str, *, block_private: bool) -> str | None:
    """The reason ``ip_str`` is refused, or ``None``. v1 ``_ip_is_blocked`` rules."""
    if ip_str in _METADATA_IPS:
        return "cloud metadata endpoint"
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return "unparseable resolved address"
    if ip.version == 4 and ip in _LINK_LOCAL_V4:
        return "link-local range 169.254.0.0/16"
    if block_private and ip.is_private:
        return "private address"
    if ip.is_loopback:
        return "loopback address"
    if ip.is_link_local:
        return "link-local address"
    if ip.is_reserved:
        return "reserved address"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_unspecified:
        return "unspecified address"
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _blocked(str(mapped), block_private=block_private)
    return None


def _allowlisted(host: str, port: int | None, allow_hosts: frozenset[str]) -> bool:
    if not allow_hosts:
        return False
    name = host.strip().lower()
    return name in allow_hosts or (port is not None and f"{name}:{port}" in allow_hosts)


async def check_remote(
    url: str,
    *,
    resolver: Resolver,
    allow_hosts: frozenset[str] = frozenset(),
) -> str | None:
    """``None`` when ``url`` is safe to forward, else the refusal reason. Never raises."""
    try:
        parsed = urlparse(url.strip())
        port = parsed.port
    except (ValueError, AttributeError):
        return "unparseable URL"
    if (parsed.scheme or "").lower() not in REMOTE_SCHEMES:
        return "scheme not allowed"
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return "userinfo not allowed"
    host = parsed.hostname
    if not host:
        return "no host"
    block_private = not _allowlisted(host, port, allow_hosts)
    literal = host.strip("[]")
    try:
        ipaddress.ip_address(literal)
    except ValueError:
        pass
    else:
        return _blocked(literal, block_private=block_private)
    try:
        addresses = await resolver(host, port)
    except Exception:  # noqa: BLE001 - fail closed on any resolver error
        return "DNS resolution failed"
    if not addresses:
        return "no addresses resolved"
    for address in addresses:
        reason = _blocked(address, block_private=block_private)
        if reason is not None:
            return reason
    return None


def check_stdio(command: str, allowed: frozenset[str]) -> str | None:
    """``None`` when ``command`` is a bare allowlisted name, else the refusal reason."""
    name = command.strip()
    if not name:
        return "empty command"
    if "/" in name or "\\" in name:
        return "path-qualified command"
    if name not in allowed:
        return "command not allowlisted"
    return None
