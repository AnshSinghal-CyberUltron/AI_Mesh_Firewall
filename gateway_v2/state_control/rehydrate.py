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

from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.state import (
    ZERO,
    Cursor,
    Manifest,
    SignedRecord,
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


class ControlPlaneUnavailable(Exception):
    """Postgres could not be read this round.

    Deliberately distinct from a store fault and from a data fault, because it is the ONE
    failure the ride-through may cover. Raised only from the re-hydrator's own database call
    sites AND only for I/O-shaped errors, so a programming error in the database layer still
    withholds the stamp rather than being ridden out for the whole grace window.
    """


def _db_faults() -> tuple[type[BaseException], ...]:
    """Error classes that mean "the database did not answer", not "the database said no".

    Discovered lazily, like the store adapter's WATCH errors: `MemoryControlDB` needs no
    psycopg, so importing it at module scope would make the twin depend on a driver it does not
    use. `ConnectionError` and `TimeoutError` are both `OSError`, so the twin's injected failure
    is covered by the first entry.
    """
    faults: list[type[BaseException]] = [OSError]
    try:
        import psycopg
    except ImportError:  # pragma: no cover - psycopg is a declared dependency
        return tuple(faults)
    faults.append(psycopg.Error)
    return tuple(faults)


_DB_FAULTS = _db_faults()


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
        fresh_ms: int = FRESH_MS,
        pg_grace_ms: int = PG_GRACE_MS,
    ) -> None:
        if pg_grace_ms != 0 and pg_grace_ms < fresh_ms:
            # A grace shorter than the gateways' freshness bound cannot ride anything through:
            # the fleet would fail closed before the window it is meant to cover had ended.
            # Rejected at construction, because a component the fleet's availability depends on
            # must not discover its own configuration is incoherent during an outage.
            raise ValueError(
                f"pg_grace_ms={pg_grace_ms} must be 0 (off) or at least fresh_ms={fresh_ms}",
            )
        self._db = db
        self._publisher = publisher
        self._writer = writer
        self._secret = secret
        self._stale_grace_s = stale_grace_s
        self._clock = clock
        self._sleep = sleep
        self._kinds = tuple(kinds)
        self._pg_grace_s = pg_grace_ms / 1000
        self._name = name or default_rehydrator_id()
        if "\n" in self._name:
            # Caught here rather than on every `make_stamp`: a bad id must fail at start-up,
            # not once a round, in a component the fleet's availability now depends on.
            raise ValueError("rehydrator id must not contain a newline")
        self._stamped: Stamp | None = None
        self._verified: Stamp | None = None
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
        counters = self._counters(kind)
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
        _counters, records, _engaged = self._snapshot(kind)
        expected = {record.key: record.feed_seq for record in records}
        published = dict(self._publisher.stored_index(kind))
        return None if published == expected else CONTENTS

    # --- the two database call sites, so a Postgres outage is attributable --------------------

    def _counters(self, kind: StateKind) -> KindCounters:
        try:
            return self._db.counters(kind)
        except _DB_FAULTS as exc:
            raise ControlPlaneUnavailable(f"{kind.value} counters: {exc}") from exc

    def _snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        try:
            return self._db.snapshot(kind)
        except _DB_FAULTS as exc:
            raise ControlPlaneUnavailable(f"{kind.value} snapshot: {exc}") from exc

    # --- repair ---------------------------------------------------------------------------------

    def repair(self, kind: StateKind, reason: str) -> RepairEvent:
        """Republish the whole kind from Postgres. The ONLY legitimate whole-kind publish."""
        detected_at = self._clock()
        if reason == STORE_AHEAD:
            self._writer.repair_epoch(kind)
        counters, records, engaged = self._snapshot(kind)
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
        db_down = False
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
            except ControlPlaneUnavailable as exc:
                db_down = True
                LOG.warning("postgres unavailable for kind=%s: %s", kind.value, exc)
                errors.append((kind, f"ControlPlaneUnavailable: {exc}"))
            except Exception as exc:  # noqa: BLE001 - store, driver and OS errors alike
                LOG.warning("rehydrate round failed for kind=%s: %s", kind.value, exc)
                errors.append((kind, f"{type(exc).__name__}: {exc}"))
        minted, won = self._stamp(started_at, verified, deep=deep, db_down=db_down)
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

    @property
    def last_verified_stamp(self) -> Stamp | None:
        """The newest stamp minted from a REAL Postgres comparison. The ride-through's anchor."""
        return self._verified

    def _stamp(
        self,
        verified_at: float,
        verified: Mapping[StateKind, Cursor],
        *,
        deep: bool,
        db_down: bool,
    ) -> tuple[Stamp | None, bool]:
        """`(the stamp this round minted, whether it won the race)`."""
        if not self._can_stamp:
            return None, False
        claim = self._claimable(verified_at, verified, db_down=db_down)
        if claim is None:
            return None, False
        cursors, degraded = claim
        stamp = make_stamp(
            self._secret,
            verified_at,
            cursors,
            self._name,
            # A ride-through round compared nothing, so it cannot claim a deep comparison.
            deep=deep and not degraded,
            degraded=degraded,
        )
        try:
            won = self._publisher.put_stamp(stamp)
        except Exception as exc:  # noqa: BLE001 - store, driver and OS errors alike
            LOG.warning("freshness stamp could not be written: %s", exc)
            self._withheld = True
            return None, False
        if self._stamped is None or self._withheld:
            LOG.info(
                "freshness stamp %s by %s at %.3f%s",
                "resumed" if self._withheld else "started",
                self._name,
                stamp.verified_at,
                " (degraded: riding out a postgres outage)" if degraded else "",
            )
        self._withheld = False
        self._stamped = stamp
        if not degraded:
            # The anchor the ride-through window is measured from. Only a round that actually
            # compared the store with Postgres may move it.
            self._verified = stamp
        return stamp, won

    def _claimable(
        self,
        verified_at: float,
        verified: Mapping[StateKind, Cursor],
        *,
        db_down: bool,
    ) -> tuple[Mapping[StateKind, Cursor], bool] | None:
        """`(cursors, degraded)` this round may attest, or None to withhold.

        Only a round that verified EVERY kind stamps a normal claim. The obvious alternative --
        stamp the kinds that did succeed -- would let one permanently broken kind sit behind a
        fresh-looking stamp forever. Withholding makes freshness LAPSE instead, and the fleet
        fails closed once the last stamp ages past the bound. Honest silence beats a confident
        half-truth, because the missing half is the broken half.
        """
        missing = sorted(kind.value for kind in StateKind if kind not in verified)
        if not missing:
            return dict(verified), False
        if verified or not db_down:
            # Some kind DID get a real comparison, so Postgres is reachable and a kind that
            # failed anyway is a genuine fault -- or the failure was never Postgres at all.
            self._withhold(missing, "not verified this round")
            return None
        ride = self._ride_through(verified_at)
        if ride is None:
            self._withhold(missing, "postgres unreachable and the ride-through does not apply")
            return None
        return ride, True

    def _ride_through(self, verified_at: float) -> Mapping[StateKind, Cursor] | None:
        """The cursors a Postgres outage may keep claiming, or None when it may not.

        Without this, the per-kind fail-closed posture turns a ROUTINE event into an outage: a
        Cloud SQL failover takes 11-16 s, and no Postgres means no verification means no stamp
        means the whole fleet refuses. The reference patch measured about 7.4 s of global 503 on
        a 10 s freeze before this clause existed, and L05b-4 requires zero.

        Safe, because the store is re-checked against the LAST VERIFIED cursors: if it still
        holds everything that was previously attested, nothing already enforced has been lost.
        Bounded, because a write committed DURING the outage cannot be published either -- the
        writer needs Postgres too -- so the window of possible non-enforcement is exactly
        PG_GRACE_MS, declared rather than open.
        """
        anchor = self._verified
        if anchor is None:
            return None  # nothing was ever verified, so there is no claim to extend
        # Measured from the last VERIFIED stamp, never the last WRITTEN one. Anchoring on the
        # latter would let each degraded stamp reset the clock and the ride never end.
        if verified_at - anchor.verified_at > self._pg_grace_s:
            return None
        if not self._store_holds(anchor.cursors):
            return None
        return anchor.cursors

    def _store_holds(self, cursors: Mapping[StateKind, Cursor]) -> bool:
        """Is every kind's published head still at or above what was last verified?"""
        for kind, cursor in cursors.items():
            try:
                head = self._publisher.stored_head(kind)
                manifest = decode_manifest(
                    self._secret, kind, head.manifest_raw, not_before=ZERO,
                )
            except Exception as exc:  # noqa: BLE001 - a store or data fault voids the claim
                LOG.warning("ride-through check failed for kind=%s: %s", kind.value, exc)
                return False
            if manifest.version < cursor.version or manifest.feed_seq < cursor.feed_seq:
                LOG.warning(
                    "ride-through void: kind=%s is at %s/%d, last verified %s/%d",
                    kind.value,
                    manifest.version,
                    manifest.feed_seq,
                    cursor.version,
                    cursor.feed_seq,
                )
                return False
        return True

    def _withhold(self, missing: Sequence[str], why: str) -> None:
        if not self._withheld:
            LOG.warning(
                "freshness stamp withheld (%s): %s; gateways fail closed once the last stamp "
                "ages past the freshness bound",
                why,
                ", ".join(missing),
            )
        self._withheld = True
