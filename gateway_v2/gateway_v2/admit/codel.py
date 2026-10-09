"""Pure per-owner CoDel admission state machine (R2-07 / R2-08, card GW19).

In v1 and the prototype, overload behaviour was emergent and unbounded: a tenant-blind FIFO queue
grew without limit, admitted p99 collapsed, and an old **12 ms instantaneous latency bound** sheds
long prompts first (prompt-size bias). This module replaces that with the classic Controlled-Delay
(CoDel) admission rule applied **per owner**, so overload becomes explicit and bounded.

The controller tracks the minimum sojourn time over a sliding ``Interval`` and sheds at the door
only once that minimum has stayed above ``Target`` for a full ``Interval``, backing off more
aggressively (``interval / sqrt(count)``) the longer the overload persists. Two rules bound the
tails: a ``Hard_Cap`` short-circuits CoDel entirely (shed regardless of backoff state), and a
single sub-``Target`` sojourn clears the excursion so the controller returns to admitting the
moment delay drains (quiescence / post-heal recovery).

Design invariants carried here (see the admission-control design doc):

* **Admission depends on sojourn + CoDel state ONLY** — never prompt size. There is deliberately
  no prompt-size argument and NO 12 ms instantaneous bound anywhere in this module (Req 3.1 / 3.2).
* **Quiescence** (Property 6, Req 1.2): while the minimum sojourn has stayed at/below ``Target``
  for a full ``Interval`` the controller admits every request and never sheds.
* **Shedding under sustained overload** (Property 2, Req 1.3): sustained sojourn above ``Target``
  eventually sheds, and the ``sqrt(count)`` contraction makes shedding grow more aggressive — more
  overload never *reduces* shedding.

The module is pure except for the injected clock (``Callable[[], float]`` defaulting to
``time.monotonic``, mirroring ``KillSwitchSnapshot`` / ``IdentityCache``). It depends on nothing
beyond the standard library — the ``admit`` layer may import ``runtime`` + ``domain``, but the CoDel
core needs neither. The controller's mutable state is per-instance (one controller per owner); there
is no module-level mutable state.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

__all__ = (
    "CoDelController",
    "CoDelDecision",
    "CoDelParams",
    "CoDelReason",
)


@dataclass(frozen=True, slots=True)
class CoDelParams:
    """Owner-signed CoDel parameters (Requirement 1.5).

    The defaults are the owner-signed values from the runbook: ``Target`` 5 ms, ``Interval``
    100 ms, ``Hard_Cap`` 60 ms. They are injected at construction so a test can vary them, but the
    production path uses these exact numbers.
    """

    target_ms: float = 5.0
    interval_ms: float = 100.0
    hard_cap_ms: float = 60.0


class CoDelReason(StrEnum):
    """Why a decision was reached. ``StrEnum`` per the house convention (``KillSwitchState``)."""

    ADMIT = "admit"
    SHED_BACKOFF = "shed_backoff"
    SHED_HARD_CAP = "shed_hard_cap"


@dataclass(frozen=True, slots=True)
class CoDelDecision:
    """An immutable admission decision.

    ``next_drop_at`` carries the controller's current ``drop_next_at`` for observability: when the
    controller is dropping it is the clock time of the next scheduled drop; otherwise it is 0.0
    (no drop scheduled).
    """

    admit: bool
    reason: CoDelReason
    next_drop_at: float


class CoDelController:
    """Per-owner CoDel admission state machine. One instance per owner (the registry owns the map).

    Pure except for the injected clock; deterministic given ``(sojourn_ms, now)``. All mutable
    state lives on the instance — there is no module-level mutable state.
    """

    __slots__ = (
        "_clock",
        "_params",
        "count",
        "drop_next_at",
        "dropping",
        "first_above_at",
        "last_below_at",
    )

    def __init__(self, params: CoDelParams, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._params = params
        self._clock = clock
        # Per-owner excursion/drop state (see the design's "exact CoDel algorithm" section).
        self.first_above_at: float | None = None
        self.dropping: bool = False
        self.drop_next_at: float = 0.0
        self.count: int = 0
        self.last_below_at: float = 0.0

    @property
    def params(self) -> CoDelParams:
        return self._params

    def observe_and_decide(
        self,
        sojourn_ms: float,
        *,
        now: float | None = None,
    ) -> CoDelDecision:
        """Observe one request's sojourn and decide admit/shed.

        Implements the design's exact algorithm. ``now`` defaults to the injected clock so tests
        can advance time deterministically (Req 1.6). Admission is a function of ``sojourn_ms`` and
        CoDel state ONLY — never prompt size (Req 3.1 / 3.2).
        """
        now = self._clock() if now is None else now
        target = self._params.target_ms
        interval = self._params.interval_ms

        # Hard cap short-circuits CoDel entirely: a request at/above the ceiling is shed regardless
        # of backoff state (Req 1.4). State is left untouched — the cap is orthogonal to the
        # excursion machinery.
        if sojourn_ms >= self._params.hard_cap_ms:
            return CoDelDecision(
                admit=False,
                reason=CoDelReason.SHED_HARD_CAP,
                next_drop_at=self.drop_next_at if self.dropping else 0.0,
            )

        # At/below target: clear the excursion; CoDel is quiescent (Req 1.2 / Property 6). A single
        # sub-target sojourn also clears `dropping`, so the controller recovers the moment delay
        # drains (post-heal recovery, Property 9 locally).
        if sojourn_ms <= target:
            self.first_above_at = None
            if self.dropping:
                self.dropping = False
            self.last_below_at = now
            return CoDelDecision(admit=True, reason=CoDelReason.ADMIT, next_drop_at=0.0)

        # Sojourn above target.
        if self.first_above_at is None:
            # First sample of a new excursion: start the window, admit (the min has not yet stayed
            # above target for a full Interval).
            self.first_above_at = now
            return CoDelDecision(admit=True, reason=CoDelReason.ADMIT, next_drop_at=0.0)

        if now - self.first_above_at < interval:
            # The min sojourn has NOT stayed above target for a full Interval yet: still admit.
            return CoDelDecision(admit=True, reason=CoDelReason.ADMIT, next_drop_at=0.0)

        # The min sojourn has stayed above target for a full Interval -> enter/continue dropping.
        if not self.dropping:
            self.dropping = True
            self.count = 1
            self.drop_next_at = now + interval  # first drop one Interval out
            return CoDelDecision(
                admit=False,
                reason=CoDelReason.SHED_BACKOFF,
                next_drop_at=self.drop_next_at,
            )

        if now >= self.drop_next_at:
            # Control law: the inter-drop gap contracts as sqrt(count) under sustained overload, so
            # shedding grows more aggressive the longer overload persists (Req 1.3 / Property 2).
            self.count += 1
            self.drop_next_at = now + interval / math.sqrt(self.count)
            return CoDelDecision(
                admit=False,
                reason=CoDelReason.SHED_BACKOFF,
                next_drop_at=self.drop_next_at,
            )

        # Between scheduled drops: still admit.
        return CoDelDecision(
            admit=True,
            reason=CoDelReason.ADMIT,
            next_drop_at=self.drop_next_at,
        )

    def reset(self) -> None:
        """Clear the excursion/drop state (quiescence).

        Returns the controller to the admitting baseline without discarding the injected clock or
        params. Equivalent to the state after a single sub-target sojourn.
        """
        self.first_above_at = None
        self.dropping = False
        self.drop_next_at = 0.0
        self.count = 0
