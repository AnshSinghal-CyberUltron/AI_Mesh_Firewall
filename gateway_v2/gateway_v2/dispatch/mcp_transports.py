"""The MCP server transports the gateway knows (GW32, Requirement 8).

One read-only table. A server whose transport is not in it is unusable: it is never coerced to
`streamable-http`. Only a MISSING, `null` or empty value defaults (matching the shipped
`row.get("transport") or ...`); every other non-string, and every unknown name, is unsupported.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

__all__ = ("DEFAULT_TRANSPORT", "TRANSPORTS", "TransportSpec", "normalise_transport")

DEFAULT_TRANSPORT = "streamable-http"


@dataclass(frozen=True, slots=True)
class TransportSpec:
    """One transport: does it dial a remote URL, and under which schemes."""

    remote: bool
    schemes: frozenset[str]


TRANSPORTS: Mapping[str, TransportSpec] = MappingProxyType(
    {
        "streamable-http": TransportSpec(True, frozenset({"http", "https"})),
        "sse": TransportSpec(True, frozenset({"http", "https"})),
        "websocket": TransportSpec(True, frozenset({"ws", "wss"})),
        "stdio": TransportSpec(False, frozenset()),
    }
)


def normalise_transport(raw: object) -> str | None:
    """The canonical transport name, or ``None`` when unsupported (fail closed)."""
    if raw is None or raw == "":
        return DEFAULT_TRANSPORT
    if not isinstance(raw, str):
        return None
    name = raw.strip().lower()
    if not name:
        return DEFAULT_TRANSPORT
    return name if name in TRANSPORTS else None
