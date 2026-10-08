"""The publish transport, and an in-memory twin that is ALSO a reader store.

`publish_record` is the fix for R2-02's third defect. RC2 republished a kind's complete record
set on every write — `DEL` plus `HSET` of every record inside one `MULTI` — which measured
3.5 MB / 23.8 ms at 10,000 keys and 17.4 MB / 124 ms at 50,000, blocking a single-threaded store
past the 25 ms request-path timeout. Live publish p50 reached 1.72 s at 25,000 tenants.

A write now touches five keys, whatever the estate size:

    SET/HSET  the one record
    ZADD      its index entry at feed_seq
    SADD/SREM the engaged set            (kill switch only)
    SET       the manifest
    PUBLISH   the nudge

`publish_kind` still exists, and still rewrites everything, but only the re-hydrator may call it:
restoring a flushed store is exactly the case where the whole kind IS the change. A test asserts
the write path never reaches it.

Ordering rule for every implementation: **the manifest is written last**. A reader therefore sees
either the old manifest (and skips, because `feed_seq` has not moved) or the new one (whose index
entries are already present). The reader bounds everything it does by the manifest, so an index
running ahead during a batch is normal rather than a fault.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain.state import (
    Manifest,
    SignedRecord,
    Stamp,
    StateKind,
)
from gateway_v2.runtime.state_feed import Head, IndexPage
from gateway_v2.runtime.state_sig import encode_manifest, encode_record, encode_stamp


@dataclass(frozen=True, slots=True)
class StoredHead:
    """What the re-hydrator reads to decide whether the store agrees with Postgres.

    Every field is O(1) to obtain (`GET`, `ZCARD`, `ZREVRANGE 0 0`, `SCARD`), which is what makes
    a round that finds nothing wrong cost nothing. RC2 read the whole kind from Postgres AND
    digested it on every round of every kind, once a second.
    """

    manifest_raw: bytes | None
    index_count: int
    index_top: int
    engaged_count: int


class StatePublisher(Protocol):
    """What the writer and the re-hydrator need from the store. Deliberately small."""

    def stored_feed_seq(self, kind: StateKind) -> int | None:
        """The position the store already holds, or None when it holds nothing verifiable."""
        ...

    def stored_head(self, kind: StateKind) -> StoredHead:
        """The published head, in O(1). The re-hydrator's per-round read."""
        ...

    def stored_index(self, kind: StateKind) -> Mapping[str, int]:
        """key -> feed_seq for every index entry. O(records): the DEEP verification path only."""
        ...

    def publish_record(
        self,
        record: SignedRecord,
        manifest: Manifest,
        *,
        engaged: bool | None = None,
    ) -> bool:
        """One record, its index entry, the manifest and a nudge. False = the store is ahead."""
        ...

    def publish_batch(
        self,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        *,
        engaged: Mapping[str, bool] | None = None,
        chunk: int = 500,
    ) -> bool:
        """Many records in bounded chunks, manifest last, ONE nudge."""
        ...

    def publish_kind(
        self,
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
    ) -> bool:
        """Replace a kind wholesale. RE-HYDRATOR ONLY: this is the O(records) path."""
        ...

    def read_stamp(self) -> bytes | None:
        """The raw freshness stamp, or None when the store holds none."""
        ...

    def put_stamp(self, stamp: Stamp) -> bool:
        """Write the stamp unless the store holds one verified at or after this round.

        False means another re-hydrator verified later and its stamp stands -- the expected,
        healthy outcome about half the time when two are running (GW05b, R2-04).
        """
        ...


class MemoryStore:
    """In-memory twin: a publisher for the writer AND a StateStore for the reader.

    Being both is the point. It lets the control plane and the gateway be tested against each
    other end to end — write, publish, poll, apply — with no Postgres and no Valkey, so the
    propagation contract is exercised by the real writer and the real FeedReader rather than by
    two sets of assumptions. It is a test and local-harness twin, not a product store.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._manifests: dict[StateKind, bytes] = {}
        self._records: dict[StateKind, dict[str, bytes]] = {}
        self._index: dict[StateKind, dict[str, int]] = {}
        self._engaged: dict[StateKind, set[str]] = {}
        self._feed_seq: dict[StateKind, int] = {}
        self._stamp: bytes | None = None
        self._stamped: Stamp | None = None
        self.nudges: list[tuple[StateKind, int]] = []
        self.whole_kind_publishes: int = 0
        self.record_writes: int = 0
        self.head_reads: int = 0
        self.index_scans: int = 0
        self.stamp_writes: int = 0
        self.stamp_races_lost: int = 0

    # --- publisher side ------------------------------------------------------------------------

    def stored_feed_seq(self, kind: StateKind) -> int | None:
        with self._lock:
            return self._feed_seq.get(kind)

    def stored_head(self, kind: StateKind) -> StoredHead:
        with self._lock:
            self.head_reads += 1
            index = self._index.get(kind, {})
            return StoredHead(
                manifest_raw=self._manifests.get(kind),
                index_count=len(index),
                index_top=max(index.values(), default=0),
                engaged_count=len(self._engaged.get(kind, set())),
            )

    def stored_index(self, kind: StateKind) -> Mapping[str, int]:
        with self._lock:
            self.index_scans += 1
            return dict(self._index.get(kind, {}))

    def publish_record(
        self,
        record: SignedRecord,
        manifest: Manifest,
        *,
        engaged: bool | None = None,
    ) -> bool:
        with self._lock:
            if self._is_ahead(record.kind, manifest):
                return False
            self._write_locked(record, engaged)
            self._commit_locked(record.kind, manifest)
        return True

    def publish_batch(
        self,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        *,
        engaged: Mapping[str, bool] | None = None,
        chunk: int = 500,
    ) -> bool:
        if not records:
            return True
        kind = records[0].kind
        with self._lock:
            if self._is_ahead(kind, manifest):
                return False
            for start in range(0, len(records), max(chunk, 1)):
                for record in records[start:start + max(chunk, 1)]:
                    flag = None if engaged is None else engaged.get(record.key)
                    self._write_locked(record, flag)
            # Manifest last: a reader either skips or sees a complete generation.
            self._commit_locked(kind, manifest)
        return True

    def publish_kind(
        self,
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
    ) -> bool:
        with self._lock:
            self.whole_kind_publishes += 1
            self._records[kind] = {}
            self._index[kind] = {}
            self._engaged[kind] = set(engaged)
            for record in records:
                self._write_locked(record, None)
            self._commit_locked(kind, manifest)
        return True

    def read_stamp(self) -> bytes | None:
        with self._lock:
            return self._stamp

    def put_stamp(self, stamp: Stamp) -> bool:
        """Never replaces a stamp verified at or after this one. The lock is the WATCH.

        Compares against the decoded stamp this twin last wrote rather than re-decoding the
        bytes, because the twin holds no signing secret and an UNVERIFIED timestamp must never
        decide anything: a forged far-future stamp would otherwise block every real one.
        `poison_stamp` is how a test puts unverifiable bytes in the store.
        """
        with self._lock:
            held = self._stamped
            if held is not None and held.verified_at >= stamp.verified_at:
                self.stamp_races_lost += 1
                return False
            self._stamp = encode_stamp(stamp)
            self._stamped = stamp
            self.stamp_writes += 1
        return True

    def _is_ahead(self, kind: StateKind, manifest: Manifest) -> bool:
        held = self._feed_seq.get(kind)
        return held is not None and held > manifest.feed_seq

    def _write_locked(self, record: SignedRecord, engaged: bool | None) -> None:
        self._records.setdefault(record.kind, {})[record.key] = encode_record(record)
        self._index.setdefault(record.kind, {})[record.key] = record.feed_seq
        self.record_writes += 1
        if engaged is None:
            return
        target = self._engaged.setdefault(record.kind, set())
        if engaged:
            target.add(record.key)
        else:
            target.discard(record.key)

    def _commit_locked(self, kind: StateKind, manifest: Manifest) -> None:
        self._manifests[kind] = encode_manifest(manifest)
        self._feed_seq[kind] = manifest.feed_seq
        self.nudges.append((kind, manifest.feed_seq))

    # --- reader side (gateway_v2.runtime.state_feed.StateStore) --------------------------------

    async def head(self, kind: StateKind) -> Head:
        with self._lock:
            index = self._index.get(kind, {})
            return Head(
                manifest_raw=self._manifests.get(kind),
                index_count=len(index),
                index_top=max(index.values(), default=0),
            )

    async def index_range(
        self,
        kind: StateKind,
        *,
        after: int,
        upto: int,
        limit: int,
    ) -> IndexPage:
        with self._lock:
            index = self._index.get(kind, {})
            selected = sorted(
                ((key, score) for key, score in index.items() if after < score <= upto),
                key=lambda item: item[1],
            )
            return IndexPage(
                entries=tuple(selected[:limit]),
                total_at_or_below=sum(1 for score in index.values() if score <= upto),
            )

    async def records(self, kind: StateKind, keys: Sequence[str]) -> tuple[bytes | None, ...]:
        with self._lock:
            held = self._records.get(kind, {})
            return tuple(held.get(key) for key in keys)

    async def engaged(self, kind: StateKind) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._engaged.get(kind, set())))

    # --- fault injection for tests -------------------------------------------------------------

    def forget_manifest(self, kind: StateKind) -> None:
        with self._lock:
            self._manifests.pop(kind, None)
            self._feed_seq.pop(kind, None)

    def flush(self) -> None:
        """Everything the store held is gone. Postgres is untouched."""
        with self._lock:
            self._manifests.clear()
            self._records.clear()
            self._index.clear()
            self._engaged.clear()
            self._feed_seq.clear()
            self._stamp = None
            self._stamped = None

    def poison_stamp(self, raw: bytes) -> None:
        """Put bytes in the stamp slot that no secret will verify, as a flush or a forge would."""
        with self._lock:
            self._stamp = raw
            self._stamped = None

    def reset_counters(self) -> None:
        self.nudges = []
        self.whole_kind_publishes = 0
        self.record_writes = 0
        self.head_reads = 0
        self.index_scans = 0
        self.stamp_writes = 0
        self.stamp_races_lost = 0


class BrokenPublisher:
    """A publisher whose every publish fails. Proves a write stays durable and is reported."""

    def __init__(self) -> None:
        self.attempts = 0

    def stored_feed_seq(self, kind: StateKind) -> int | None:
        del kind
        return None

    def stored_head(self, kind: StateKind) -> StoredHead:
        del kind
        raise ConnectionError("injected store failure")

    def stored_index(self, kind: StateKind) -> Mapping[str, int]:
        del kind
        raise ConnectionError("injected store failure")

    def publish_record(
        self,
        record: SignedRecord,
        manifest: Manifest,
        *,
        engaged: bool | None = None,
    ) -> bool:
        del record, manifest, engaged
        self.attempts += 1
        raise ConnectionError("injected store failure")

    def publish_batch(
        self,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        *,
        engaged: Mapping[str, bool] | None = None,
        chunk: int = 500,
    ) -> bool:
        del records, manifest, engaged, chunk
        self.attempts += 1
        raise ConnectionError("injected store failure")

    def publish_kind(
        self,
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
    ) -> bool:
        del kind, records, manifest, engaged
        self.attempts += 1
        raise ConnectionError("injected store failure")

    def read_stamp(self) -> bytes | None:
        raise ConnectionError("injected store failure")

    def put_stamp(self, stamp: Stamp) -> bool:
        del stamp
        self.attempts += 1
        raise ConnectionError("injected store failure")
