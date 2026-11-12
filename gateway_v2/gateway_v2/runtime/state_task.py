"""Own the cursors, run one bounded round per kind, and keep the CPU off the serving loop.

Three properties this module exists to guarantee, each measured by R2-02 or named by GW05b:

* **Bounded per round.** A round applies at most `budget.records`, so a bulk onboard of 25,000
  tenants converges over several rounds instead of one long stall. §2.1 rule (1) is about work
  proportional to the estate; an unbounded delta would reintroduce it by another route.
* **Off the loop above a threshold.** Verifying and compiling a large delta is CPU. Above
  `budget.offload_above` records it runs in a GIL-releasing thread, the same seam
  `RV_TOKENIZE_IN_THREAD` already uses. Small deltas stay inline, where a thread hop would cost
  more than the work.
* **Per-kind isolation.** One kind's store or database error never stalls another's rounds
  (H7 / the rc3-state-p0 re-hydrator fix). `drain_all` reports every kind's outcome and raises
  for none of them; the caller decides what a failure means for serving. That holds for ANY
  exception a reader or an applier raises, not only `StoreDataUnavailable`: before it did, one
  tenant's record that made the plan compiler raise `RecursionError` escaped `round_once`,
  aborted `drain_all` before the kill-switch, key and budget kinds were read, and did so again
  every cycle until every gate aged out -- on every gateway, because they all read that record.
* **Per-record isolation, for the kinds that can fail one subject closed.** A kind listed in
  `isolate_kinds` is polled with `isolate=True`: a record that fails verification arrives as a
  `RecordFault` on the round and its applier fails that one subject closed. The round, its
  cursor and every other tenant move on.

A failed round does NOT advance the cursor. The next round retries from the same position, so a
partially applied generation is never mistaken for a complete one.

Logging names the kind and the exception TYPE only. Exception text from a decoder or a compiler
can quote the record that caused it, and that record is a tenant's data.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.domain.state import START, Cursor, StateKind, StoreDataUnavailable
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_stamp import StampView

LOG = logging.getLogger("amf.gateway.state")

Applier = Callable[[FeedRound], object]
"""Applies one round's records. Synchronous, so it may be handed to a worker thread.

The return value is discarded: an applier may report an outcome for metrics without the
synchroniser having to know the shape of it.
"""

Offload = Callable[[Callable[[], object]], Awaitable[None]]
"""Runs a synchronous apply somewhere other than the calling loop."""


@runtime_checkable
class RejectionCount(Protocol):
    """An applier outcome that counts the verified records it refused to apply.

    Structural, because the plan applier's `ApplyOutcome` lives in `plan`, above this layer.
    An outcome without it counts zero.
    """

    @property
    def rejected_count(self) -> int:
        ...

MAX_DRAIN_ROUNDS = 64
"""A drain stops here and reports lag rather than monopolising the loop."""


@dataclass(frozen=True, slots=True)
class DeltaBudget:
    """How much one round may do. Both numbers are about loop occupancy, not about capacity."""

    records: int = 256
    offload_above: int = 32


DEFAULT_BUDGET = DeltaBudget()


@dataclass(frozen=True, slots=True)
class RoundReport:
    """What one round did. Every field is O(1), so it is safe to meter per kind."""

    kind: StateKind
    cursor: Cursor
    applied: int
    truncated: bool
    offloaded: bool
    error: str | None = None
    rejected: int = 0
    """Verified records the applier refused (the plan compiler's rejections): last-good kept."""

    quarantined: int = 0
    """Records that failed verification on an isolating kind: that subject is failed closed."""

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def lag(self) -> int:
        """Records still behind the attested position, as far as this round could tell."""
        return 0 if not self.truncated else -1


async def _inline(work: Callable[[], object]) -> None:
    work()


async def _in_thread(work: Callable[[], object]) -> None:
    await asyncio.to_thread(work)


class StateSynchroniser:
    """Holds one cursor per kind and drives the feed. The only owner of cursor advancement."""

    def __init__(
        self,
        reader: FeedReader,
        appliers: Mapping[StateKind, Applier],
        *,
        budget: DeltaBudget = DEFAULT_BUDGET,
        offload: Offload = _in_thread,
        stamp: StampView | None = None,
        inline_kinds: frozenset[StateKind] = frozenset(),
        isolate_kinds: frozenset[StateKind] = frozenset(),
    ) -> None:
        """`inline_kinds`: appliers that must never leave the loop, whatever the round size.

        The identity cache is unlocked loop-owned state (GW36 S3, N8); a thread-run applier
        would mutate it concurrently with request-path reads. The plan and kill-switch
        appliers lock, so they keep the offload.

        `isolate_kinds`: kinds read with per-record isolation. List a kind here ONLY when its
        applier fails every `round_.faults` subject closed: an applier that ignored them would
        serve those subjects their pre-fault state, which is failing open.
        """
        if budget.records <= 0:
            raise ValueError("delta budget must be positive")
        self._reader = reader
        self._appliers = dict(appliers)
        self._budget = budget
        self._offload = offload
        self._stamp = stamp
        self._inline = inline_kinds
        self._isolate = isolate_kinds
        self._cursors: dict[StateKind, Cursor] = {kind: START for kind in appliers}
        self._floors: dict[StateKind, Cursor] = {kind: START for kind in appliers}
        self._failing: dict[StateKind, str] = {}
        """Each kind's current unexpected failure, so a standing fault logs once, not per cycle."""

    def cursor(self, kind: StateKind) -> Cursor:
        """Where this process IS: the generation it has actually applied."""
        return self._cursors.get(kind, START)

    def floor(self, kind: StateKind) -> Cursor:
        """The oldest generation this process may ACCEPT. Raised by GW05b's stamp."""
        return self._floors.get(kind, START)

    def raise_floor(self, kind: StateKind, floor: Cursor) -> None:
        """Raise the acceptance floor without claiming anything was applied.

        The distinction is the whole point and it is easy to collapse by accident. A process
        must not start from zero on a lagging replica, so a signed stamp raises what it will
        ACCEPT. It must not also move the read cursor: a fresh process that jumped its cursor to
        the stamp's position would short-circuit its first round, apply nothing, and then serve
        `PlanUnknownTenant` -- a 503 `plan_unavailable` for every healthy tenant -- for every
        org until the next write happened to touch it.

        Floors only rise, and a stamp whose version and position disagree about direction is
        refused outright rather than adopted by halves.
        """
        current = self.floor(kind)
        if floor.feed_seq < current.feed_seq or floor.version < current.version:
            return
        self._floors[kind] = floor

    async def round_once(self, kind: StateKind) -> RoundReport:
        """One bounded round. Reports ANY failure instead of raising, so siblings keep running.

        `StoreDataUnavailable` is the expected refusal (an outage, drift, an ambiguous switch)
        and its message is code-written, so it is carried. Anything else is a defect -- a
        decoder or compiler raising on data it should have refused -- and is reported and logged
        by type alone. Either way the cursor stays put and the next round retries.
        """
        applier = self._appliers.get(kind)
        if applier is None:
            raise KeyError(f"no applier registered for {kind.value}")
        cursor = self.cursor(kind)
        try:
            round_ = await self._poll(kind, cursor)
        except Exception as exc:  # noqa: BLE001 - one kind's fault must not stop its siblings
            return self._failed(kind, cursor, exc, offloaded=False)
        offloaded = len(round_.records) > self._budget.offload_above and kind not in self._inline
        runner = self._offload if offloaded else _inline
        outcome: list[object] = []
        try:
            await runner(lambda: outcome.append(applier(round_)))
        except Exception as exc:  # noqa: BLE001 - the applier refused the delta; cursor stays
            return self._failed(kind, cursor, exc, offloaded=offloaded)
        self._cursors[kind] = round_.cursor
        self._recovered(kind)
        result = outcome[0] if outcome else None
        return RoundReport(
            kind=kind,
            cursor=round_.cursor,
            applied=len(round_.records),
            truncated=round_.truncated,
            offloaded=offloaded,
            rejected=result.rejected_count if isinstance(result, RejectionCount) else 0,
            quarantined=len(round_.faults),
        )

    async def _poll(self, kind: StateKind, cursor: Cursor) -> FeedRound:
        """`isolate` is passed only when set, so a non-isolating kind keeps its old call shape."""
        limit, floor = self._budget.records, self.floor(kind)
        if kind in self._isolate:
            return await self._reader.poll(kind, cursor, limit=limit, floor=floor, isolate=True)
        return await self._reader.poll(kind, cursor, limit=limit, floor=floor)

    def _failed(
        self,
        kind: StateKind,
        cursor: Cursor,
        exc: Exception,
        *,
        offloaded: bool,
    ) -> RoundReport:
        """A refused round, as a report. Logs a defect once per change, by type only."""
        if isinstance(exc, StoreDataUnavailable):
            error = str(exc)
        else:
            error = type(exc).__name__
            if self._failing.get(kind) != error:
                LOG.error("state round failed: kind=%s error=%s", kind.value, error)
            self._failing[kind] = error
        return RoundReport(kind, cursor, 0, truncated=False, offloaded=offloaded, error=error)

    def _recovered(self, kind: StateKind) -> None:
        previous = self._failing.pop(kind, None)
        if previous is not None:
            LOG.warning("state round recovered: kind=%s after error=%s", kind.value, previous)

    async def drain(self, kind: StateKind, *, max_rounds: int = MAX_DRAIN_ROUNDS) -> RoundReport:
        """Round until caught up, bounded. Reports the LAST round, so lag stays visible."""
        report = await self.round_once(kind)
        rounds = 1
        while report.ok and report.truncated and rounds < max_rounds:
            report = await self.round_once(kind)
            rounds += 1
        return report

    async def observe_stamp(self) -> bool:
        """Read the freshness stamp and raise every kind's floor from it. GW05b's call site.

        **This runs before any kind is polled, and the order is the fix rather than an
        optimisation.** `poll` hands the cursor's version and position to `decode_manifest` as
        the anti-regress floor, so a floor raised AFTER a round arrives exactly one round too
        late -- and the round it missed is the first one a fresh process runs, which is precisely
        the SP1 window. A `raise_floor` after the fact would look right in review and fix
        nothing.

        Returns whether a stamp was adopted. A missing or forged one changes no floor, so the
        process keeps whatever it had and ages into "not fresh" on its own.
        """
        if self._stamp is None:
            return False
        adopted = self._stamp.observe(await self._reader.read_stamp())
        for kind in self._appliers:
            self.raise_floor(kind, self._stamp.floor(kind))
        # Once per cycle, on the refresh path. `fresh()` is read per REQUEST, so a transition
        # log belongs here and nowhere near it.
        self._stamp.note_freshness()
        return adopted

    def next_delay_s(
        self,
        reports: Sequence[RoundReport],
        *,
        period_s: float,
    ) -> float:
        """How long the caller should wait before the next cycle.

        Shorter than the period only while this process has a working store connection and no
        usable stamp -- in other words while it is not serving anything, so the extra polling
        costs nothing anyone is waiting on. GW06's loop calls this; there is no loop here on
        purpose, because the serving process owns its own scheduling.
        """
        if self._stamp is None:
            return period_s
        return self._stamp.recheck_s(period_s, store_answered=any(r.ok for r in reports))

    async def drain_all(self, *, max_rounds: int = MAX_DRAIN_ROUNDS) -> tuple[RoundReport, ...]:
        """One cycle: observe the stamp, then drain every kind, isolated.

        This is the product entry point, and the only place the stamp is read. `round_once` and
        `drain` are the per-kind primitives underneath it and assume the floor is already
        current, so product code schedules THIS.
        """
        try:
            await self.observe_stamp()
        except StoreDataUnavailable:
            # The store did not answer the stamp read. The view keeps what it had and ages out
            # on its own; the kinds are still drained, each reporting its own outcome.
            pass
        except Exception as exc:  # noqa: BLE001 - a stamp defect never stops the kinds' drains
            # Same as a missing stamp: no floor moves, the view ages out on its own. Logged by
            # type, because unlike a store outage this is a defect someone must look at.
            LOG.error("freshness stamp read failed: error=%s", type(exc).__name__)
        return tuple(
            [await self.drain(kind, max_rounds=max_rounds) for kind in self._appliers],
        )

    async def fast_forward(
        self,
        kind: StateKind,
        mark: Callable[[int], object],
    ) -> RoundReport:
        """Cold start a kind whose RAM holds nothing to evict: jump the cursor to its head.

        For the identity cache (GW36 S3): principals are fetched on demand, so replaying every
        key record only to evict entries from an empty cache is O(keys) per worker per boot
        for nothing. The head is read under the stamp floor, so a lagging store is refused
        here exactly as a delta round would refuse it. `mark` receives the applied position.
        """
        cursor = self.cursor(kind)
        try:
            manifest = await self._reader.head(kind, cursor, self.floor(kind))
        except Exception as exc:  # noqa: BLE001 - reported like any refused round
            return self._failed(kind, cursor, exc, offloaded=False)
        moved = Cursor(version=manifest.version, feed_seq=manifest.feed_seq)
        mark(manifest.feed_seq)
        self._cursors[kind] = moved
        self._recovered(kind)
        return RoundReport(kind=kind, cursor=moved, applied=0, truncated=False, offloaded=False)

    async def bootstrap_engaged(
        self,
        kind: StateKind,
        adopt: Callable[[FeedRound], object],
    ) -> RoundReport:
        """Cold start a kind from its engaged set, O(engaged), and jump the cursor to it."""
        cursor = self.cursor(kind)
        try:
            manifest, records = await self._reader.bootstrap_engaged(
                kind, cursor, self.floor(kind),
            )
            round_ = FeedRound(
                kind=kind,
                manifest=manifest,
                records=records,
                cursor=Cursor(version=manifest.version, feed_seq=manifest.feed_seq),
                truncated=False,
            )
            adopt(round_)
        except Exception as exc:  # noqa: BLE001 - reported like any refused round
            return self._failed(kind, cursor, exc, offloaded=False)
        self._cursors[kind] = round_.cursor
        self._recovered(kind)
        return RoundReport(
            kind=kind,
            cursor=round_.cursor,
            applied=len(records),
            truncated=False,
            offloaded=False,
        )
