"""Pure per-worker Local GCRA for the Org burst allowance and sustained rate (R2-09, card GW06).

The org-level token *budget* is enforced by a shared store-backed lease (``admit/lease.py``); this
module enforces the orthogonal **burst and sustained rate** limits, **per worker, locally, with no
store round trip ever** (Req 1.1 / 1.3; Property 8). It implements the classic Generic Cell Rate
Algorithm (GCRA) over a single theoretical-arrival-time (TAT) carrier.

GCRA in one line: with an emission interval ``T = 1 / rate_per_s`` and a burst tolerance
``tau = burst * T``, an arrival at time ``now`` is admitted when ``now >= TAT - tau`` (i.e. the
arrival is not "too early" by more than the tolerated burst), and on admit the TAT advances to
``max(TAT, now) + T``. A rejected arrival does **not** advance the TAT, so a rejected burst never
pushes the allowance further into the future — the next legitimately-timed arrival is still served.

Design invariants carried here (see the budget-lease design doc, the ``LocalGCRA`` component):

* **No store round trip, ever** — the decision is a pure function of the injected clock + params
  (Req 1.3 / 1.4; Property 8). The module depends on nothing beyond the standard library; the
  ``admit`` layer may import ``runtime`` + ``domain``, but the GCRA core needs neither.
* **The TAT advances only on admit** — this is what bounds the windowed admission count to
  ``burst + rate_per_s * window`` (plus the standard GCRA +1 boundary; see the property test).
* **No capacity literal here** — ``rate_per_s`` / ``burst`` are supplied by the façade
  (``admit/quota.py``), derived from the :class:`~gateway_v2.runtime.resources.ResourceContract`
  and the per-org share (Req 2.2 / 2.3; Property 9). The ``admit/`` capacity-literal gate covers it.

The carrier's mutable state is per-instance (one GCRA per worker per org); there is no module-level
mutable state. The injected ``Callable[[], float]`` clock defaults to ``time.monotonic``, mirroring
``CoDelController`` / ``KillSwitchSnapshot``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

__all__ = (
    "GcraParams",
    "LocalGCRA",
)


@dataclass(frozen=True, slots=True)
class GcraParams:
    """Local GCRA parameters, supplied by the façade — never literals here.

    ``rate_per_s`` is the sustained rate the Org is allowed; ``burst`` is the ``Local_Burst_Limit``
    enforced per worker. Both derive from the ``ResourceContract`` + the per-org share in
    ``admit/quota.py`` (Req 2.2 / 2.3), so this module holds no capacity-position literal.
    """

    rate_per_s: float
    burst: int


class LocalGCRA:
    """Per-worker Generic Cell Rate Algorithm. One instance per worker per org.

    Pure over its state except for the injected clock; deterministic given ``(now,)``. The only
    mutable state is the per-instance TAT carrier — there is no module-level mutable state and no
    store round trip (Req 1.3; Property 8).
    """

    __slots__ = ("_burst", "_clock", "_params", "_tat", "_tau", "_t")

    def __init__(
        self,
        params: GcraParams,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._params = params
        self._clock = clock
        # Emission interval T = 1 / rate; burst tolerance tau = burst * T. Precomputed once so
        # ``admit`` stays allocation-free on the hot path. A non-positive rate would make T
        # ill-defined, so it is rejected at construction (fail-closed — Req 13.1).
        if params.rate_per_s <= 0.0:
            msg = f"rate_per_s must be positive, got {params.rate_per_s!r}"
            raise ValueError(msg)
        if params.burst < 0:
            msg = f"burst must be non-negative, got {params.burst!r}"
            raise ValueError(msg)
        self._t: float = 1.0 / params.rate_per_s
        self._burst: int = params.burst
        self._tau: float = params.burst * self._t
        # TAT carrier: ``None`` until the first arrival, which is always admitted.
        self._tat: float | None = None

    @property
    def params(self) -> GcraParams:
        return self._params

    def admit(self, *, now: float | None = None) -> bool:
        """Decide whether an arrival at ``now`` is within the burst + rate allowance.

        ``now`` defaults to the injected clock so tests advance time deterministically (Req 1.4).
        Standard GCRA: admit when ``now >= TAT - tau`` and then advance ``TAT = max(TAT, now) + T``;
        otherwise reject and leave the TAT untouched. The first arrival (``TAT is None``) is always
        admitted. No store round trip, ever (Req 1.3; Property 8).
        """
        now = self._clock() if now is None else now

        # First arrival: admit and seat the TAT at ``now + T``.
        if self._tat is None:
            self._tat = now + self._t
            return True

        # Reject when the arrival is earlier than the earliest allowed time (TAT - tau). The TAT is
        # NOT advanced on reject, so a rejected burst does not push the allowance into the future.
        if now < self._tat - self._tau:
            return False

        # Admit: advance the TAT by one emission interval from the later of (TAT, now). Using
        # ``max`` lets an idle gap refill the allowance (TAT falls behind ``now``) up to the burst
        # tolerance, while a steady stream advances the TAT by exactly T per admit.
        self._tat = max(self._tat, now) + self._t
        return True
