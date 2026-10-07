"""GW05c phase 2 — the change feed.

The load-bearing test here is `test_steady_state_at_ten_thousand_tenants_reads_no_records`: at
10,000 registered records with nothing changed, a round must read ZERO records and issue a
CONSTANT number of store commands. That is R2-02's acceptance criterion expressed as a unit test,
and it is why the store is reached through a Protocol — a real client or fakeredis would hide the
command count.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence

import pytest

from gateway_v2.domain.state import (
    ZERO,
    SignedRecord,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.runtime.state_feed import (
    START,
    Cursor,
    FeedReader,
    Head,
    IndexPage,
)
from gateway_v2.runtime.state_sig import encode_manifest, encode_record, make_manifest, make_record
from gateway_v2.runtime.store_keys import HASH_KINDS, StoreKeys

SECRET = b"gw05c-feed-test-secret"


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class FakeStore:
    """In-memory store that records every command, so cost can be asserted, not assumed."""

    def __init__(self) -> None:
        self._manifests: dict[StateKind, bytes] = {}
        self._index: dict[StateKind, dict[str, int]] = {}
        self._records: dict[StateKind, dict[str, bytes]] = {}
        self._engaged: dict[StateKind, set[str]] = {}
        self.commands: list[str] = []
        self.trips: int = 0
        self.records_read: int = 0
        self.index_entries_scanned: int = 0

    # --- the writer's side, mirroring one per-record publish MULTI ---------------------------

    def publish(self, record: SignedRecord, *, count: int, on_count: int = 0) -> None:
        kind = record.kind
        self._index.setdefault(kind, {})[record.key] = record.feed_seq
        self._records.setdefault(kind, {})[record.key] = encode_record(record)
        manifest = make_manifest(
            SECRET, kind, record.version, record.feed_seq, count, on_count,
        )
        self._manifests[kind] = encode_manifest(manifest)

    def publish_index_only(self, record: SignedRecord) -> None:
        """A bulk publish in flight: index entries land before the manifest is rewritten."""
        self._index.setdefault(record.kind, {})[record.key] = record.feed_seq
        self._records.setdefault(record.kind, {})[record.key] = encode_record(record)

    def drop_index_entry(self, kind: StateKind, key: str) -> None:
        self._index[kind].pop(key, None)

    def drop_record(self, kind: StateKind, key: str) -> None:
        self._records[kind].pop(key, None)

    def set_index_score(self, kind: StateKind, key: str, score: int) -> None:
        self._index[kind][key] = score

    def forget_manifest(self, kind: StateKind) -> None:
        self._manifests.pop(kind, None)

    def engage(self, kind: StateKind, scope: str) -> None:
        self._engaged.setdefault(kind, set()).add(scope)

    def reset_counters(self) -> None:
        self.commands = []
        self.trips = 0
        self.records_read = 0
        self.index_entries_scanned = 0

    # --- the StateStore Protocol ---------------------------------------------------------------

    async def head(self, kind: StateKind) -> Head:
        self.commands.extend(("get", "zcard", "zrevrange"))
        self.trips += 1
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
        self.commands.extend(("zcount", "zrangebyscore"))
        self.trips += 1
        index = self._index.get(kind, {})
        selected = sorted(
            ((key, score) for key, score in index.items() if after < score <= upto),
            key=lambda item: item[1],
        )
        self.index_entries_scanned += len(selected[:limit])
        return IndexPage(
            entries=tuple(selected[:limit]),
            total_at_or_below=sum(1 for score in index.values() if score <= upto),
        )

    async def records(self, kind: StateKind, keys: Sequence[str]) -> tuple[bytes | None, ...]:
        self.commands.append("mget")
        self.trips += 1
        self.records_read += len(keys)
        held = self._records.get(kind, {})
        return tuple(held.get(key) for key in keys)

    async def engaged(self, kind: StateKind) -> tuple[str, ...]:
        self.commands.append("smembers")
        self.trips += 1
        return tuple(sorted(self._engaged.get(kind, set())))


def _record(
    key: str,
    *,
    kind: StateKind = StateKind.PLAN,
    epoch: int = 1,
    seq: int,
    feed_seq: int,
    deleted: bool = False,
    op: StateOp = StateOp.PUT,
) -> SignedRecord:
    return make_record(
        SECRET,
        kind,
        key,
        {"org_id": key},
        Version(epoch, seq),
        feed_seq,
        deleted=deleted,
        op=op,
    )


def _seed(store: FakeStore, tenants: int, *, kind: StateKind = StateKind.PLAN) -> Cursor:
    """Publish `tenants` records and return a cursor that has applied all of them."""
    manifest_version = ZERO
    for position in range(1, tenants + 1):
        record = _record(f"org-{position}", kind=kind, seq=position, feed_seq=position)
        store.publish(record, count=position)
        manifest_version = record.version
    return Cursor(version=manifest_version, feed_seq=tenants)


# --- the cost shape: this is the card ---------------------------------------------------------


def test_steady_state_at_ten_thousand_tenants_reads_no_records() -> None:
    """R2-02's acceptance criterion as a unit test. Must stay flat with tenant count."""
    store = FakeStore()
    cursor = _seed(store, 10_000)
    reader = FeedReader(store, SECRET)
    store.reset_counters()

    round_ = _run(reader.poll(StateKind.PLAN, cursor, limit=256))

    assert round_.changed is False
    assert round_.records == ()
    assert round_.cursor == cursor
    assert store.records_read == 0, "a steady-state round must not read a single record"
    assert store.index_entries_scanned == 0, "nor scan a single index entry"
    assert store.trips == 1, "one round trip"
    assert store.commands == ["get", "zcard", "zrevrange"], "a constant command set"


def test_steady_state_cost_is_identical_at_one_thousand_and_twenty_five_thousand() -> None:
    """'Flat with tenant count, within +/-20% of the 1k baseline' — here, exactly equal."""
    costs: dict[int, tuple[int, tuple[str, ...]]] = {}
    for tenants in (1_000, 10_000, 25_000):
        store = FakeStore()
        cursor = _seed(store, tenants)
        reader = FeedReader(store, SECRET)
        store.reset_counters()
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))
        costs[tenants] = (store.records_read, tuple(store.commands))

    assert costs[1_000] == costs[10_000] == costs[25_000] == (0, ("get", "zcard", "zrevrange"))


def test_one_change_among_ten_thousand_reads_one_record() -> None:
    store = FakeStore()
    cursor = _seed(store, 10_000)
    reader = FeedReader(store, SECRET)
    changed = _record("org-7", seq=10_001, feed_seq=10_001)
    store.publish(changed, count=10_000)
    store.reset_counters()

    round_ = _run(reader.poll(StateKind.PLAN, cursor, limit=256))

    assert [record.key for record in round_.records] == ["org-7"]
    assert store.records_read == 1, "O(changes), not O(tenants)"
    assert store.index_entries_scanned == 1
    assert store.trips == 3
    assert round_.cursor == Cursor(version=Version(1, 10_001), feed_seq=10_001)
    assert round_.truncated is False


def test_a_new_tenant_is_one_change_not_a_reload() -> None:
    store = FakeStore()
    cursor = _seed(store, 500)
    reader = FeedReader(store, SECRET)
    store.publish(_record("org-501", seq=501, feed_seq=501), count=501)
    store.reset_counters()

    round_ = _run(reader.poll(StateKind.PLAN, cursor, limit=256))

    assert [record.key for record in round_.records] == ["org-501"]
    assert store.records_read == 1


# --- the cursor model -------------------------------------------------------------------------


def test_a_rollback_across_an_epoch_bump_is_selected() -> None:
    """seq goes backwards on a rollback; the cursor must still pick the record up."""
    store = FakeStore()
    cursor = _seed(store, 3)
    reader = FeedReader(store, SECRET)
    rollback = _record("org-2", epoch=2, seq=0, feed_seq=4, op=StateOp.ROLLBACK)
    store.publish(rollback, count=3)

    round_ = _run(reader.poll(StateKind.PLAN, cursor, limit=256))

    assert [record.key for record in round_.records] == ["org-2"]
    assert round_.records[0].version == Version(2, 0)
    assert round_.cursor.feed_seq == 4


def test_the_budget_truncates_and_asks_to_run_again() -> None:
    store = FakeStore()
    cursor = _seed(store, 10)
    reader = FeedReader(store, SECRET)
    for position in range(11, 31):
        store.publish(_record(f"org-{position}", seq=position, feed_seq=position), count=30)
    store.reset_counters()

    first = _run(reader.poll(StateKind.PLAN, cursor, limit=5))

    assert len(first.records) == 5
    assert first.truncated is True
    assert first.cursor.feed_seq == 15
    assert first.cursor.version == cursor.version, "a partial round does not raise the floor"

    second = _run(reader.poll(StateKind.PLAN, first.cursor, limit=5))
    assert [record.key for record in second.records] == [f"org-{n}" for n in range(16, 21)]
    assert second.truncated is True


def test_repeated_rounds_converge_and_then_cost_nothing() -> None:
    store = FakeStore()
    cursor = _seed(store, 4)
    reader = FeedReader(store, SECRET)
    for position in range(5, 25):
        store.publish(_record(f"org-{position}", seq=position, feed_seq=position), count=24)

    rounds = 0
    while True:
        rounds += 1
        result = _run(reader.poll(StateKind.PLAN, cursor, limit=8))
        cursor = result.cursor
        if not result.truncated:
            break
    assert rounds == 3
    assert cursor.feed_seq == 24

    store.reset_counters()
    _run(reader.poll(StateKind.PLAN, cursor, limit=8))
    assert store.records_read == 0


def test_an_empty_kind_short_circuits() -> None:
    store = FakeStore()
    reader = FeedReader(store, SECRET)
    store._manifests[StateKind.KS] = encode_manifest(
        make_manifest(SECRET, StateKind.KS, ZERO, 0, 0),
    )

    round_ = _run(reader.poll(StateKind.KS, START, limit=256))

    assert round_.records == ()
    assert store.records_read == 0


# --- integrity --------------------------------------------------------------------------------


def test_a_bulk_publish_in_flight_does_not_fail_closed() -> None:
    """Index entries land before the manifest. The reader reads only the attested generation."""
    store = FakeStore()
    cursor = _seed(store, 5)
    reader = FeedReader(store, SECRET)
    store.publish(_record("org-6", seq=6, feed_seq=6), count=6)
    for position in (7, 8, 9):
        store.publish_index_only(_record(f"org-{position}", seq=position, feed_seq=position))

    round_ = _run(reader.poll(StateKind.PLAN, cursor, limit=256))

    assert [record.key for record in round_.records] == ["org-6"]
    assert round_.cursor.feed_seq == 6, "nothing above the attested position is applied"


def test_an_index_behind_the_manifest_is_unavailable() -> None:
    """The manifest attests writes the index cannot name: those records would be missed."""
    store = FakeStore()
    cursor = _seed(store, 5)
    reader = FeedReader(store, SECRET)
    store.publish(_record("org-6", seq=6, feed_seq=6), count=6)
    store.drop_index_entry(StateKind.PLAN, "org-6")

    with pytest.raises(StoreDataUnavailable, match="is behind the attested"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_a_partial_store_is_unavailable() -> None:
    store = FakeStore()
    cursor = _seed(store, 5)
    reader = FeedReader(store, SECRET)
    store.publish(_record("org-6", seq=6, feed_seq=6), count=6)
    store.drop_index_entry(StateKind.PLAN, "org-3")

    with pytest.raises(StoreDataUnavailable, match="of 6 records"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_a_missing_entry_masked_by_an_extra_is_caught_on_a_delta_round() -> None:
    """ZCARD alone would be fooled; counting at or below the attested position is not."""
    store = FakeStore()
    cursor = _seed(store, 5)
    reader = FeedReader(store, SECRET)
    store.publish(_record("org-6", seq=6, feed_seq=6), count=6)
    store.drop_index_entry(StateKind.PLAN, "org-3")
    store.publish_index_only(_record("org-99", seq=99, feed_seq=99))

    with pytest.raises(StoreDataUnavailable, match="records at or below"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_a_flushed_store_is_unavailable_not_empty() -> None:
    store = FakeStore()
    cursor = _seed(store, 5)
    reader = FeedReader(store, SECRET)
    store.forget_manifest(StateKind.PLAN)

    with pytest.raises(StoreDataUnavailable, match="manifest missing"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_a_version_regressed_store_is_unavailable() -> None:
    """A lagging replica: the manifest is older than this reader already applied."""
    store = FakeStore()
    cursor = _seed(store, 9)
    reader = FeedReader(store, SECRET)
    store._manifests[StateKind.PLAN] = encode_manifest(
        make_manifest(SECRET, StateKind.PLAN, Version(1, 4), 4, 9),
    )

    with pytest.raises(StoreDataUnavailable, match="older than 1.9 already applied"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_a_cursor_regressed_store_is_unavailable() -> None:
    """The version is intact but the published position went backwards.

    This is the check the whole-set digest used to provide implicitly: without it the delta
    would come back empty and the reader would serve stale state believing it was current.
    """
    store = FakeStore()
    cursor = _seed(store, 9)
    reader = FeedReader(store, SECRET)
    store._manifests[StateKind.PLAN] = encode_manifest(
        make_manifest(SECRET, StateKind.PLAN, cursor.version, 8, 9),
    )

    with pytest.raises(StoreDataUnavailable, match="below the applied cursor"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_an_older_record_under_a_newer_score_is_unavailable() -> None:
    """Every individual check passes: the old record really is signed. The pair must agree."""
    store = FakeStore()
    cursor = _seed(store, 5)
    reader = FeedReader(store, SECRET)
    store.publish(_record("org-6", seq=6, feed_seq=6), count=6)
    store.set_index_score(StateKind.PLAN, "org-3", 6)
    store.set_index_score(StateKind.PLAN, "org-6", 3)

    with pytest.raises(StoreDataUnavailable, match="index says"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_a_record_missing_while_indexed_is_unavailable() -> None:
    store = FakeStore()
    cursor = _seed(store, 5)
    reader = FeedReader(store, SECRET)
    store.publish(_record("org-6", seq=6, feed_seq=6), count=6)
    store.drop_record(StateKind.PLAN, "org-6")

    with pytest.raises(StoreDataUnavailable, match="record missing"):
        _run(reader.poll(StateKind.PLAN, cursor, limit=256))


def test_a_manifest_claiming_no_records_at_a_position_is_unavailable() -> None:
    """C36 keeps an explicit OFF record, so count never falls back to zero."""
    store = FakeStore()
    reader = FeedReader(store, SECRET)
    store._manifests[StateKind.PLAN] = encode_manifest(
        make_manifest(SECRET, StateKind.PLAN, Version(1, 3), 3, 0),
    )
    store.publish_index_only(_record("org-1", seq=3, feed_seq=3))

    with pytest.raises(StoreDataUnavailable, match="claims no records"):
        _run(reader.poll(StateKind.PLAN, START, limit=256))


def test_a_cross_kind_record_is_unavailable() -> None:
    store = FakeStore()
    reader = FeedReader(store, SECRET)
    foreign = _record("hash-1", kind=StateKind.KEY, seq=1, feed_seq=1)
    store._index[StateKind.PLAN] = {"hash-1": 1}
    store._records[StateKind.PLAN] = {"hash-1": encode_record(foreign)}
    store._manifests[StateKind.PLAN] = encode_manifest(
        make_manifest(SECRET, StateKind.PLAN, Version(1, 1), 1, 1),
    )

    with pytest.raises(StoreDataUnavailable, match="carries another kind"):
        _run(reader.poll(StateKind.PLAN, START, limit=256))


def test_a_record_newer_than_its_manifest_is_unavailable() -> None:
    store = FakeStore()
    reader = FeedReader(store, SECRET)
    ahead = _record("org-1", seq=9, feed_seq=1)
    store._index[StateKind.PLAN] = {"org-1": 1}
    store._records[StateKind.PLAN] = {"org-1": encode_record(ahead)}
    store._manifests[StateKind.PLAN] = encode_manifest(
        make_manifest(SECRET, StateKind.PLAN, Version(1, 2), 1, 1),
    )

    with pytest.raises(StoreDataUnavailable, match="newer than its manifest"):
        _run(reader.poll(StateKind.PLAN, START, limit=256))


def test_a_zero_limit_is_rejected() -> None:
    store = FakeStore()
    reader = FeedReader(store, SECRET)

    with pytest.raises(ValueError, match="must be positive"):
        _run(reader.poll(StateKind.PLAN, START, limit=0))


def test_a_cold_reader_applies_every_record_once() -> None:
    """A restarted worker with no stamp: correct, and still bounded by the budget."""
    store = FakeStore()
    _seed(store, 12)
    reader = FeedReader(store, SECRET)
    cursor = START
    seen: list[str] = []
    while True:
        result = _run(reader.poll(StateKind.PLAN, cursor, limit=5))
        seen.extend(record.key for record in result.records)
        cursor = result.cursor
        if not result.truncated:
            break

    assert seen == [f"org-{n}" for n in range(1, 13)]
    assert len(seen) == len(set(seen)), "no record is applied twice"


# --- key layout -------------------------------------------------------------------------------


def test_store_keys_are_namespaced_and_single_slot() -> None:
    keys = StoreKeys()

    assert keys.manifest(StateKind.PLAN) == "{rv2}:meta:plan"
    assert keys.index(StateKind.KS) == "{rv2}:idx:ks"
    assert keys.engaged(StateKind.KS) == "{rv2}:on:ks"
    assert keys.record_hash(StateKind.KEY) == "{rv2}:key"
    assert keys.record_key(StateKind.PLAN, "org-a") == "{rv2}:plan:org-a"
    assert keys.updates == "{rv2}:updates"
    assert keys.stamp == "{rv2}:stamp"
    for name in (
        keys.manifest(StateKind.PLAN),
        keys.index(StateKind.PLAN),
        keys.engaged(StateKind.KS),
        keys.record_key(StateKind.PLAN, "org-a"),
        keys.updates,
        keys.stamp,
    ):
        assert name.startswith("{rv2}"), "every key must share one hash slot"


def test_store_keys_honour_a_namespace() -> None:
    keys = StoreKeys(namespace="{t7}")

    assert keys.manifest(StateKind.KEY) == "{t7}:meta:key"
    assert keys.index(StateKind.KEY) == "{t7}:idx:key"


def test_hash_stored_and_key_stored_kinds_do_not_mix() -> None:
    keys = StoreKeys()

    assert StateKind.PLAN not in HASH_KINDS
    assert StateKind.KEY in HASH_KINDS
    with pytest.raises(ValueError, match="not hash-stored"):
        keys.record_hash(StateKind.PLAN)
    with pytest.raises(ValueError, match="are hash-stored"):
        keys.record_key(StateKind.KEY, "hash-1")
