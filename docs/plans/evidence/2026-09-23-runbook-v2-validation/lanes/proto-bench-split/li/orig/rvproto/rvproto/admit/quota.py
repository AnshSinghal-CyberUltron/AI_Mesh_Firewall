"""Quota: local GCRA per org (burst/rate) + org token-budget LEASE from the shared store.

GCRA is per-process burst smoothing. The org-wide limit is the token budget: a
worker draws a lease chunk (size from the ResourceContract) with one Lua round
trip only when its local lease cannot cover the request, so N replicas cannot
admit N x the budget; overshoot is bounded by the chunks currently held.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import K_BUDGET_PREFIX, LEASE_LUA


class Gcra:
    def __init__(self) -> None:
        self._tat: dict[str, float] = {}

    def check(self, org: str, rate_per_s: float, burst: float, now_s: float) -> float | None:
        """None if admitted, else seconds until the next conforming arrival."""
        if rate_per_s <= 0:
            return None
        interval = 1.0 / rate_per_s
        tolerance = max(burst - 1.0, 0.0) * interval
        tat = max(self._tat.get(org, now_s), now_s)
        if tat - now_s > tolerance:
            return tat - now_s - tolerance
        self._tat[org] = tat + interval
        return None


class TokenLease:
    def __init__(self, r: aioredis.Redis, metrics: Registry, *, chunk_tokens: int) -> None:
        self.r = r
        self.metrics = metrics
        self.chunk = chunk_tokens
        self._local: dict[str, int] = {}
        self._script = r.register_script(LEASE_LUA)

    def try_local(self, org: str, cost: int) -> bool:
        have = self._local.get(org, 0)
        if have >= cost:
            self._local[org] = have - cost
            self.metrics.inc(f'quota_admitted_tokens{{org="{org}"}}', cost)
            return True
        return False

    def held(self) -> dict[str, int]:
        """Unspent lease per org held by this worker (the bound on this worker's overshoot)."""
        return dict(self._local)

    async def refill(self, org: str, cost: int) -> bool:
        """One shared-state round trip. Raises StoreError on outage."""
        want = max(self.chunk, cost)
        grant = int(await self._script(keys=[K_BUDGET_PREFIX + org], args=[want]))
        self.metrics.inc("lease_refills")
        self.metrics.inc(f'lease_granted_tokens{{org="{org}"}}', grant)
        have = self._local.get(org, 0) + grant
        if have >= cost:
            self._local[org] = have - cost
            self.metrics.inc(f'quota_admitted_tokens{{org="{org}"}}', cost)
            return True
        self._local[org] = have
        self.metrics.inc(f'quota_rejected{{org="{org}"}}')
        return False
