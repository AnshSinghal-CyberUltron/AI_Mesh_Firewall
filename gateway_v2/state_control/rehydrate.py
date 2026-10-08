"""Restore the store's copy of durable state from Postgres, in O(1) when nothing is wrong.

RC2's re-hydrator read a kind's complete record set from Postgres AND digested it, for every one
of the four kinds, once a second — whether or not anything had changed. That is finding F6/S3,
and it is not merely wasteful: GW05b puts this component on the data plane's availability path,
because a round slower than `RV_STATE_FRESH_MS - period` produces freshness stamps that are born
stale and the whole fleet fails closed. The rc3-state-p0 README says so directly: "At 50k records
per kind, P1's O(changes) rounds are needed."

So `diagnose` reads nothing but heads:

    Postgres  one indexed counter row   (version, feed_seq, count, on_count)
    store     GET + ZCARD + ZREVRANGE + SCARD

and compares integers. A healthy kind costs that and stops. No records, no digest.

`verify` is the deep check, and it is deliberately separate and occasional. It compares the
store's full index (key -> feed_seq) against Postgres, which catches the one class `diagnose`
cannot see in O(1): a missing index entry masked by an extra, so the counts agree while the
contents do not. That is the residual the GW05c plan recorded for the short-circuit round
(A10), now closed on a slow cadence rather than by paying O(records) every second.

`repair` is the only legitimate caller of `publish_kind`: restoring a flushed store is exactly
the case where the whole kind IS the change.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.domain.state import (
    ZERO,
    Cursor,
    Manifest,
    Stamp,
    StateKind,
    StoreDataUnavailable,
)
from gateway_v2.runtime.state_sig import decode_manifest, make_stamp
from state_control.db import ControlDB, KindCounters
from state_control.publisher import StatePublisher, StoredHead
from state_control.writer import StateWriter

LOG = logging.getLogger("amf.state.rehydrate")

MISSING = "missing"
INVALID = "invalid"
STORE_AHEAD = "store_ahead"
STALE = "stale"
VERSION = "version"
COUNTS = "counts"
INDEX = "index"
ENGAGED = "engaged"
CONTENTS = "contents"

ALL_KINDS = (StateKind.PLAN, StateKind.KEY, StateKind.KS, StateKind.BUDGET)


def default_rehydrator_id() -> str:
    """`hostname:pid`. Travels in the stamp's `by` field, so a gateway log names its source."""
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True, slots=True)
class RepairEvent:
    """One restore, with the timings the re-hydration bound is computed from."""

    kind: StateKind
    reason: str
    detected_at: float
    restored_at: float
    records: int
    cursor: Cursor
    """Where the kind stands AFTER the restore. A repaired kind counts as verified."""

    @property
    def took_ms(self) -> float:
        return round((self.restored_at - self.detected_at) * 1000, 3)


@dataclass(frozen=True, slots=True)
class RoundSummary:
    """Per-kind outcome. One kind's failure never stops another's round (H7)."""

    repairs: tuple[RepairEvent, ...]
    healthy: tuple[StateKind, ...]
    errors: tuple[tuple[StateKind, str], ...]
    stamped: Stamp | None = None
    """The stamp this round MINTED. None means withheld: some kind was not verified."""

    stamp_race_lost: bool = False
    """Minted, but the store already held a newer one. Healthy when two re-hydrators run."""

    @property
    def ok(self) -> bool:
        return not self.errors


class Rehydrator:
    """Compares the published copy with Postgres and republishes what the store lost."""

    def __init__(
        self,
        db: ControlDB,
        publisher: StatePublisher,
        writer: StateWriter,
        secret: bytes,
        *,
        stale_grace_s: float = 0.25,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        kinds: Sequence[StateKind] = ALL_KINDS,
        name: str | None = None,
    ) -> None:
        self._db = db
        self._publisher = publisher
        self._writer = writer
        self._secret = secret
        self._stale_grace_s = stale_grace_s
        self._clock = clock
        self._sleep = sleep
        self._kinds = tuple(kinds)
        self._name = name or default_rehydrator_id()
        if "\n" in self._name:
            # Caught here rather than on every `make_stamp`: a bad id must fail at start-up,
            # not once a round, in a component the fleet's availability now depends on.
            raise ValueError("rehydrator id must not contain a newline")
        self._stamped: Stamp | None = None
        self._withheld = False
        # A re-hydrator watching a SUBSET of kinds can never make the whole-store claim a stamp
        # makes, so it writes none at all rather than a partial one a reader would refuse.
        self._can_stamp = set(self._kinds) >= set(StateKind)
        if not self._can_stamp:
            LOG.warning(
                "re-hydrator %s covers only %s: it will write no freshness stamp",
                self._name,
                ", ".join(kind.value for kind in self._kinds),
            )

    # --- O(1) ----------------------------------------------------------------------------------

    def diagnose(self, kind: StateKind) -> str | None:
        """None when the store agrees with Postgres. Reads no records and no bodies."""
        return self._diagnose(kind)[0]

    def _diagnose(self, kind: StateKind) -> tuple[str | None, KindCounters]:
        """`(reason, the Postgres counters compared)`. The store is read BEFORE Postgres.

        That order is load-bearing. A write that commits and publishes BETWEEN the two reads
        would otherwise show the store ahead of an already-stale Postgres read -- and `repair`
        answers `store_ahead` with an epoch bump, so an ordinary healthy concurrent write would
        spuriously bump the epoch. Read this way round the same race reads as `stale`, which the
        grace re-check in `_reason` then clears without touching anything.

        GW05b also needs the counters themselves: they are the cursor the freshness stamp
        attests for this kind, so `diagnose` can no longer throw them away.
        """
        head = self._publisher.stored_head(kind)
        counters = self._db.counters(kind)
        if head.manifest_raw is None:
            return MISSING, counters
        try:
            manifest = decode_manifest(self._secret, kind, head.manifest_raw, not_before=ZERO)
        except StoreDataUnavailable:
            return INVALID, counters
        return self._compare(kind, manifest, head, counters), counters

    def _compare(
        self,
        kind: StateKind,
        manifest: Manifest,
        head: StoredHead,
        counters: KindCounters,
    ) -> str | None:
        if manifest.feed_seq > counters.feed_seq:
            # A version Postgres never issued. Repairing needs an epoch bump first, so that the
            # republished state is newer than anything a gateway has already applied.
            return STORE_AHEAD
        if manifest.feed_seq < counters.feed_seq:
            return STALE
        if manifest.version != counters.version:
            return VERSION
        if manifest.count != counters.count or manifest.on_count != counters.on_count:
            return COUNTS
        if head.index_count < manifest.count or head.index_top < manifest.feed_seq:
            return INDEX
        if kind is StateKind.KS and head.engaged_count != counters.on_count:
            return ENGAGED
        return None

    # --- O(records), on a slow cadence ---------------------------------------------------------

    def verify(self, kind: StateKind) -> str | None:
        """Deep check: the store's whole index against Postgres's positions.

        Catches a missing entry masked by an extra, which keeps the counts agreeing while the
        contents disagree. Positions are inside each record's signature, so comparing
        key -> feed_seq is sufficient and far cheaper than reading every body.
        """
        _counters, records, _engaged = self._db.snapshot(kind)
        expected = {record.key: record.feed_seq for record in records}
        published = dict(self._publisher.stored_index(kind))
        return None if published == expected else CONTENTS

    # --- repair ---------------------------------------------------------------------------------

    def repair(self, kind: StateKind, reason: str) -> RepairEvent:
        """Republish the whole kind from Postgres. The ONLY legitimate whole-kind publish."""
        detected_at = self._clock()
        if reason == STORE_AHEAD:
            self._writer.repair_epoch(kind)
        counters, records, engaged = self._db.snapshot(kind)
        manifest = self._writer.manifest_for(kind, counters)
        self._publisher.publish_kind(kind, records, manifest, engaged)
        event = RepairEvent(
            kind=kind,
            reason=reason,
            detected_at=detected_at,
            restored_at=self._clock(),
            records=len(records),
            # The position the store now holds, which is what the stamp must attest for this
            # kind. The pre-repair counters are the wrong answer: an epoch bump moved them.
            cursor=Cursor(version=manifest.version, feed_seq=manifest.feed_seq),
        )
        LOG.warning(
            "rehydrated kind=%s reason=%s records=%d took_ms=%s",
            kind.value,
            reason,
            event.records,
            event.took_ms,
        )
        return event

    def round_once(self, *, deep: bool = False) -> RoundSummary:
        """One round over every kind, isolated. Never raises for a single kind's failure."""
        started_at = self._clock()
        repairs: list[RepairEvent] = []
        healthy: list[StateKind] = []
        errors: list[tuple[StateKind, str]] = []
        verified: dict[StateKind, Cursor] = {}
        for kind in self._kinds:
            try:
                reason, counters = self._reason(kind, deep=deep)
                if reason is None:
                    healthy.append(kind)
                    verified[kind] = Cursor(counters.version, counters.feed_seq)
                else:
                    event = self.repair(kind, reason)
                    repairs.append(event)
                    verified[kind] = event.cursor
            except Exception as exc:  # noqa: BLE001 - store, driver and OS errors alike
                LOG.warning("rehydrate round failed for kind=%s: %s", kind.value, exc)
                errors.append((kind, f"{type(exc).__name__}: {exc}"))
        minted, won = self._stamp(started_at, verified, deep=deep)
        return RoundSummary(
            repairs=tuple(repairs),
            healthy=tuple(healthy),
            errors=tuple(errors),
            stamped=minted,
            stamp_race_lost=minted is not None and not won,
        )

    def _reason(self, kind: StateKind, *, deep: bool) -> tuple[str | None, KindCounters]:
        reason, counters = self._diagnose(kind)
        if reason == STALE and self._stale_grace_s > 0:
            # A writer publishes right after its commit; that in-flight publish is not a fault.
            self._sleep(self._stale_grace_s)
            reason, counters = self._diagnose(kind)
        if reason is not None:
            return reason, counters
        return (self.verify(kind) if deep else None), counters

    # --- the freshness stamp (GW05b) -----------------------------------------------------------

    @property
    def last_stamp(self) -> Stamp | None:
        """The newest stamp this re-hydrator MINTED, won the race or not."""
        return self._stamped

    def _stamp(
        self,
        verified_at: float,
        verified: Mapping[StateKind, Cursor],
        *,
        deep: bool,
    ) -> tuple[Stamp | None, bool]:
        """`(the stamp this round minted, whether it won the race)`.

        Only a round that verified EVERY kind stamps anything. The obvious alternative --
        stamp the kinds that did succeed -- would let one permanently broken kind sit behind a
        fresh-looking stamp forever. Withholding means freshness LAPSES instead, and the fleet
        fails closed once the last stamp ages past the bound. Honest silence beats a confident
        half-truth here, because the half that is missing is the half that is broken.
        """
        if not self._can_stamp:
            return None, False
        missing = sorted(kind.value for kind in StateKind if kind not in verified)
        if missing:
            if not self._withheld:
                LOG.warning(
                    "freshness stamp withheld: %s not verified this round; gateways fail "
                    "closed once the last stamp ages past the freshness bound",
                    ", ".join(missing),
                )
            self._withheld = True
            return None, False
        stamp = make_stamp(self._secret, verified_at, verified, self._name, deep=deep)
        try:
            won = self._publisher.put_stamp(stamp)
        except Exception as exc:  # noqa: BLE001 - store, driver and OS errors alike
            LOG.warning("freshness stamp could not be written: %s", exc)
            self._withheld = True
            return None, False
        if self._stamped is None or self._withheld:
            LOG.info(
                "freshness stamp %s by %s at %.3f",
                "resumed" if self._withheld else "started",
                self._name,
                stamp.verified_at,
            )
        self._withheld = False
        self._stamped = stamp
        return stamp, won
