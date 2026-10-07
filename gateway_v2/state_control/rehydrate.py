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
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gateway_v2.domain.state import ZERO, StateKind, StoreDataUnavailable
from gateway_v2.runtime.state_sig import decode_manifest
from state_control.db import ControlDB
from state_control.publisher import StatePublisher
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


@dataclass(frozen=True, slots=True)
class RepairEvent:
    """One restore, with the timings the re-hydration bound is computed from."""

    kind: StateKind
    reason: str
    detected_at: float
    restored_at: float
    records: int

    @property
    def took_ms(self) -> float:
        return round((self.restored_at - self.detected_at) * 1000, 3)


@dataclass(frozen=True, slots=True)
class RoundSummary:
    """Per-kind outcome. One kind's failure never stops another's round (H7)."""

    repairs: tuple[RepairEvent, ...]
    healthy: tuple[StateKind, ...]
    errors: tuple[tuple[StateKind, str], ...]

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
    ) -> None:
        self._db = db
        self._publisher = publisher
        self._writer = writer
        self._secret = secret
        self._stale_grace_s = stale_grace_s
        self._clock = clock
        self._sleep = sleep
        self._kinds = tuple(kinds)

    # --- O(1) ----------------------------------------------------------------------------------

    def diagnose(self, kind: StateKind) -> str | None:
        """None when the store agrees with Postgres. Reads no records and no bodies."""
        counters = self._db.counters(kind)
        head = self._publisher.stored_head(kind)
        if head.manifest_raw is None:
            return MISSING
        try:
            manifest = decode_manifest(self._secret, kind, head.manifest_raw, not_before=ZERO)
        except StoreDataUnavailable:
            return INVALID
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
        repairs: list[RepairEvent] = []
        healthy: list[StateKind] = []
        errors: list[tuple[StateKind, str]] = []
        for kind in self._kinds:
            try:
                reason = self._reason(kind, deep=deep)
                if reason is None:
                    healthy.append(kind)
                    continue
                repairs.append(self.repair(kind, reason))
            except Exception as exc:  # noqa: BLE001 - store, driver and OS errors alike
                LOG.warning("rehydrate round failed for kind=%s: %s", kind.value, exc)
                errors.append((kind, f"{type(exc).__name__}: {exc}"))
        return RoundSummary(tuple(repairs), tuple(healthy), tuple(errors))

    def _reason(self, kind: StateKind, *, deep: bool) -> str | None:
        reason = self.diagnose(kind)
        if reason == STALE and self._stale_grace_s > 0:
            # A writer publishes right after its commit; that in-flight publish is not a fault.
            self._sleep(self._stale_grace_s)
            reason = self.diagnose(kind)
        if reason is not None:
            return reason
        return self.verify(kind) if deep else None
