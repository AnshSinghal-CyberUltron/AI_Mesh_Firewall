"""GW29 `RevocationLedger`: bounded memory of key revocations for the in-flight `key_revoked` cut.

`IdentityCache.on_delta` evicts a changed key, so "not in the cache" cannot tell a revoked key from
a rate-changed one. The ledger records only REVOCATIONS, fed from the same `StateKind.KEY` delta
(`deleted=True` records).

Sequence basis (review finding 5): it stores `record.feed_seq` of the deleted KEY record, the same
per-record sequence `Principal.feed_seq` copies at admission. The MANIFEST `feed_seq` is never used
(a manifest value is the batch high-water and would make a revoke look newer than it is), so both
sides of `revoked_since(h, seq)` are the same quantity.

`floor_feed_seq` starts at -1 (no overflow yet; a stream with `feed_seq` 0 is not cut by an empty
ledger) and rises only when an entry is dropped for lack of room: a stream admitted at or below the
floor can no longer be proven un-revoked and is cut as revoked (C5, fail closed). A later
non-deleted record for the same hash never erases a revoke: the old stream is still below the revoke
and is still cut, while the re-created principal is admitted above it and is not.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence

from gateway_v2.domain.state import SignedRecord, StateKind

__all__ = ("RevocationLedger",)


class RevocationLedger:
    """Bounded `key_hash -> highest revoke feed_seq` with an overflow floor and age expiry."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        slots: int,
        retention_s: float,
        on_floor_cut: Callable[[], None] | None = None,
    ) -> None:
        if slots < 1 or retention_s <= 0:
            raise ValueError("a revocation ledger needs positive slots and retention")
        self._clock = clock
        self._slots = slots
        self._retention_s = retention_s
        self._on_floor_cut = on_floor_cut
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._floor = -1
        self._floor_cuts = 0

    @property
    def floor_feed_seq(self) -> int:
        with self._lock:
            return self._floor

    @property
    def floor_cuts_total(self) -> int:
        with self._lock:
            return self._floor_cuts

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def observe(self, records: Sequence[SignedRecord]) -> None:
        """Record every KEY revocation in a delta. Anything else is ignored; never raises."""
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            for record in records:
                if record.kind is not StateKind.KEY or not record.deleted:
                    continue
                self._record_locked(record.key, record.feed_seq, now)

    def refuse(self, key_hash: str, feed_seq: int) -> None:
        """A KEY record at index position `feed_seq` failed verification: cut like a revoke.

        The record may be a revocation this gateway could not read, so every stream admitted
        below that position is cut (`revoked_since`), exactly as a verified revoke at it would
        cut them. A principal admitted later, from a verified record at or above it, is not.
        Never raises.
        """
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            self._record_locked(key_hash, feed_seq, now)

    def _record_locked(self, key_hash: str, feed_seq: int, now: float) -> None:
        held = self._entries.pop(key_hash, None)
        top = feed_seq if held is None else max(held[0], feed_seq)
        self._entries[key_hash] = (top, now)
        while len(self._entries) > self._slots:
            _, (dropped_seq, _) = self._entries.popitem(last=False)
            self._floor = max(self._floor, dropped_seq)

    def revoked_since(self, key_hash: str, key_feed_seq: int) -> bool:
        """True if revoked after the stream was admitted, or if that can no longer be ruled out."""
        with self._lock:
            self._expire_locked(self._clock())
            if key_feed_seq <= self._floor:
                self._floor_cuts += 1
                cut = True
            else:
                held = self._entries.get(key_hash)
                return held is not None and held[0] > key_feed_seq
        if self._on_floor_cut is not None:
            self._on_floor_cut()
        return cut

    def _expire_locked(self, now: float) -> None:
        """No stream outlives `retention_s` (max duration + fresh), so older entries are dead."""
        while self._entries:
            key, (_, stamped) = next(iter(self._entries.items()))
            if now - stamped < self._retention_s:
                return
            del self._entries[key]
