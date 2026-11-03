"""Per-principal ``LocalGCRA`` cache keyed by key hash (GW36 S4, Req 3.1-3.5, 3.7).

Each resolved :class:`~gateway_v2.domain.identity.Principal` gets at most one
:class:`~gateway_v2.admit.gcra.LocalGCRA` per worker, built from the principal's
``rate_per_s`` and ``burst`` and reused while those two values are unchanged. The GCRA carries the
per-worker burst allowance and sustained rate; the org-level token *budget* is a separate
store-backed lease (``admit/lease.py``). This cache is the thing the production boot consults on the
request path (via the composed quota gate) to find the right GCRA for a key.

Design invariants carried here (GW36 S4 design §3, "Per-principal GCRA cache"):

* **Keyed by the key's sha256 hash** — the same hash identity resolves on. ``for_principal`` builds
  and caches on the first request for a key (Req 3.2) and reuses the cached instance while the
  cached principal's ``(rate_per_s, burst)`` are unchanged; a change rebuilds (Req 3.2 / 3.3).
* **Per-worker rate split** — the ``GcraParams.rate_per_s`` is ``principal.rate_per_s /
  worker_count`` so N workers sum to the principal's configured rate (Req 3.4). ``burst`` is the
  per-worker ``Local_Burst_Limit``, passed through unchanged.
* **Rate 0 = no per-key limit** — a :class:`LocalGCRA` rejects a non-positive rate at construction,
  so ``principal.rate_per_s == 0`` caches and returns ``None`` to mean "admit with respect to this
  limit" (Req 3.5). The caller treats ``None`` as "no rate check".
* **Rebuild on KEY delta** — :meth:`invalidate` drops the cached entry for each named key hash so
  the next request rebuilds from the updated principal (Req 3.3). The production boot invalidates
  this cache alongside the identity cache from the identity applier on a KEY delta.
* **No per-key request-count limit beyond burst + rate** — those are GW27, out of scope (Req 3.7).

The cache state is per-instance (one cache per worker); there is no module-level mutable state, so
the ``admit/`` module-mutable AST gate holds. ``admit`` may import ``runtime`` + ``domain`` only —
this module imports the sibling ``gcra`` and ``domain.identity`` and nothing from
``edge``/``resolve``.
The injected clock defaults to ``time.monotonic`` and is threaded into every built ``LocalGCRA``,
mirroring the ``LocalGCRA`` / ``CoDelController`` idiom.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.domain.identity import Principal

__all__ = ("GcraCache",)


class GcraCache:
    """Build, cache, and invalidate per-principal ``LocalGCRA`` instances, keyed by key hash.

    One instance per worker. ``worker_count`` divides each principal's sustained rate across workers
    (Req 3.4); it must be ``>= 1`` so the division is well-defined (a zero or negative count would
    divide by zero — fail closed at construction). ``clock`` is injected and threaded into every
    built ``LocalGCRA``.
    """

    __slots__ = ("_cache", "_clock", "_worker_count")

    def __init__(
        self,
        *,
        worker_count: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if worker_count < 1:
            msg = f"worker_count must be >= 1, got {worker_count!r}"
            raise ValueError(msg)
        self._worker_count: int = worker_count
        self._clock: Callable[[], float] = clock
        # key sha256 hash -> (principal the entry was built from, its GCRA or None for rate 0).
        # Instance state only; no module-level mutable state (admit/ module-mutable gate).
        self._cache: dict[str, tuple[Principal, LocalGCRA | None]] = {}

    def for_principal(self, key_hash: str, principal: Principal) -> LocalGCRA | None:
        """Return the cached ``LocalGCRA`` for ``key_hash``, (re)building when needed.

        Builds and caches on the first request for a key (Req 3.2). On a later request, reuses the
        cached instance while the cached principal's ``(rate_per_s, burst)`` are unchanged, else
        rebuilds from the passed principal (Req 3.2 / 3.3). Returns ``None`` when
        ``principal.rate_per_s == 0`` (no per-key limit, Req 3.5).
        """
        cached = self._cache.get(key_hash)
        if cached is not None:
            cached_principal, cached_gcra = cached
            if (
                cached_principal.rate_per_s == principal.rate_per_s
                and cached_principal.burst == principal.burst
            ):
                return cached_gcra

        gcra = self._build(principal)
        self._cache[key_hash] = (principal, gcra)
        return gcra

    def invalidate(self, key_hashes: Iterable[str]) -> None:
        """Drop the cached GCRA for each named key hash so the next request rebuilds (Req 3.3)."""
        for key_hash in key_hashes:
            self._cache.pop(key_hash, None)

    def _build(self, principal: Principal) -> LocalGCRA | None:
        """Build a per-worker ``LocalGCRA`` from a principal, or ``None`` when rate is 0.

        Per-worker rate = ``principal.rate_per_s / worker_count`` (Req 3.4); ``burst`` passes
        through as the per-worker ``Local_Burst_Limit``. A non-positive rate means no per-key
        limit: a ``LocalGCRA`` would reject it at construction, so return ``None`` (Req 3.5).
        """
        if principal.rate_per_s == 0:
            return None
        params = GcraParams(
            rate_per_s=principal.rate_per_s / self._worker_count,
            burst=int(principal.burst),
        )
        return LocalGCRA(params, clock=self._clock)
