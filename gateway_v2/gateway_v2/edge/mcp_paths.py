"""MCP path parsing (GW32 Requirement 2). Pure: no ASGI objects, no I/O.

Two shapes reach the same handler: ``/v1/mcp/{slug}`` and the v1 path
``/gateway/{org}/mcp/{srv}``. The server segment is validated by ``BROKER_SLUG`` itself, so a slug
that parses can never be an unsafe broker path component. A percent sign, NUL, empty or dot
segment, or an extra segment makes the whole path unparseable (``None``): a declared 404 with zero
downstream calls.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.dispatch.mcp_broker import BROKER_SLUG
from gateway_v2.domain.mcp_path import ORG_SEGMENT

__all__ = ("GatewayPath", "McpPath", "classify_gateway_path", "parse_mcp_path", "raw_path")

_V1_PREFIX = "/v1/mcp/"
_GATEWAY_PREFIX = "/gateway/"
_FORBIDDEN = ("%", "\x00")


@dataclass(frozen=True, slots=True)
class McpPath:
    server_slug: str
    path_org: str | None  # None for /v1/mcp/{slug}


@dataclass(frozen=True, slots=True)
class GatewayPath:
    """A classified ``/gateway/...`` path. ``kind`` is one of ``mcp``, ``oauth_start``,
    ``oauth_status``, ``oauth_callback`` or ``miss`` (anything else: the routing miss)."""

    kind: str
    org: str = ""
    server_slug: str = ""


def _clean(raw: str) -> bool:
    return not any(bad in raw for bad in _FORBIDDEN)


def _segment_ok(value: str) -> bool:
    return bool(value) and value not in (".", "..")


def parse_mcp_path(raw: str) -> McpPath | None:
    """Parse the raw (undecoded) path. ``None`` = not an MCP server path."""
    if not _clean(raw):
        return None
    if raw.startswith(_V1_PREFIX):
        slug = raw[len(_V1_PREFIX) :].removesuffix("/")
        return McpPath(slug, None) if _segment_ok(slug) and BROKER_SLUG.match(slug) else None
    parsed = classify_gateway_path(raw)
    if parsed.kind == "mcp":
        return McpPath(parsed.server_slug, parsed.org)
    return None


def classify_gateway_path(raw: str) -> GatewayPath:
    """Classify a ``/gateway/...`` path into the five served shapes, or ``miss``."""
    miss = GatewayPath("miss")
    if not _clean(raw) or not raw.startswith(_GATEWAY_PREFIX):
        return miss
    rest = raw[len(_GATEWAY_PREFIX) :]
    if rest == "oauth/callback":
        return GatewayPath("oauth_callback")
    parts = rest.removesuffix("/").split("/") if rest.endswith("/") else rest.split("/")
    if len(parts) < 3 or parts[1] != "mcp":
        return miss
    org, slug = parts[0], parts[2]
    if not (_segment_ok(org) and ORG_SEGMENT.match(org) and _segment_ok(slug)):
        return miss
    if not BROKER_SLUG.match(slug):
        return miss
    tail = parts[3:]
    if not tail:
        return GatewayPath("mcp", org, slug)
    if tail == ["oauth", "start"]:
        return GatewayPath("oauth_start", org, slug)
    if tail == ["oauth", "status"]:
        return GatewayPath("oauth_status", org, slug)
    return miss


def raw_path(scope: Mapping[str, object]) -> str:
    """The undecoded path when the server provides it (so a percent-encoded separator stays
    visible to the parser), else ``scope["path"]``. The query string is never part of it."""
    raw = scope.get("raw_path")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).split(b"?", 1)[0].decode("latin-1")
    path = scope.get("path")
    return path if isinstance(path, str) else ""
