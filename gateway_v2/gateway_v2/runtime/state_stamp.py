"""Freshness: not "did I read the store recently" but "was the store checked against truth".

R2-03 is two defects with one root. A gateway's staleness ceiling measures time since its OWN
last successful store read, and that measure is blind in exactly the two cases that matter:

* The store fails over to a lagging replica. A process that already applied the newer manifest
  refuses the older one and fails closed, correctly. But a process started AFTER the failover
  has applied nothing, so its floor is `ZERO`, so it refuses nothing: it verifies the older --
  genuinely signed -- manifest and serves pre-write state. Revoked keys admitted, killed orgs
  admitted (SP1: 359 of 365 admits in 30 s).
* A write commits to Postgres and its publish fails. Gateways verify the store against ITSELF,
  so the older manifest verifies, the refresh succeeds, the snapshot never ages and `state()`
  reports `ok`. With no re-hydrator running the gap is unbounded (SP2: 60 s, no 503).

The re-hydrator is the only component that compares the store with Postgres. GW05b makes that
comparison readable by a gateway for the price of one `GET`, and `StampView` is the reader.

Three rules, and the third is the one that actually closes SP1
--------------------------------------------------------------
* **Floors only rise**, and `verified_at` only moves forward. A stamp from a lagging replica, or
  from a slower second re-hydrator, never lowers either.
* **State not verified within `FRESH_MS` is refused.** So a committed write is enforced -- or the
  gateway fails closed -- within a bounded time of its commit, whatever lost it.
* **A new process trusts only a stamp from a round that STARTED AFTER the process did.** A floor
  alone is not enough here, and this is worth being precise about: after a failover the stamp
  sitting on the lagging replica is ITSELF old, so a fresh process would adopt its old floor and
  serve old state perfectly happily, believing itself verified. The start gate forces the process
  to wait until a re-hydrator has compared THAT store -- the one it is now reading -- with
  Postgres. The cost is up to one re-hydrator round of extra start-up.

What this module does NOT do
----------------------------
It holds no opinion about what a request should do. `fresh()` is a fact; turning it into a 503 is
the kill switch's, the identity cache's and the plan snapshot's business, because the right
answer differs per kind. `state_ready` is the one exception, and it exists so GW06's `/readyz` and
this module cannot drift into two different definitions of ready.

The clock, stated plainly
-------------------------
`verified_at` is the re-hydrator's wall clock; the age is the gateway's. The comparison is only as
good as NTP across both tiers. Skew in the gateway-behind direction makes stamps look old and the
fleet fails closed: loud, safe, and visible as a large or negative age. Skew the other way extends
the exposure window by the skew, and `fresh()`'s absolute value catches only gross cases -- a
stamp dated further ahead than the bound is not fresh, so a badly skewed re-hydrator cannot pin
the fleet "fresh" forever. Monotonic clocks cannot help: the two readings come from different
processes on different hosts.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping

from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.domain.state import (
    START,
    Cursor,
    StateKind,
    StoreDataUnavailable,
)
from gateway_v2.runtime.state_sig import decode_stamp

LOG = logging.getLogger("amf.state.freshness")

NO_STAMP_SEEN = (
    "no freshness stamp seen yet: the store has not been compared with Postgres "
    "(is a re-hydrator running?)"
)
FRESHNESS_NOT_ENFORCED = "state freshness is not enforced"

RECHECK_S = 0.1
"""How often to look for a usable stamp while the store answers but freshness is not yet met.

Only ever shortens the wait of a process that is NOT serving: once fresh, the cadence returns to
the normal refresh period and the steady state is untouched. This is what bounds a new process's
extra start-up at one re-hydrator round plus 100 ms, and it is deliberately preferred over a
pub/sub nudge for the stamp -- a nudge would buy at most one refresh period against a 5 s budget
while adding a constant publish rate proportional to re-hydrators times workers (R2-23's cost
class), and it would do nothing for the case that actually matters, which is start-up.
"""


class StampView:
    """The newest stamp this process trusts. One per worker, shared by every kind's reader.

    Shared on purpose: identity, the kill switch and the plan snapshot must agree about whether
    the state they hold was verified, or two components take opposite actions on one fault --
    the v1 rate-limiter/breaker defect that the single failure-posture table exists to prevent.
    """

    def __init__(
        self,
        secret: bytes,
        *,
        fresh_ms: int = FRESH_MS,
        started_at: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secret:
            raise ValueError("a state signing secret is required")
        self._secret = secret
        self._fresh_s = fresh_ms / 1000
        self._clock = clock
        # Stamps carry whole milliseconds, so a round that started within this process's start
        # millisecond counts as having started after it. Without the snap that boundary is a
        # coin toss, and a process could wait a whole extra round for nothing.
        moment = clock() if started_at is None else started_at
        self._started_at = int(moment * 1000) / 1000
        self._lock = threading.Lock()
        self._verified_at: float | None = None
        self._deep_at: float | None = None
        self._by = ""
        self._degraded = False
        self._floor: dict[StateKind, Cursor] = {}
        self._missing = 0
        self._invalid = 0
        self._logged: bool | None = None  # the freshness last logged; None = nothing yet
        self._lapses = 0

    # --- the refresh path ----------------------------------------------------------------------

    def observe(self, raw: bytes | None) -> bool:
        """Adopt a stamp. False when it is absent, malformed or forged.

        A rejected stamp changes NOTHING -- no floor moves, no timestamp moves -- so the view
        simply ages into "not fresh" and the fleet fails closed on its own. Absent and invalid
        are counted apart because they mean different things to an operator: absent usually means
        no re-hydrator is running, while invalid is a signing-key or tampering signal.
        """
        if raw is None:
            with self._lock:
                self._missing += 1
            return False
        try:
            stamp = decode_stamp(self._secret, raw)
        except StoreDataUnavailable:
            with self._lock:
                self._invalid += 1
            return False
        with self._lock:
            for kind, cursor in stamp.cursors.items():
                self._raise_locked(kind, cursor)
            if stamp.verified_at > (self._verified_at or 0.0):
                self._verified_at = stamp.verified_at
                self._by = stamp.by
                self._degraded = stamp.degraded
            if stamp.deep and stamp.verified_at > (self._deep_at or 0.0):
                self._deep_at = stamp.verified_at
        return True

    def _raise_locked(self, kind: StateKind, cursor: Cursor) -> None:
        held = self._floor.get(kind)
        if held is None:
            self._floor[kind] = cursor
            return
        if cursor.version < held.version or cursor.feed_seq < held.feed_seq:
            # Either dimension lower means the stamp is older or incoherent. Adopting the half
            # that happens to be higher would build a floor no single generation ever had.
            return
        self._floor[kind] = cursor

    # --- the request path: RAM only -------------------------------------------------------------

    def floor(self, kind: StateKind) -> Cursor:
        """The lowest generation of `kind` this process may apply. `START` until a stamp lands."""
        with self._lock:
            return self._floor.get(kind, START)

    def fresh(self, now: float | None = None) -> bool:
        """Was the state verified recently enough to serve from?

        `fresh_ms <= 0` disables the check. That is a break-glass, not a mode: it reinstates
        R2-03's exposure, so the start-up path logs it and `FRESHNESS_NOT_ENFORCED` is the
        reason string. Floors still apply under it -- the opt-out turns off the 503, not the
        anti-regress rule, because a floor costs nothing and only ever refuses stale data.
        """
        if self._fresh_s <= 0:
            return True
        moment = self._clock() if now is None else now
        with self._lock:
            verified = self._verified_at
            if verified is None or verified < self._started_at:
                return False
            # Absolute: a stamp from further in the FUTURE than the bound is not fresh either,
            # so a re-hydrator with a badly skewed clock cannot pin the fleet fresh for ever.
            return abs(moment - verified) <= self._fresh_s

    def age_seconds(self, now: float | None = None) -> float | None:
        """Seconds since the state was last verified. None when no stamp has been seen."""
        moment = self._clock() if now is None else now
        with self._lock:
            return None if self._verified_at is None else moment - self._verified_at

    def deep_age_seconds(self, now: float | None = None) -> float | None:
        """Seconds since a round also compared the whole index. None when none has been seen."""
        moment = self._clock() if now is None else now
        with self._lock:
            return None if self._deep_at is None else moment - self._deep_at

    def unverified(self) -> str:
        """Why state is refused, for the 503 detail and the structured log.

        Three distinct messages, because the three causes need different operator actions: no
        re-hydrator at all, a re-hydrator that has not yet looked at the store THIS process
        reads, and a re-hydrator that has gone away.
        """
        with self._lock:
            verified, by, started = self._verified_at, self._by, self._started_at
        if self._fresh_s <= 0:
            return FRESHNESS_NOT_ENFORCED
        if verified is None:
            return NO_STAMP_SEEN
        age = self._clock() - verified
        if verified < started:
            return (
                f"waiting for a freshness stamp from a re-hydrator round that started after "
                f"this process (newest seen is {age:.1f} s old, by {by})"
            )
        return (
            f"state was last verified against Postgres {age:.1f} s ago by {by}, over the "
            f"{self._fresh_s:g} s bound: re-hydrators absent or the store unreachable"
        )

    # --- the refresh path, once per cycle -------------------------------------------------------

    def note_freshness(self, now: float | None = None) -> bool | None:
        """Log a freshness TRANSITION, if there was one. Returns the new value, or None.

        Called once per cycle from the refresh path, never from `fresh()`. `fresh()` is on the
        request path, so logging there would produce a line per request -- which is C31's
        "instrumentation on the serving loop" defect, and it would also bury the one event an
        operator needs to see under the noise of it still being true.

        A NEW process waiting for its first stamp is not a lapse. Counting it as one would make
        every deploy and every autoscale event look like an incident.
        """
        fresh = self.fresh(now)
        with self._lock:
            previous = self._logged
            if fresh == previous:
                return None
            lapsed = previous is True and not fresh
            if lapsed:
                self._lapses += 1
            self._logged = fresh
            age = None if self._verified_at is None else (
                (self._clock() if now is None else now) - self._verified_at
            )
            by = self._by
        if fresh:
            LOG.info(
                "state_verified age_s=%s by=%s",
                "unknown" if age is None else f"{age:.3f}",
                by or "unknown",
            )
        else:
            LOG.warning("state_unverified detail=%s", self.unverified())
        return fresh

    def recheck_s(self, period_s: float, *, store_answered: bool) -> float:
        """How long until the next cycle. Shorter only while this process cannot serve.

        A process that has read the store successfully but has no usable stamp yet is not
        serving anything, so polling it harder costs nothing anyone is waiting on and saves up
        to a full period of start-up. A process that could not reach the store at all waits the
        normal period: hammering an unreachable store is how a partition becomes a thundering
        herd.
        """
        if not store_answered or self.fresh():
            return period_s
        return min(period_s, RECHECK_S)

    # --- operator-visible counters --------------------------------------------------------------

    @property
    def verified_at(self) -> float | None:
        with self._lock:
            return self._verified_at

    @property
    def by(self) -> str:
        with self._lock:
            return self._by

    @property
    def degraded(self) -> bool:
        """The newest stamp was a Postgres ride-through: its cursors did not advance."""
        with self._lock:
            return self._degraded

    @property
    def missing(self) -> int:
        with self._lock:
            return self._missing

    @property
    def invalid(self) -> int:
        with self._lock:
            return self._invalid

    @property
    def lapses(self) -> int:
        """Fresh -> not-fresh transitions. A new process's first wait is NOT one."""
        with self._lock:
            return self._lapses

    def floors(self) -> Mapping[StateKind, Cursor]:
        """Every kind's floor. For the per-kind gauge; at most one entry per enum member."""
        with self._lock:
            return dict(self._floor)

    @property
    def enforced(self) -> bool:
        return self._fresh_s > 0


def state_ready(view: StampView | None, now: float | None = None) -> tuple[bool, str | None]:
    """`(ready, why not)` for GW06's `/readyz`. Pure: no HTTP, no store, no clock of its own.

    This lives here rather than in `edge` so there is ONE definition of ready. The runbook makes
    `/readyz` 503 while state is unverified, and a second definition written next to the HTTP
    handler is how that requirement quietly stops being true.

    Deliberately keyed on freshness ALONE, not on the last round's per-kind outcome. A single
    failed round is a blip and the kinds age out on their own; treating it as un-readiness would
    flap a worker in and out of the load balancer on every store hiccup, which costs more
    availability than it buys. A kind that stays broken stops the stamp (the re-hydrator
    withholds it), and that is what turns up here.
    """
    if view is None:
        return True, None
    if view.fresh(now):
        return True, None
    return False, view.unverified()
