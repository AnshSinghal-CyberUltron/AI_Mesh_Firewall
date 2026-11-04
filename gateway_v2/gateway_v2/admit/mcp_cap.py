"""GW17c: the per-key tool-call cap, as a bounded lease table (the GW06 `BudgetLease` idiom).

`try_spend` is synchronous and local: it never touches the store. A worker spends from a lease it
took from the shared window counter off the request path. Conservation: every unit spent from
`remaining` was granted by an atomic `INCRBY` that only grants `min(chunk, cap - used)`, so grants
in a window sum to at most `cap`. The only admissions not backed by a grant are bootstrap debts, at
most `bootstrap` per key per worker per window (DD-8), repaid out of the next grant.

A store outage never admits beyond the held allowance plus `bootstrap`; the refusal then carries the
cap code, not `shared_state_unavailable` (R17.5). Nothing here fails open.

Window (DD-9): starts at a key's first acquire and ends on the store key's TTL (``PEXPIRE ... NX``),
so no worker clock skew enters. A worker's local window ends at ``t0 + pttl`` with ``t0`` read
BEFORE the store call, so it never outlives the store window.
"""

from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from gateway_v2.admit.mcp_metrics import McpPolicyMetrics
from gateway_v2.domain import posture
from gateway_v2.domain.identity import Principal
from gateway_v2.runtime.resources_mcp import CapBounds
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys

__all__ = ("CallCapTable", "CapVerdict")

_ADMIT = ""


@dataclass(frozen=True, slots=True)
class CapVerdict:
    """A value; ``edge`` renders the 429. No HTTP object here."""

    admitted: bool
    code: str
    retry_after_s: float
    should_retry: bool
    request_id: str


class _Lease:
    """Mutable per-key carrier, instance-owned (the `_LeaseState` pattern)."""

    __slots__ = (
        "bootstrap_used", "cap", "debt", "refill_in_flight", "remaining", "retry_at", "window_end",
    )

    def __init__(self, cap: int, bootstrap_used: int = 0, debt: int = 0) -> None:
        self.cap = cap
        self.remaining = 0
        self.debt = debt
        self.window_end: float | None = None
        self.bootstrap_used = bootstrap_used
        self.retry_at = 0.0
        self.refill_in_flight = False


class _Pipe(Protocol):
    async def __aenter__(self) -> _Pipe: ...
    async def __aexit__(self, *exc: object) -> None: ...
    async def watch(self, *keys: str) -> object: ...
    async def unwatch(self) -> object: ...
    async def get(self, key: str) -> object: ...
    def multi(self) -> None: ...
    def incrby(self, key: str, amount: int) -> object: ...
    def pexpire(self, key: str, ms: int, nx: bool = ...) -> object: ...
    def pttl(self, key: str) -> object: ...
    async def execute(self) -> list[Any]: ...


class _Client(Protocol):
    def pipeline(self) -> _Pipe: ...


Spawn = Callable[[Coroutine[Any, Any, None]], None]


def _retrieve(task: asyncio.Task[None]) -> None:
    """Mark a finished refill task's exception retrieved; the lease already backed off."""
    if not task.cancelled():
        task.exception()


def default_spawn(coro: Coroutine[Any, Any, None]) -> None:
    """Run off the request path on the running loop. Fire-and-forget is the point (R17.3)."""
    task = asyncio.get_running_loop().create_task(coro)
    task.add_done_callback(_retrieve)


def _as_int(raw: object) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (bytes, bytearray)):
        return int(raw)
    return int(str(raw))


class CallCapTable:
    """Bounded per-key leases plus a bounded tombstone map. One instance per worker."""

    __slots__ = (
        "_bounds", "_client", "_clock", "_keys", "_leases", "_metrics", "_rng", "_spawn",
        "_tombstones",
    )

    def __init__(
        self,
        *,
        client: _Client,
        keys: McpStoreKeys,
        bounds: CapBounds,
        clock: Callable[[], float],
        rng: random.Random,
        metrics: McpPolicyMetrics,
        spawn: Spawn = default_spawn,
    ) -> None:
        self._client, self._keys, self._bounds = client, keys, bounds
        self._clock, self._rng, self._metrics, self._spawn = clock, rng, metrics, spawn
        self._leases: dict[str, _Lease] = {}
        self._tombstones: dict[str, tuple[int, int, float]] = {}

    # -- derivations (no literal here; every number comes from CapBounds) -------------------------
    def _chunk(self, cap: int) -> int:
        share = math.ceil(cap / (2 * self._bounds.workers))
        return max(1, min(self._bounds.chunk_max, share))

    def _low_watermark(self, cap: int) -> int:
        return self._chunk(cap) // 2

    # -- the request path: synchronous, local -----------------------------------------------------
    def try_spend(self, principal: Principal, request_id: str) -> CapVerdict:
        """Spend one call from the local lease. Never touches the store."""
        now = self._clock()
        lease = self._lease_for(principal, now)
        if lease is None:
            return self._refuse(None, now, request_id)
        if lease.remaining > 0:
            lease.remaining -= 1
            admitted = True
        elif lease.window_end is None and lease.bootstrap_used < self._bounds.bootstrap:
            lease.bootstrap_used += 1
            lease.debt += 1
            admitted = True
        else:
            admitted = False
        self._maybe_refill(principal, lease, now)
        return CapVerdict(True, _ADMIT, 0.0, False, request_id) if admitted else self._refuse(
            lease, now, request_id
        )

    def touch(self, principal: Principal) -> None:
        """Pre-warm: schedule an acquire for a capped key on a non-counted method."""
        if principal.mcp_max_tool_calls <= 0:
            return
        now = self._clock()
        lease = self._lease_for(principal, now)
        if lease is not None and lease.remaining <= 0:
            self._maybe_refill(principal, lease, now, force=True)

    def _lease_for(self, principal: Principal, now: float) -> _Lease | None:
        cap, key_id = principal.mcp_max_tool_calls, principal.key_id
        lease = self._leases.get(key_id)
        if lease is None:
            lease = self._insert(key_id, cap, now)
            if lease is None:
                return None
            return lease
        if lease.cap != cap:  # generation tag (R18.3): drop held allowance, keep bootstrap_used
            lease.remaining, lease.debt, lease.cap, lease.window_end = 0, 0, cap, None
        elif lease.window_end is not None and now >= lease.window_end:
            self._metrics.observe_overshoot(lease.debt)
            lease.remaining, lease.debt, lease.window_end, lease.bootstrap_used = 0, 0, None, 0
        return lease

    def _insert(self, key_id: str, cap: int, now: float) -> _Lease | None:
        self._expire_tombstones(now)
        if len(self._leases) >= self._bounds.table_slots:
            victim = min(
                self._leases,
                key=lambda k: -1.0 if self._leases[k].window_end is None
                else self._leases[k].window_end or 0.0,
            )
            if len(self._tombstones) >= self._bounds.table_slots and key_id not in self._tombstones:
                return None  # churn above the table bounds yields 429, never free calls
            old = self._leases.pop(victim)
            self._tombstones[victim] = (old.bootstrap_used, old.debt, now)
            self._metrics.observe_evicted()
        used, debt, _ = self._tombstones.pop(key_id, (0, 0, 0.0))
        lease = _Lease(cap, used, debt)
        self._leases[key_id] = lease
        return lease

    def _expire_tombstones(self, now: float) -> None:
        window = self._bounds.cap_window_s
        stale = [k for k, (_, _, at) in self._tombstones.items() if now - at >= window]
        for key in stale:
            del self._tombstones[key]

    def _refuse(self, lease: _Lease | None, now: float, request_id: str) -> CapVerdict:
        window = self._bounds.cap_window_s
        left = window if lease is None or lease.window_end is None else lease.window_end - now
        base = min(window, max(posture.MIN_RETRY_AFTER_S, left))
        retry = min(window, base + self._rng.uniform(0.0, base / 4))
        self._metrics.observe_cap_refused()
        return CapVerdict(False, posture.MCP_TOOL_CALL_CAP_EXCEEDED, retry, False, request_id)

    def _maybe_refill(
        self, principal: Principal, lease: _Lease, now: float, *, force: bool = False
    ) -> None:
        low = lease.remaining <= self._low_watermark(lease.cap)
        if not (low or force) or lease.refill_in_flight or now < lease.retry_at:
            return
        lease.refill_in_flight = True
        self._spawn(self._acquire(principal.key_id, lease))

    # -- off the request path: touches the store --------------------------------------------------
    async def _acquire(self, key_id: str, lease: _Lease) -> None:
        self._metrics.observe_async_refill()
        cap, chunk = lease.cap, self._chunk(lease.cap)
        t0 = self._clock()
        try:
            grant, pttl_ms = await self._transact(key_id, cap, chunk)
        except Exception:  # noqa: BLE001 - any store failure keeps the held allowance and backs off
            lease.retry_at = self._clock() + self._bounds.retry_backoff_s
            lease.refill_in_flight = False
            return
        net = lease.remaining + grant - lease.debt
        lease.remaining, lease.debt = (net, 0) if net >= 0 else (0, -net)
        lease.window_end = t0 + pttl_ms / 1000.0
        lease.retry_at = 0.0
        lease.refill_in_flight = False

    async def _transact(self, key_id: str, cap: int, chunk: int) -> tuple[int, int]:
        key = self._keys.cap_used(key_id)
        window_ms = max(1, round(self._bounds.cap_window_s * 1000))
        for _ in range(self._bounds.max_cas_tries):
            async with self._client.pipeline() as pipe:
                try:
                    await pipe.watch(key)
                    used = _as_int(await pipe.get(key))
                    grant = min(chunk, max(0, cap - used))
                    pipe.multi()
                    pipe.incrby(key, grant)
                    pipe.pexpire(key, window_ms, nx=True)
                    pipe.pttl(key)
                    results = await pipe.execute()
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ == "WatchError":
                        continue
                    raise
            pttl = _as_int(results[-1])
            if pttl <= 0:
                raise RuntimeError("cap window has no TTL")
            return grant, pttl
        raise RuntimeError("cap acquire exhausted its optimistic retries")
