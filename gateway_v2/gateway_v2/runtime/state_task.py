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
  for none of them; the caller decides what a failure means for serving.

A failed round does NOT advance the cursor. The next round retries from the same position, so a
partially applied generation is never mistaken for a complete one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.domain.state import START, Cursor, StateKind, StoreDataUnavailable
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_stamp import StampView

Applier = Callable[[FeedRound], object]
"""Applies one round's records. Synchronous, so it may be handed to a worker thread.

The return value is discarded: an applier may report an outcome for metrics without the
synchroniser having to know the shape of it.
"""

Offload = Callable[[Callable[[], object]], Awaitable[None]]
"""Runs a synchronous apply somewhere other than the calling loop."""

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
    ) -> None:
        if budget.records <= 0:
            raise ValueError("delta budget must be positive")
        self._reader = reader
        self._appliers = dict(appliers)
        self._budget = budget
        self._offload = offload
        self._stamp = stamp
        self._cursors: dict[StateKind, Cursor] = {kind: START for kind in appliers}
        self._floors: dict[StateKind, Cursor] = {kind: START for kind in appliers}

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
        `PlanUnknownTenant` -- a 403 telling healthy tenants to complete onboarding -- for every
        org until the next write happened to touch it.

        Floors only rise, and a stamp whose version and position disagree about direction is
        refused outright rather than adopted by halves.
        """
        current = self.floor(kind)
        if floor.feed_seq < current.feed_seq or floor.version < current.version:
            return
        self._floors[kind] = floor

    async def round_once(self, kind: StateKind) -> RoundReport:
        """One bounded round. Reports a failure instead of raising, so siblings keep running."""
        applier = self._appliers.get(kind)
        if applier is None:
            raise KeyError(f"no applier registered for {kind.value}")
        cursor = self.cursor(kind)
        try:
            round_ = await self._reader.poll(
                kind, cursor, limit=self._budget.records, floor=self.floor(kind),
            )
        except StoreDataUnavailable as exc:
            return RoundReport(kind, cursor, 0, truncated=False, offloaded=False, error=str(exc))
        offloaded = len(round_.records) > self._budget.offload_above
        runner = self._offload if offloaded else _inline
        try:
            await runner(lambda: applier(round_))
        except StoreDataUnavailable as exc:
            # The applier refused the delta (drift, an ambiguous switch). The cursor stays put.
            return RoundReport(
                kind, cursor, 0, truncated=False, offloaded=offloaded, error=str(exc),
            )
        self._cursors[kind] = round_.cursor
        return RoundReport(
            kind=kind,
            cursor=round_.cursor,
            applied=len(round_.records),
            truncated=round_.truncated,
            offloaded=offloaded,
        )

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
        return adopted

    async def drain_all(self, *, max_rounds: int = MAX_DRAIN_ROUNDS) -> tuple[RoundReport, ...]:
        """One cycle: observe the stamp, then drain every kind, isolated.

        This is the product entry point, and the only place the stamp is read. `round_once` and
        `drain` are the per-kind primitives underneath it and assume the floor is already
        current, so product code schedules THIS.
        """
        await self.observe_stamp()
        return tuple(
            [await self.drain(kind, max_rounds=max_rounds) for kind in self._appliers],
        )

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
        except StoreDataUnavailable as exc:
            return RoundReport(kind, cursor, 0, truncated=False, offloaded=False, error=str(exc))
        self._cursors[kind] = round_.cursor
        return RoundReport(
            kind=kind,
            cursor=round_.cursor,
            applied=len(records),
            truncated=False,
            offloaded=False,
        )
