"""
SSRF guards for vector-DB provider connection URLs.

Why this exists
---------------
Operators register external vector databases (Milvus, Pinecone, custom) by
posting a ``connection_url`` to the control plane. That URL is later handed
to a Milvus/HTTP client inside the gateway. Without validation, any
authenticated org admin can point the gateway at:

  * cloud metadata services (e.g. ``http://169.254.169.254/...``)
  * loopback / private-RFC1918 hosts that may expose internal admin APIs
  * non-routable / link-local addresses
  * exotic schemes (``file://``, ``gopher://``, ``ftp://`` …) that some HTTP
    clients silently follow

This module centralises the allow/deny logic so both the control plane
serializer (write-time validation) and the gateway runtime resolver
(read-time defence in depth) apply the *same* rules.

Policy
------
* Allowed schemes: ``http``, ``https``, ``grpc``, ``grpcs``.
* Hostname must be present and parse cleanly.
* If the host is an IP literal or resolves to one, the address must be
  global unicast (``ipaddress.is_global``). Loopback, link-local,
  multicast, unspecified, reserved, and private RFC1918 ranges are
  rejected.
* Set ``VECTOR_PROVIDER_ALLOW_PRIVATE=1`` to relax the private-IP check
  (intended for docker-compose dev where Milvus runs at
  ``http://milvus:19530``). Loopback / metadata / link-local are still
  blocked in that mode.

Failure mode
------------
Raises :class:`UnsafeProviderURLError` with a human-readable reason on
rejection. Both call sites translate that into a 4xx for the API client.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse, urlunparse

_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "grpc", "grpcs"})

# Always-blocked addresses regardless of VECTOR_PROVIDER_ALLOW_PRIVATE.
# These are well-known SSRF gadgets (cloud metadata + loopback + IPv4-mapped
# loopback + link-local + unspecified).
_HARD_BLOCK_NETS: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),       # IPv4 loopback
    ipaddress.ip_network("0.0.0.0/8"),         # IPv4 unspecified / "any"
    ipaddress.ip_network("169.254.0.0/16"),    # IPv4 link-local + AWS/GCP metadata
    ipaddress.ip_network("224.0.0.0/4"),       # IPv4 multicast
    ipaddress.ip_network("240.0.0.0/4"),       # IPv4 reserved
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("::/128"),            # IPv6 unspecified
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
    ipaddress.ip_network("fc00::/7"),          # IPv6 unique-local
    ipaddress.ip_network("ff00::/8"),          # IPv6 multicast
)


class UnsafeProviderURLError(ValueError):
    """Raised when a provider URL fails SSRF/policy validation."""


@dataclass(frozen=True)
class ValidatedProviderURL:
    scheme: str
    host: str
    port: int | None
    url: str  # cleaned / re-serialised


def _addr_is_hard_blocked(addr: ipaddress._BaseAddress) -> bool:
    return any(addr in net for net in _HARD_BLOCK_NETS)


def _resolve_host(host: str) -> Iterable[ipaddress._BaseAddress]:
    """
    Resolve ``host`` to a tuple of ipaddress objects. If ``host`` is already
    an IP literal we short-circuit. We deliberately use the blocking
    ``getaddrinfo`` here because the validator runs in a Django request
    handler (synchronous) and at gateway startup-time for cached configs.
    """
    try:
        return (ipaddress.ip_address(host),)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeProviderURLError(
            f"Cannot resolve provider host {host!r}: {exc}"
        ) from exc

    resolved: list[ipaddress._BaseAddress] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family == socket.AF_INET:
            resolved.append(ipaddress.ip_address(sockaddr[0]))
        elif family == socket.AF_INET6:
            resolved.append(ipaddress.ip_address(sockaddr[0]))
    if not resolved:
        raise UnsafeProviderURLError(
            f"Host {host!r} resolved to no usable IPv4/IPv6 addresses"
        )
    return tuple(resolved)


def validate_safe_provider_url(raw_url: str) -> ValidatedProviderURL:
    """
    Validate ``raw_url`` against the SSRF policy. Returns a cleaned form
    of the URL on success; raises :class:`UnsafeProviderURLError` otherwise.

    ``VECTOR_PROVIDER_ALLOW_PRIVATE=1`` relaxes the private-net check but
    NEVER relaxes the hard-block list (loopback, metadata, link-local).
    """
    if not raw_url or not isinstance(raw_url, str):
        raise UnsafeProviderURLError("connection_url must be a non-empty string")

    raw_url = raw_url.strip()
    if not raw_url:
        raise UnsafeProviderURLError("connection_url must be a non-empty string")

    parsed = urlparse(raw_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeProviderURLError(
            f"Scheme {scheme!r} not allowed. "
            f"Use one of: {sorted(_ALLOWED_SCHEMES)}."
        )

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeProviderURLError("connection_url is missing a hostname")

    # Reject credentials embedded in URL — they bypass our credential store
    # and make audit/rotation impossible.
    if parsed.username or parsed.password:
        raise UnsafeProviderURLError(
            "connection_url must not contain inline userinfo "
            "(use the api_key field instead)"
        )

    allow_private = os.getenv("VECTOR_PROVIDER_ALLOW_PRIVATE", "").lower() in {
        "1",
        "true",
        "yes",
    }

    for addr in _resolve_host(host):
        if _addr_is_hard_blocked(addr):
            raise UnsafeProviderURLError(
                f"Host {host!r} resolves to blocked address {addr} "
                f"(loopback / metadata / link-local / multicast)"
            )
        if not allow_private and not addr.is_global:
            raise UnsafeProviderURLError(
                f"Host {host!r} resolves to non-global address {addr}. "
                f"Set VECTOR_PROVIDER_ALLOW_PRIVATE=1 to permit private "
                f"networks (dev/docker only)."
            )

    cleaned = urlunparse(
        (
            scheme,
            parsed.netloc,
            parsed.path or "",
            parsed.params,
            parsed.query,
            "",  # drop fragment
        )
    )

    return ValidatedProviderURL(
        scheme=scheme,
        host=host,
        port=parsed.port,
        url=cleaned,
    )


__all__ = [
    "UnsafeProviderURLError",
    "ValidatedProviderURL",
    "validate_safe_provider_url",
]
