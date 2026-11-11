"""GW36 S3 runtime pieces: store-fault conversion (N7), inline KEY applier (N8), KEY cold start,
per-kind gates (N9) and the plan snapshot forgetting an offboarded org (N19)."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable, StreamingMode
from gateway_v2.domain.state import START, StateKind, StoreDataUnavailable
from gateway_v2.plan.delta import PlanDeltaApplier
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_gate import KindGates
from gateway_v2.runtime.state_stamp import StampView
from gateway_v2.runtime.state_task import RoundReport, StateSynchroniser
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import ValkeyStateStore
from state_control.db import MemoryControlDB
from state_control.valkey import ValkeyPublisher
from state_control.writer import StateWriter
from tests.plan.test_lgw05c_delta import _draft

SECRET = b"gw36-s3-runtime"
KEYS = StoreKeys("{rv2}")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _Raising:
    """A client whose every call raises `exc` -- a dead socket, or a server that answers no."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Any:
        async def call(*_args: Any, **_kwargs: Any) -> Any:
            raise self._exc

        return call


# --- N7 ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc", [RedisConnectionError("reset"), TimeoutError("slow"), OSError("refused")],
)
def test_a_transport_fault_is_store_data_unavailable(exc: BaseException) -> None:
    store = ValkeyStateStore(_Raising(exc), KEYS)
    with pytest.raises(StoreDataUnavailable, match="unreachable"):
        _run(store.stamp())
    with pytest.raises(StoreDataUnavailable):
        _run(store.records(StateKind.KEY, ("k",)))


def test_a_server_error_stays_loud() -> None:
    """WRONGTYPE / a missing module is a configuration defect, not a quiet 503 (R8)."""
    store = ValkeyStateStore(_Raising(ResponseError("WRONGTYPE")), KEYS)
    with pytest.raises(ResponseError):
        _run(store.stamp())


def _published_server() -> fakeredis.FakeServer:
    server = fakeredis.FakeServer()
    writer = StateWriter(
        MemoryControlDB(), ValkeyPublisher(fakeredis.FakeRedis(server=server), SECRET, KEYS),
        SECRET,
    )
    for org in ("a", "b"):
        body = encode_plan_body(PlanDocument(org, StreamingMode.INCREMENTAL, (_draft(),)))
        writer.plan_set(org, body)
    return server


def test_one_kind_down_does_not_stop_the_others_or_the_cycle() -> None:
    server = _published_server()
    client = fakeredis.FakeAsyncRedis(server=server)
    applied: list[StateKind] = []
    sync = StateSynchroniser(
        FeedReader(ValkeyStateStore(client, KEYS), SECRET),
        {
            StateKind.PLAN: lambda round_: applied.append(round_.kind),
            StateKind.KEY: lambda round_: applied.append(round_.kind),
        },
        stamp=StampView(SECRET),
    )
    server.connected = False
    reports = _run(sync.drain_all())  # stamp read fails, every kind fails: reported, not raised
    assert [report.ok for report in reports] == [False, False]
    server.connected = True
    reports = _run(sync.drain_all())
    assert reports[0].ok and StateKind.PLAN in applied


# --- N8 ----------------------------------------------------------------------------------------


def test_the_key_applier_never_leaves_the_loop() -> None:
    offloaded: list[int] = []

    async def offload(work: Any) -> None:
        offloaded.append(1)
        work()

    class _Reader:
        async def poll(self, kind: StateKind, cursor: Any, *, limit: int, floor: Any) -> FeedRound:
            del cursor, limit, floor
            return FeedRound(kind, _manifest(), tuple(_records(40)), START, truncated=False)

    sync = StateSynchroniser(
        _Reader(),  # type: ignore[arg-type]
        {StateKind.KEY: lambda _r: None, StateKind.PLAN: lambda _r: None},
        offload=offload,
        inline_kinds=frozenset({StateKind.KEY}),
    )
    key = _run(sync.round_once(StateKind.KEY))
    plan = _run(sync.round_once(StateKind.PLAN))
    assert not key.offloaded and plan.offloaded
    assert offloaded == [1], "only the PLAN round was offloaded"


def _manifest() -> Any:
    from gateway_v2.domain.state import Manifest, Version

    return Manifest(StateKind.KEY, Version(1, 40), 40, 40, 0, "s")


def _records(count: int) -> list[Any]:
    from gateway_v2.domain.state import SignedRecord, StateOp, Version

    return [
        SignedRecord(StateKind.KEY, f"k{i}", Version(1, i), i, "h", False, StateOp.PUT, "s", b"{}")
        for i in range(1, count + 1)
    ]


# --- KEY cold start ----------------------------------------------------------------------------


def test_fast_forward_jumps_to_the_head_and_marks_the_position() -> None:
    server = _published_server()
    client = fakeredis.FakeAsyncRedis(server=server)
    marked: list[int] = []
    sync = StateSynchroniser(
        FeedReader(ValkeyStateStore(client, KEYS), SECRET), {StateKind.PLAN: lambda _r: None},
    )
    report = _run(sync.fast_forward(StateKind.PLAN, marked.append))
    assert report.ok and report.applied == 0
    assert sync.cursor(StateKind.PLAN).feed_seq == 2 == marked[0]
    server.connected = False
    assert not _run(sync.fast_forward(StateKind.PLAN, marked.append)).ok


# --- N9 ----------------------------------------------------------------------------------------


class _Stamp:
    def __init__(self) -> None:
        self.is_fresh = True

    def fresh(self, now: float | None = None) -> bool:
        return self.is_fresh

    def unverified(self) -> str:
        return "stamp stale"


def _report(kind: StateKind, *, ok: bool = True, truncated: bool = False) -> RoundReport:
    return RoundReport(kind, START, 0, truncated, False, None if ok else "boom")


def test_a_kind_gate_needs_a_caught_up_round_and_ages_out() -> None:
    now = [0.0]
    stamp = _Stamp()
    gates = KindGates(stamp, fresh_ms=5_000, clock=lambda: now[0])
    gate = gates.gate(StateKind.PLAN)
    assert not gate.fresh() and "not been read" in gate.unverified()
    gates.observe([_report(StateKind.PLAN, truncated=True)])
    assert not gate.fresh(), "progress is not caught up"
    gates.observe([_report(StateKind.PLAN, ok=False)])
    assert not gate.fresh(), "a failed round has truncated=False but is not caught up"
    gates.observe([_report(StateKind.PLAN)])
    assert gate.fresh()
    now[0] = 6.0
    assert not gate.fresh() and "over the" in gate.unverified()
    gates.observe([_report(StateKind.PLAN)])
    assert gate.fresh()
    stamp.is_fresh = False
    assert not gate.fresh() and gate.unverified() == "stamp stale"
    assert gates.ready([StateKind.PLAN]) == (False, "stamp stale")


# --- N19 ---------------------------------------------------------------------------------------


def test_an_offboarded_org_is_never_served_its_old_plan() -> None:
    from gateway_v2.domain.state import SignedRecord, StateOp, Version
    from gateway_v2.runtime.state_sig import make_record

    store = PlanStore()
    snapshot = ReplicaSnapshot(store)
    applier = PlanDeltaApplier(store, snapshot)
    body = encode_plan_body(PlanDocument("a", StreamingMode.INCREMENTAL, (_draft(),)))
    applier.apply([make_record(SECRET, StateKind.PLAN, "a", body, Version(1, 1), 1)])
    assert isinstance(snapshot.lookup("a"), ExecutionPlan)
    applier.apply(
        [make_record(SECRET, StateKind.PLAN, "a", {"org_id": "a"}, Version(1, 2), 2,
                     deleted=True, op=StateOp.OFFBOARD)],
    )
    bad = SignedRecord(StateKind.PLAN, "a", Version(1, 3), 3, "h", False, StateOp.PUT, "s", b"[]")
    applier.apply([bad])  # a rejected re-onboard: known again, no plan
    assert isinstance(snapshot.lookup("a"), PlanUnavailable), "the pre-offboard plan is gone"
