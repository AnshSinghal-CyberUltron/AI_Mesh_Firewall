"""Read a kind's changes since a cursor. O(1) when nothing changed, O(changes) when it did.

This is the module that makes R2-02's cost shape go away. One round:

    head()                       1 round trip, 3 commands, constant cost
      manifest verified, index checked against it
      manifest.feed_seq == cursor.feed_seq  ->  DONE. Nothing is read.
    index_range()                1 round trip, 2 commands, O(changes) bytes
    records()                    1 round trip, O(changes) bytes

So the steady state a worker sits in 99.9% of the time costs three commands, whatever the tenant
count. Nothing here iterates tenants, and nothing reads a whole kind.

The store is reached through the `StateStore` Protocol, not a client library: the acceptance gate
is "a refresh round touches 0 records and a constant number of commands at 10,000 tenants", and
that can only be asserted by counting the commands a round issues.

Integrity, and why each check is here
-------------------------------------
The manifest attests one generation of the kind. The index can legitimately run AHEAD of it while
a bulk publish is in flight (the writer appends index entries and writes the manifest last), so
the reader bounds everything it does by `manifest.feed_seq` and never trusts the index's own head:

* `index_top >= manifest.feed_seq` — the index must not be BEHIND the manifest. Behind means the
  manifest attests writes the index cannot name, so the reader would silently miss them.
* `index_count >= manifest.count` — a cheap screen on every round, including the short-circuit.
  In-flight extras only ever make it larger, so it never false-positives.
* `total_at_or_below == manifest.count` — the EXACT completeness proof, run on any round that
  reads a delta. Counting only entries at or below the attested position is immune to in-flight
  extras, which a plain `ZCARD` comparison is not.
* `record.feed_seq == index score` — pairs the record with the entry that selected it, so an
  older genuinely-signed record cannot be served under a newer score.
* strictly ascending scores — deltas are applied in write order.

Residual, stated plainly: on a short-circuit round (nothing changed) completeness rests on
`index_count >= manifest.count`, so one missing entry masked by one in-flight extra would not be
caught until the next round that reads a delta. The re-hydrator's full digest comparison, which
runs off the serving loop, is the backstop for that window.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain.state import (
    ZERO,
    Manifest,
    SignedRecord,
    StateKind,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.runtime.state_sig import decode_manifest, decode_record, record_matches_index


@dataclass(frozen=True, slots=True)
class Head:
    """One kind's published head, read in a single round trip."""

    manifest_raw: bytes | None
    index_count: int
    index_top: int
    """Highest score in the index, 0 when the index is empty."""


@dataclass(frozen=True, slots=True)
class IndexPage:
    """Index entries above a cursor, bounded above by the attested position."""

    entries: tuple[tuple[str, int], ...]
    total_at_or_below: int
    """Entries whose score is at or below the attested position. The exact completeness proof."""


@dataclass(frozen=True, slots=True)
class Cursor:
    """How far a reader has applied a kind. Floors only rise; GW05b raises them from a stamp."""

    version: Version
    feed_seq: int

    def advanced(self, version: Version, feed_seq: int) -> Cursor:
        return Cursor(version=version, feed_seq=feed_seq)


START = Cursor(version=ZERO, feed_seq=0)
"""A reader with no stamp and nothing applied."""


@dataclass(frozen=True, slots=True)
class FeedRound:
    """What one round found. `records` is empty on the steady-state short circuit."""

    kind: StateKind
    manifest: Manifest
    records: tuple[SignedRecord, ...]
    cursor: Cursor
    truncated: bool
    """The delta budget was reached. Run again immediately; do not wait for the next period."""

    @property
    def changed(self) -> bool:
        return bool(self.records)


class StateStore(Protocol):
    """The store operations a feed reader needs. Each method is ONE round trip."""

    async def head(self, kind: StateKind) -> Head:
        """GET manifest + ZCARD index + top index score."""
        ...

    async def index_range(
        self,
        kind: StateKind,
        *,
        after: int,
        upto: int,
        limit: int,
    ) -> IndexPage:
        """Entries with `after` < score <= `upto`, ascending, plus the count at or below `upto`."""
        ...

    async def records(self, kind: StateKind, keys: Sequence[str]) -> tuple[bytes | None, ...]:
        """Record envelopes for `keys`, in order, None where absent."""
        ...

    async def engaged(self, kind: StateKind) -> tuple[str, ...]:
        """Members of the kind's engaged set. O(engaged), used on a cold start."""
        ...


class FeedReader:
    """Turns a cursor into the records that changed. Holds no state: the caller owns the cursor."""

    def __init__(self, store: StateStore, secret: bytes) -> None:
        self._store = store
        self._secret = secret

    async def poll(self, kind: StateKind, cursor: Cursor, *, limit: int) -> FeedRound:
        """One round. Raises StoreDataUnavailable; never returns a partially trusted view."""
        if limit <= 0:
            raise ValueError("delta limit must be positive")
        manifest = await self._read_head(kind, cursor)
        if manifest.feed_seq == cursor.feed_seq:
            return FeedRound(kind, manifest, (), cursor, truncated=False)
        page = await self._read_page(kind, manifest, cursor, limit)
        records = await self._read_records(kind, manifest, page)
        last = records[-1].feed_seq
        truncated = last < manifest.feed_seq
        if truncated and len(page.entries) < limit:
            raise StoreDataUnavailable(
                f"{kind.value} index ends at {last} below the attested {manifest.feed_seq}",
            )
        return FeedRound(
            kind=kind,
            manifest=manifest,
            records=records,
            # A partial round has not applied the attested generation, so its version floor
            # stays put. Only the cursor position moves.
            cursor=cursor.advanced(cursor.version if truncated else manifest.version, last),
            truncated=truncated,
        )

    async def _read_head(self, kind: StateKind, cursor: Cursor) -> Manifest:
        head = await self._store.head(kind)
        manifest = decode_manifest(
            self._secret,
            kind,
            head.manifest_raw,
            not_before=cursor.version,
            feed_floor=cursor.feed_seq,
        )
        if head.index_top < manifest.feed_seq:
            raise StoreDataUnavailable(
                f"{kind.value} index head {head.index_top} is behind the attested "
                f"{manifest.feed_seq} (published records were lost)",
            )
        if head.index_count < manifest.count:
            raise StoreDataUnavailable(
                f"{kind.value} index holds {head.index_count} of {manifest.count} records "
                "(partial store)",
            )
        if manifest.count == 0 and manifest.feed_seq != 0:
            raise StoreDataUnavailable(
                f"{kind.value} manifest claims no records at position {manifest.feed_seq}; "
                "an offboarded record stays as an explicit OFF record",
            )
        return manifest

    async def _read_page(
        self,
        kind: StateKind,
        manifest: Manifest,
        cursor: Cursor,
        limit: int,
    ) -> IndexPage:
        page = await self._store.index_range(
            kind, after=cursor.feed_seq, upto=manifest.feed_seq, limit=limit,
        )
        if page.total_at_or_below != manifest.count:
            raise StoreDataUnavailable(
                f"{kind.value} index holds {page.total_at_or_below} records at or below "
                f"{manifest.feed_seq}, manifest attests {manifest.count}",
            )
        if not page.entries:
            raise StoreDataUnavailable(
                f"{kind.value} manifest advanced to {manifest.feed_seq} but the index names "
                f"nothing above {cursor.feed_seq}",
            )
        if len(page.entries) > limit:
            raise StoreDataUnavailable(
                f"{kind.value} index returned {len(page.entries)} entries over the {limit} asked",
            )
        previous = cursor.feed_seq
        for key, score in page.entries:
            if score <= previous:
                raise StoreDataUnavailable(
                    f"{kind.value} index entry {key!r} at {score} is not above {previous}",
                )
            if score > manifest.feed_seq:
                raise StoreDataUnavailable(
                    f"{kind.value} index entry {key!r} at {score} is above the attested "
                    f"{manifest.feed_seq}",
                )
            previous = score
        return page

    async def _read_records(
        self,
        kind: StateKind,
        manifest: Manifest,
        page: IndexPage,
    ) -> tuple[SignedRecord, ...]:
        keys = tuple(key for key, _ in page.entries)
        raws = await self._store.records(kind, keys)
        if len(raws) != len(page.entries):
            raise StoreDataUnavailable(
                f"{kind.value} store returned {len(raws)} values for {len(page.entries)} keys",
            )
        out: list[SignedRecord] = []
        for (key, score), raw in zip(page.entries, raws, strict=True):
            record = decode_record(self._secret, kind, raw)
            if record.key != key:
                raise StoreDataUnavailable(
                    f"{kind.value} record at {key!r} identifies itself as {record.key!r}",
                )
            if not record_matches_index(record, score):
                raise StoreDataUnavailable(
                    f"{kind.value} record {key!r} is at {record.feed_seq}, index says {score}",
                )
            if record.version > manifest.version:
                raise StoreDataUnavailable(
                    f"{kind.value} record {key!r} at {record.version} is newer than its "
                    f"manifest {manifest.version}",
                )
            out.append(record)
        return tuple(out)
