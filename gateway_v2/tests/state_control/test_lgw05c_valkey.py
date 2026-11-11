"""GW05c phase 4c — the real Valkey adapters, driven against a real Redis implementation.

These are the tests that make the cost claims concrete rather than structural. The twins in
phases 1-4b prove the LOGIC; fakeredis executes the actual command set, so
`test_a_steady_round_issues_exactly_three_commands` is counting commands a real server would see.

`test_no_whole_collection_read_reaches_the_store` is the durable backstop for §2.1 rule (1): it
asserts, over a monkeypatched client that records every call, that a steady round never issues
HGETALL, HVALS, SMEMBERS, KEYS or SCAN -- the shapes that made RC2 O(records).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import fakeredis
import pytest

from gateway_v2.admit.killswitch import KillSwitchSnapshot, killswitch_adopter
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.state import START, StateKind, StoreDataUnavailable
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_task import DeltaBudget, StateSynchroniser
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import ValkeyStateStore
from state_control.db import MemoryControlDB
from state_control.rehydrate import MISSING, Rehydrator
from state_control.valkey import ValkeyPublisher
from state_control.writer import OK, OK_PUBLISH_PENDING, StateWriter
from tests.plan.test_lgw05c_delta import _draft

SECRET = b"gw05c-valkey-test-secret"
KEYS = StoreKeys(namespace="{t}")


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _plan_body(org_id: str) -> dict[str, object]:
    return encode_plan_body(PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(),)))


class Lab:
    """A writer on a sync client and a reader on an async client, over one fake server."""

    def __init__(self) -> None:
        self.server = fakeredis.FakeServer()
        self.sync = fakeredis.FakeStrictRedis(server=self.server)
        self.publisher = ValkeyPublisher(self.sync, SECRET, KEYS)
        self.db = MemoryControlDB()
        self.writer = StateWriter(self.db, self.publisher, SECRET)
        self.rehydrator = Rehydrator(
            self.db, self.publisher, self.writer, SECRET,
            stale_grace_s=0.0, kinds=(StateKind.PLAN, StateKind.KS),
        )

    def reader(self) -> ValkeyStateStore:
        return ValkeyStateStore(
            fakeredis.FakeAsyncRedis(server=self.server), KEYS,
        )

    def feed(self) -> FeedReader:
        return FeedReader(self.reader(), SECRET)

    def gateway(self, *, budget: DeltaBudget | None = None) -> StateSynchroniser:
        self.plans = PlanStore()
        self.snapshot = ReplicaSnapshot(self.plans, clock=lambda: 1.0)
        self.switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
        return StateSynchroniser(
            self.feed(),
            {StateKind.PLAN: plan_applier(self.plans, self.snapshot, clock=lambda: 1.0)},
            **({} if budget is None else {"budget": budget}),
        )


class CountingClient:
    """Wraps an async client and records every command name issued."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.commands: list[str] = []

    def pipeline(self, transaction: bool = True) -> CountingPipe:
        return CountingPipe(self._inner.pipeline(transaction=transaction), self)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        self.commands.append(name)
        return getattr(self._inner, name)


class CountingPipe:
    def __init__(self, inner: object, owner: CountingClient) -> None:
        self._inner = inner
        self._owner = owner

    async def __aenter__(self) -> CountingPipe:
        await self._inner.__aenter__()  # type: ignore[attr-defined]
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._inner.__aexit__(*exc)  # type: ignore[attr-defined]

    async def execute(self) -> object:
        return await self._inner.execute()  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        self._owner.commands.append(name)
        return getattr(self._inner, name)


# --- the command set ----------------------------------------------------------------------------


def test_a_steady_round_issues_exactly_three_commands() -> None:
    lab = Lab()
    for position in range(1, 201):
        lab.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    counting = CountingClient(fakeredis.FakeAsyncRedis(server=lab.server))
    reader = FeedReader(ValkeyStateStore(counting, KEYS), SECRET)  # type: ignore[arg-type]
    cursor = _run(reader.poll(StateKind.PLAN, START, limit=1_000)).cursor
    counting.commands = []

    round_ = _run(reader.poll(StateKind.PLAN, cursor, limit=1_000))

    assert round_.records == ()
    assert counting.commands == ["get", "zcard", "zrevrange"]


def test_no_whole_collection_read_reaches_the_steady_path() -> None:
    """§2.1 rule (1): the shapes that made RC2 O(records) must not appear on this path.

    `smembers` and `zrange` ARE used elsewhere and legitimately — the kill-switch cold start
    reads the engaged set (O(engaged)) and the re-hydrator's deep verify reads the index
    (O(records), occasional). Neither may appear on the steady plan round, which is the one
    that runs once a second on a serving loop.
    """
    forbidden = {"hgetall", "hvals", "keys", "scan", "smembers", "zrange", "sscan", "hscan"}
    lab = Lab()
    for position in range(1, 51):
        lab.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    counting = CountingClient(fakeredis.FakeAsyncRedis(server=lab.server))
    reader = FeedReader(ValkeyStateStore(counting, KEYS), SECRET)  # type: ignore[arg-type]

    cursor = _run(reader.poll(StateKind.PLAN, START, limit=1_000)).cursor
    lab.writer.plan_set("org-7", _plan_body("org-7"))
    _run(reader.poll(StateKind.PLAN, cursor, limit=1_000))

    assert not forbidden & set(counting.commands), sorted(forbidden & set(counting.commands))


def test_one_change_reads_one_record_against_a_real_client() -> None:
    lab = Lab()
    for position in range(1, 501):
        lab.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    reader = lab.feed()
    cursor = _run(reader.poll(StateKind.PLAN, START, limit=1_000)).cursor

    lab.writer.plan_set("org-250", _plan_body("org-250"))
    round_ = _run(reader.poll(StateKind.PLAN, cursor, limit=1_000))

    assert [record.key for record in round_.records] == ["org-250"]


# --- the published layout -----------------------------------------------------------------------


def test_a_write_touches_the_five_keys_and_nothing_else() -> None:
    lab = Lab()
    lab.writer.killswitch("org:acme", on=True)

    assert lab.sync.exists(KEYS.record_hash(StateKind.KS)) == 1
    assert lab.sync.zscore(KEYS.index(StateKind.KS), "org:acme") == 1.0
    assert lab.sync.smembers(KEYS.engaged(StateKind.KS)) == {b"org:acme"}
    assert lab.sync.exists(KEYS.manifest(StateKind.KS)) == 1
    assert sorted(_text(key) for key in lab.sync.keys("*")) == sorted(
        [
            KEYS.record_hash(StateKind.KS),
            KEYS.index(StateKind.KS),
            KEYS.engaged(StateKind.KS),
            KEYS.manifest(StateKind.KS),
        ],
    )


def test_plans_get_a_key_each_and_hash_kinds_share_one() -> None:
    lab = Lab()
    lab.writer.plan_set("org-a", _plan_body("org-a"))
    lab.writer.key_add("hash-a", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)
    lab.writer.key_add("hash-b", "org-a", key_id="k", rate_per_s=1.0, burst=1.0)

    assert lab.sync.exists(KEYS.record_key(StateKind.PLAN, "org-a")) == 1
    assert lab.sync.hlen(KEYS.record_hash(StateKind.KEY)) == 2


def test_every_key_shares_one_hash_slot() -> None:
    """A cluster-mode store must keep MULTI and multi-key reads in one slot."""
    lab = Lab()
    lab.writer.plan_set("org-a", _plan_body("org-a"))
    lab.writer.killswitch("global", on=False)

    for key in lab.sync.keys("*"):
        assert _text(key).startswith("{t}"), _text(key)


def test_a_disengage_removes_the_scope_from_the_engaged_set() -> None:
    lab = Lab()
    lab.writer.killswitch("org:acme", on=True)
    lab.writer.killswitch("org:acme", on=False)

    assert lab.sync.smembers(KEYS.engaged(StateKind.KS)) == set()
    assert lab.sync.hlen(KEYS.record_hash(StateKind.KS)) == 1, "an explicit OFF record remains"


def test_the_nudge_carries_the_kind_and_the_position() -> None:
    lab = Lab()
    listener = lab.sync.pubsub()
    listener.subscribe(KEYS.updates)
    listener.get_message(timeout=0.1)

    lab.writer.plan_set("org-a", _plan_body("org-a"))

    message = listener.get_message(timeout=1.0)
    assert message is not None
    assert _text(message["data"]) == "plan:1"


# --- guards and failure -------------------------------------------------------------------------


def test_a_publisher_behind_the_store_does_not_move_it_backwards() -> None:
    lab = Lab()
    for position in range(1, 4):
        lab.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    behind = StateWriter(MemoryControlDB(), lab.publisher, SECRET)

    outcome = behind.plan_set("org-late", _plan_body("org-late"))

    assert outcome.status == OK_PUBLISH_PENDING
    assert outcome.durable is True
    assert lab.publisher.stored_feed_seq(StateKind.PLAN) == 3


def test_a_forged_manifest_never_blocks_a_publish() -> None:
    lab = Lab()
    lab.sync.set(KEYS.manifest(StateKind.PLAN), b"not a manifest")

    outcome = lab.writer.plan_set("org-a", _plan_body("org-a"))

    assert outcome.status == OK


def test_a_reader_refuses_a_foreign_signer() -> None:
    lab = Lab()
    StateWriter(MemoryControlDB(), lab.publisher, b"another-secret").plan_set(
        "org-a", _plan_body("org-a"),
    )

    with pytest.raises(StoreDataUnavailable, match="signature"):
        _run(lab.feed().poll(StateKind.PLAN, START, limit=10))


def test_a_flushed_store_is_diagnosed_and_restored() -> None:
    lab = Lab()
    for position in range(1, 21):
        lab.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))
    lab.sync.flushall()

    assert lab.rehydrator.diagnose(StateKind.PLAN) == MISSING
    summary = lab.rehydrator.round_once()

    assert [event.records for event in summary.repairs if event.kind is StateKind.PLAN] == [20]
    assert lab.rehydrator.diagnose(StateKind.PLAN) is None


def test_a_healthy_diagnose_touches_no_records_against_a_real_client() -> None:
    lab = Lab()
    for position in range(1, 101):
        lab.writer.plan_set(f"org-{position}", _plan_body(f"org-{position}"))

    assert lab.rehydrator.diagnose(StateKind.PLAN) is None
    assert lab.rehydrator.verify(StateKind.PLAN) is None


def test_an_empty_secret_is_refused_by_the_publisher() -> None:
    with pytest.raises(ValueError, match="signing secret is required"):
        ValkeyPublisher(fakeredis.FakeStrictRedis(), b"", KEYS)


# --- end to end over a real client ---------------------------------------------------------------


def test_a_plan_write_reaches_a_gateway_over_valkey() -> None:
    lab = Lab()
    sync = lab.gateway()
    lab.writer.plan_set("org-a", _plan_body("org-a"))

    report = _run(sync.drain(StateKind.PLAN))

    assert report.ok, report.error
    assert isinstance(lab.plans.read("org-a"), ExecutionPlan)


def test_a_bulk_onboard_converges_over_valkey() -> None:
    lab = Lab()
    lab.writer.put_many(
        StateKind.PLAN,
        [(f"org-{n}", _plan_body(f"org-{n}")) for n in range(1, 301)],
    )
    sync = lab.gateway(budget=DeltaBudget(records=50))

    rounds = 0
    while True:
        rounds += 1
        report = _run(sync.round_once(StateKind.PLAN))
        assert report.ok, report.error
        if not report.truncated:
            break

    assert rounds == 6
    assert len(lab.plans.known()) == 300


def test_a_cold_kill_switch_start_reads_one_scope_over_valkey() -> None:
    """Gate G-04 over the real command set: SMEMBERS of the engaged set, not the kind."""
    lab = Lab()
    lab.writer.put_many(
        StateKind.KS,
        [(f"org:t{n}", {"on": False}) for n in range(1, 1_001)],
        engaged={},
    )
    lab.writer.killswitch("org:acme", on=True)
    switches = KillSwitchSnapshot(stale_ms=5_000, clock=lambda: 100.0)
    sync = StateSynchroniser(
        lab.feed(),
        {StateKind.KS: lambda round_: None},
    )

    report = _run(sync.bootstrap_engaged(StateKind.KS, killswitch_adopter(switches)))

    assert report.ok, report.error
    assert report.applied == 1, "one engaged scope read out of 1,001 records"
    assert switches.org_killed("acme") is True


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)
