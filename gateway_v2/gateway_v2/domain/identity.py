"""Who a request belongs to. The published key record, resolved.

`feed_seq` is the cursor position at which this principal was admitted to a cache. It is what
makes per-key invalidation sound: a cached principal is valid as of a known position, so the
enforcement bound for a revocation is the feed lag, not "whenever the whole cache was last
dropped".
"""

from __future__ import annotations

from dataclasses import dataclass

DENY_ALL_TOOLS = "\x00deny-all"
"""GW17c sentinel allow-list entry. No valid tool name can equal it (names carry no control
characters), so a list holding it permits nothing. A corrupt or over-bound list projects to this,
never to "unrestricted" (R15.6)."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved from one signed key record. GW06 owns what the quota fields mean."""

    key_id: str
    org_id: str
    rate_per_s: float
    burst: float
    feed_seq: int
    mcp_allowed_tools: frozenset[str] = frozenset()
    """GW17c: tool names this key may call. Empty = unrestricted; `DENY_ALL_TOOLS` = none."""
    mcp_max_tool_calls: int = 0
    """GW17c: tool calls per cap window. 0 means unlimited."""
