"""GW17c properties 2, 3, 4 and the cap outage property, over fakeredis and injected clocks."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Coroutine
from typing import Any

import fakeredis
import pytest

from gateway_v2.admit.mcp_cap import CallCapTable
from gateway_v2.admit.mcp_metrics import McpPolicyMetrics
from gateway_v2.admit.mcp_policy import filter_listed, tool_name_valid, tool_permitted
from gateway_v2.domain import posture
from gateway_v2.domain.identity import DENY_ALL_TOOLS, Principal
from gateway_v2.runtime.resources_mcp import CapBounds
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys

ITERATIONS = 200


def _principal(key: str = "k1", tools: frozenset[str] = frozenset(), cap: int = 0) -> Principal:
    return Principal(key, "org", 0.0, 0.0, 1, tools, cap)


def _bounds(workers: int = 2, slots: int = 8, bootstrap: int = 1) -> CapBounds:
    return CapBounds(
        cap_window_s=100.0,
        chunk_max=8,
        bootstrap=bootstrap,
        retry_backoff_s=1.0,
        max_cas_tries=16,
        table_slots=slots,
        workers=workers,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Fleet:
    """N workers (tables) over ONE fakeredis; refills are queued and run on demand."""

    def __init__(self, workers: int, *, slots: int = 8, bootstrap: int = 1, seed: int = 0) -> None:
        self.server = fakeredis.FakeServer()
        self.client = fakeredis.FakeAsyncRedis(server=self.server)
        self.clock = Clock()
        self.pending: list[Coroutine[Any, Any, None]] = []
        self.tables = [
            CallCapTable(
                client=self.client,
                keys=McpStoreKeys(),
                bounds=_bounds(workers, slots, bootstrap),
                clock=self.clock,
                rng=random.Random(seed + i),
                metrics=McpPolicyMetrics(),
                spawn=self.pending.append,
            )
            for i in range(workers)
        ]

    def settle(self) -> None:
        async def run() -> None:
            while self.pending:
                await self.pending.pop(0)

        asyncio.run(run())

    def drop_refills(self) -> None:
        for coro in self.pending:
            coro.close()
        self.pending.clear()


# Feature: gw32-mcp-transport, Property 2: filter_listed keeps permitted tools in order
def test_property_2_filter_listed() -> None:
    rng = random.Random(2)
    for _ in range(ITERATIONS):
        names = [f"t{i}" for i in range(rng.randint(0, 12))]
        tools: list[object] = [{"name": n, "description": "d"} for n in names]
        tools += [rng.choice(["junk", 3, None, {"nome": "x"}, {"name": 5}]) for _ in range(3)]
        rng.shuffle(tools)
        allowed = frozenset(rng.sample(names, rng.randint(0, len(names)))) if names else frozenset()
        principal = _principal(tools=allowed)
        kept = filter_listed(principal, tools)
        if not allowed:
            assert kept == tools
            continue
        assert all(isinstance(t, dict) and t["name"] in allowed for t in kept)
        assert kept == [t for t in tools if isinstance(t, dict) and t.get("name") in allowed]
        for tool in kept:
            assert tool is next(t for t in tools if t is tool)  # not copied, not mutated


def test_deny_all_and_name_validity() -> None:
    deny = _principal(tools=frozenset({DENY_ALL_TOOLS}))
    assert not tool_permitted(deny, "anything") and filter_listed(deny, [{"name": "a"}]) == []
    assert tool_permitted(_principal(), "x")
    assert not tool_name_valid("bad\x00name", 128) and not tool_name_valid("", 128)
    assert not tool_name_valid(None, 128) and not tool_name_valid("x" * 129, 128)
    assert tool_name_valid("good_tool", 128)


# Feature: gw32-mcp-transport, Property 3: cap conservation
def test_property_3_admitted_never_exceeds_cap_plus_declared_overshoot() -> None:
    rng = random.Random(3)
    for it in range(ITERATIONS):
        workers = rng.randint(1, 4)
        cap = rng.randint(1, 40)
        fleet = Fleet(workers, bootstrap=1, seed=it)
        principal = _principal(cap=cap)
        admitted = 0
        for _ in range(rng.randint(cap, cap * 4)):
            table = rng.choice(fleet.tables)
            if table.try_spend(principal, "r").admitted:
                admitted += 1
            if rng.random() < 0.4:
                fleet.settle()
        fleet.settle()
        assert admitted <= cap + workers * 1  # workers x bootstrap
    # a settled single worker admits exactly cap in one window
    fleet = Fleet(1, bootstrap=0)
    principal = _principal(cap=10)
    fleet.tables[0].try_spend(principal, "r")
    fleet.settle()
    total = sum(fleet.tables[0].try_spend(principal, "r").admitted for _ in range(30))
    fleet.settle()
    total += sum(fleet.tables[0].try_spend(principal, "r").admitted for _ in range(30))
    assert total <= 10


# Feature: gw32-mcp-transport, Property 4b: Retry-After is bounded by the window (R17.2)
def test_property_4_retry_after_bounds() -> None:
    rng = random.Random(4)
    for it in range(ITERATIONS):
        fleet = Fleet(1, bootstrap=0, seed=it)
        table = fleet.tables[0]
        principal = _principal(cap=rng.randint(1, 6))
        for _ in range(rng.randint(1, 4)):
            fleet.clock.now += rng.uniform(0, 30)
            verdict = table.try_spend(principal, "r")
            if rng.random() < 0.5:
                fleet.settle()
            if not verdict.admitted:
                assert verdict.code == posture.MCP_TOOL_CALL_CAP_EXCEEDED
                assert posture.MIN_RETRY_AFTER_S <= verdict.retry_after_s <= 100.0
                assert verdict.should_retry is False
    fleet.drop_refills()


# Feature: gw32-mcp-transport, Property 4: cap fail-closed (outage admits <= held + bootstrap)
def test_cap_outage_property() -> None:
    rng = random.Random(33)
    for it in range(ITERATIONS):
        fleet = Fleet(1, bootstrap=1, seed=it)
        table = fleet.tables[0]

        async def broken(*_a: object, **_k: object) -> Any:
            raise ConnectionError("store down")

        fleet.client.pipeline = broken  # type: ignore[method-assign,assignment]
        principal = _principal(cap=rng.randint(1, 30))
        admitted = 0
        for _ in range(rng.randint(5, 60)):
            fleet.clock.now += rng.uniform(0, 0.5)
            admitted += table.try_spend(principal, "r").admitted
            if rng.random() < 0.5:
                fleet.settle()
        assert admitted <= 1  # bootstrap only: nothing was ever granted


def test_eviction_tombstone_keeps_bootstrap_and_bounds_churn() -> None:
    fleet = Fleet(1, slots=2, bootstrap=1)
    table = fleet.tables[0]
    a, b, c = (_principal(key=k, cap=5) for k in ("a", "b", "c"))
    assert table.try_spend(a, "r").admitted  # bootstrap for a
    fleet.drop_refills()
    table.try_spend(b, "r")
    fleet.drop_refills()
    table.try_spend(c, "r")  # evicts the earliest-window lease (a or b) into a tombstone
    fleet.drop_refills()
    # re-creating the evicted key must not hand out a second bootstrap call
    evicted = a if "a" not in table._leases else b  # noqa: SLF001
    assert not table.try_spend(evicted, "r").admitted


def test_cap_edit_keeps_bootstrap_used() -> None:
    fleet = Fleet(1, bootstrap=1)
    table = fleet.tables[0]
    assert table.try_spend(_principal(cap=5), "r").admitted
    fleet.drop_refills()
    assert not table.try_spend(_principal(cap=9), "r").admitted  # cap edit mints no new bootstrap


def test_metrics_series_are_fixed_and_fail_open_pinned_zero() -> None:
    metrics = McpPolicyMetrics()
    metrics.observe_tool_denied()
    reading = metrics.snapshot()
    assert McpPolicyMetrics.SERIES_COUNT == 6 and reading.fail_open_total == 0
    assert reading.tool_denied_total == 1


@pytest.mark.parametrize("cap", [0])
def test_unrestricted_key_never_touches_the_store(cap: int) -> None:
    from gateway_v2.edge.mcp_policy_gate import apply_tool_policy, spend_cap

    fleet = Fleet(1)
    principal = _principal(cap=cap)
    assert apply_tool_policy(principal, "tools/call", {"name": "x"}, None) is None
    assert spend_cap(fleet.tables[0], principal, "tools/call", "r") is None
    assert fleet.pending == []
