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

Record isolation (`poll(isolate=True)`)
---------------------------------------
The checks above are of two different kinds, and only one of them is about the whole round:

* the manifest's signature and anti-regress floors, the index's completeness count, its order and
  its bounds prove the STORE is a complete, current copy. Any of them failing is a store-level
  fault and the round is refused, isolated or not: nothing read in it can be trusted.
* a record's envelope, signature, key and score pairing prove ONE RECORD. With `isolate=False`
  (the default, and the kill switch's and budget's contract) the first bad record still refuses
  the round. With `isolate=True` it becomes a `RecordFault` instead -- its key, its index score
  and the exception TYPE, never the bytes -- and every other record in the round is still
  verified and returned. The applier of an isolating kind owns failing that one subject closed;
  the round, the cursor and every other tenant move on.

Refusing the round for one record was the fleet-wide failure: every gateway reads the same record,
every one refused the kind every cycle, and once the kind aged past the freshness bound every
tenant on every gateway was answered 503. Completeness is not weakened by isolating: the faulted
entry is still one of the entries the signed count proves, it is just not applied.

One record mismatch is NOT a fault even when isolating: a genuinely signed record whose position
is ABOVE both its index entry and the attested manifest is a newer publish that landed between
this round's index read and its record read. It is skipped like any record above the attested
generation, and the next round reads it under its new score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from gateway_v2.domain.state import (
    START,
    Cursor,
    Manifest,
    SignedRecord,
    StateKind,
    StoreDataUnavailable,
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
class RecordFault:
    """One index entry whose record could not be verified. Carries nothing the record said.

    `key` is the INDEX's key (the record itself was not trusted, so its own claim is not used),
    `feed_seq` the index score, and `error` the exception type name -- never its text, which for a
    decoder can quote the stored bytes.
    """

    key: str
    feed_seq: int
    error: str


@dataclass(frozen=True, slots=True)
class FeedRound:
    """What one round found. `records` is empty on the steady-state short circuit."""

    kind: StateKind
    manifest: Manifest
    records: tuple[SignedRecord, ...]
    cursor: Cursor
    truncated: bool
    """The delta budget was reached. Run again immediately; do not wait for the next period."""

    faults: tuple[RecordFault, ...] = ()
    """Entries whose record failed verification. Only an isolating poll produces any."""

    in_flight: int = 0
    """Entries skipped because a newer publish landed mid-round (`_in_flight`). Counted, so a
    writer stuck between its record write and its manifest write is visible from the gateway."""

    @property
    def changed(self) -> bool:
        return bool(self.records) or bool(self.faults)


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

    async def stamp(self) -> bytes | None:
        """The raw freshness stamp, or None when the store holds none.

        ONE key for the whole namespace, not one per kind, so a cycle costs one `GET` however
        many kinds a worker tracks. Verification is `StampView`'s, not the store's.
        """
        ...


class FeedReader:
    """Turns a cursor into the records that changed. Holds no state: the caller owns the cursor."""

    def __init__(self, store: StateStore, secret: bytes) -> None:
        self._store = store
        self._secret = secret

    async def read_stamp(self) -> bytes | None:
        """Pass the raw freshness stamp through. Verifying it belongs to whoever owns the floor.

        A reader cannot verify its own floor: `poll` is handed a cursor and trusts it. So the
        stamp is carried past this class untouched, to `StampView`, which is the thing that
        decides what a floor may become.
        """
        return await self._store.stamp()

    async def poll(
        self,
        kind: StateKind,
        cursor: Cursor,
        *,
        limit: int,
        floor: Cursor = START,
        isolate: bool = False,
    ) -> FeedRound:
        """One round. Raises StoreDataUnavailable; never returns a partially trusted view.

        `isolate` turns a defect in ONE record into a `RecordFault` on the round instead of a
        refused round (module docstring). Every store-level proof still refuses the round.

        `cursor` is where this reader IS; `floor` is the oldest generation it may accept. They
        are separate because GW05b's stamp raises the second without the first: a process that
        has applied nothing may still know -- from a signed stamp -- that the store should be at
        position 10,000, and it must refuse anything older while still READING those 10,000
        records. Collapsing the two would make a fresh process short-circuit on its first round
        and hold no state at all, which for plans means every tenant resolves as "unknown
        tenant" (403, complete onboarding) instead of being served -- the exact answer C36
        forbids.
        """
        if limit <= 0:
            raise ValueError("delta limit must be positive")
        manifest = await self._read_head(kind, cursor, floor)
        if manifest.feed_seq == cursor.feed_seq:
            return FeedRound(kind, manifest, (), cursor, truncated=False)
        page = await self._read_page(kind, manifest, cursor, limit)
        records, faults, in_flight = await self._read_records(
            kind, manifest, page, isolate=isolate,
        )
        # The last INDEX entry, not the last verified record: with isolation the last entry may
        # be a fault or a skipped in-flight publish, and the round has still accounted for it.
        last = page.entries[-1][1]
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
            faults=faults,
            in_flight=in_flight,
        )

    async def bootstrap_engaged(
        self,
        kind: StateKind,
        cursor: Cursor,
        floor: Cursor = START,
    ) -> tuple[Manifest, tuple[SignedRecord, ...]]:
        """Cold start a kind from its published ENGAGED set. O(engaged), not O(records).

        A cold reader cannot use the delta path: its cursor is at zero, so the delta is the whole
        kind. For the kill switch that is O(tenants) at startup — finding F5, and gate G-04's
        "0 x 503 on new gateways". The engaged set is the subset that actually changes a decision,
        so a worker reads that instead and jumps its cursor straight to the attested position.

        Completeness is the caller's to check: the manifest's signed `on_count` against what the
        caller holds after adopting these records. Returning the records rather than a count keeps
        the fail-closed decision with the component that serves requests.
        """
        manifest = await self._read_head(kind, cursor, floor)
        scopes = await self._store.engaged(kind)
        if not scopes:
            return manifest, ()
        raws = await self._store.records(kind, scopes)
        if len(raws) != len(scopes):
            raise StoreDataUnavailable(
                f"{kind.value} store returned {len(raws)} values for {len(scopes)} engaged scopes",
            )
        out: list[SignedRecord] = []
        for scope, raw in zip(scopes, raws, strict=True):
            record = decode_record(self._secret, kind, raw)
            if record.key != scope:
                raise StoreDataUnavailable(
                    f"{kind.value} engaged scope {scope!r} resolved to {record.key!r}",
                )
            if record.feed_seq > manifest.feed_seq:
                continue  # above the attested generation: a publish in flight
            out.append(record)
        return manifest, tuple(out)

    async def head(self, kind: StateKind, cursor: Cursor, floor: Cursor = START) -> Manifest:
        """The verified head alone: one round trip, no index page and no records."""
        return await self._read_head(kind, cursor, floor)

    async def _read_head(
        self,
        kind: StateKind,
        cursor: Cursor,
        floor: Cursor = START,
    ) -> Manifest:
        head = await self._store.head(kind)
        manifest = decode_manifest(
            self._secret,
            kind,
            head.manifest_raw,
            # The higher of what this process applied and what a stamp verified. Both bounds
            # matter: a partial restore can leave an older POSITION under an equal version.
            not_before=max(cursor.version, floor.version),
            feed_floor=max(cursor.feed_seq, floor.feed_seq),
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
        *,
        isolate: bool = False,
    ) -> tuple[tuple[SignedRecord, ...], tuple[RecordFault, ...], int]:
        keys = tuple(key for key, _ in page.entries)
        raws = await self._store.records(kind, keys)
        if len(raws) != len(page.entries):
            raise StoreDataUnavailable(
                f"{kind.value} store returned {len(raws)} values for {len(page.entries)} keys",
            )
        out: list[SignedRecord] = []
        faults: list[RecordFault] = []
        in_flight = 0
        for (key, score), raw in zip(page.entries, raws, strict=True):
            if not isolate:
                out.append(self._verified(kind, manifest, key, score, raw))
                continue
            try:
                record = decode_record(self._secret, kind, raw)
                if _in_flight(record, key, score, manifest):
                    in_flight += 1
                    continue  # a newer publish landed mid-round: next round reads it
                out.append(self._paired(kind, manifest, key, score, record))
            except Exception as exc:  # noqa: BLE001 - one record never refuses the round here
                faults.append(RecordFault(key=key, feed_seq=score, error=type(exc).__name__))
        return tuple(out), tuple(faults), in_flight

    def _verified(
        self,
        kind: StateKind,
        manifest: Manifest,
        key: str,
        score: int,
        raw: bytes | None,
    ) -> SignedRecord:
        return self._paired(kind, manifest, key, score, decode_record(self._secret, kind, raw))

    @staticmethod
    def _paired(
        kind: StateKind,
        manifest: Manifest,
        key: str,
        score: int,
        record: SignedRecord,
    ) -> SignedRecord:
        """The record IS the entry that selected it, and no newer than what the manifest attests."""
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
        return record


def _in_flight(record: SignedRecord, key: str, score: int, manifest: Manifest) -> bool:
    """A verified record for this key, published AFTER this round read the manifest.

    Its position is above the index entry that selected it and above the attested generation, so
    it is a newer generation of the same key, not a regress: the writer replaced the stored value
    between the index read and the record read. Skipping it is what the reader already does for
    any record above the attested position. An OLDER record under a newer score is the regress
    `record_matches_index` exists for, and stays a fault.
    """
    return record.key == key and record.feed_seq > score and record.feed_seq > manifest.feed_seq
