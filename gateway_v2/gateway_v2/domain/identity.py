"""Who a request belongs to. The published key record, resolved.

`feed_seq` is the cursor position at which this principal was admitted to a cache. It is what
makes per-key invalidation sound: a cached principal is valid as of a known position, so the
enforcement bound for a revocation is the feed lag, not "whenever the whole cache was last
dropped".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """Resolved from one signed key record. GW06 owns what the quota fields mean."""

    key_id: str
    org_id: str
    rate_per_s: float
    burst: float
    feed_seq: int
