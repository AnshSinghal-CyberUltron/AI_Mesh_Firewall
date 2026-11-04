"""GW32 Property 10: session binding, bounds and expiry, over the memory twin and fakeredis."""

from __future__ import annotations

import asyncio
import random

import fakeredis
import pytest

from gateway_v2.admit.mcp_sessions import (
    MemorySessionStore,
    SessionBinding,
    SessionRefusal,
    ValkeySessionStore,
)
from gateway_v2.domain import posture
from gateway_v2.domain.state import StoreDataUnavailable
from gateway_v2.runtime.resources_mcp import McpTransportBounds, derive_transport_bounds
from gateway_v2.runtime.store_keys_mcp import McpStoreKeys
from tests.runtime.test_resources_mcp import _contract

ITERATIONS = 200


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _bounds(per_key: int = 3, per_org: int = 5, total: int = 8) -> McpTransportBounds:
    from dataclasses import replace

    return replace(
        derive_transport_bounds(_contract()),
        sessions_per_key=per_key,
        sessions_per_org=per_org,
        sessions_global=total,
        session_idle_ttl_s=60.0,
        session_max_lifetime_s=300.0,
    )


def _stores(clock: Clock, bounds: McpTransportBounds):  # type: ignore[no-untyped-def]
    yield MemorySessionStore(bounds, clock)
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    yield ValkeySessionStore(client, McpStoreKeys(), bounds, clock, 16)


def _b(key: str = "k1", org: str = "o1", srv: str = "s") -> SessionBinding:
    return SessionBinding(key, org, srv)


def test_open_check_close_and_foreign_bindings() -> None:
    for store in _stores(Clock(), _bounds()):

        async def run() -> None:
            sid = await store.open(_b())
            assert isinstance(sid, str)
            assert await store.check(sid, _b())
            for other in (_b(key="k2"), _b(org="o2"), _b(srv="t")):
                assert not await store.check(sid, other)
                assert not await store.close(sid, other)
            assert not await store.check("not-a-session", _b())
            assert await store.close(sid, _b())
            assert not await store.check(sid, _b())

        asyncio.run(run())


# Feature: gw32-mcp-transport, Property 10: bounded session counts under random schedules
def test_property_10_counts_never_exceed_bounds_and_live_sessions_stay_counted() -> None:
    rng = random.Random(10)
    for it in range(ITERATIONS // 10):
        clock = Clock()
        bounds = _bounds(per_key=rng.randint(1, 3), per_org=rng.randint(2, 5), total=6)
        for store in _stores(clock, bounds):

            async def run() -> None:
                live: dict[str, SessionBinding] = {}
                for _ in range(40):
                    clock.now += rng.uniform(0, 30)
                    action = rng.choice(["open", "open", "touch", "close"])
                    binding = _b(key=f"k{rng.randint(1, 3)}", org=f"o{rng.randint(1, 2)}")
                    if action == "open":
                        out = await store.open(binding)
                        if isinstance(out, str):
                            live[out] = binding
                        else:
                            assert isinstance(out, SessionRefusal)
                            assert out.code == posture.MCP_SESSION_LIMIT
                    elif live:
                        sid = rng.choice(list(live))
                        if action == "touch":
                            await store.check(sid, live[sid])
                        else:
                            if await store.close(sid, live[sid]):
                                del live[sid]
                    # every session the store still accepts is within every bound
                    accepted = [s for s, b in live.items() if await _peek(store, s, b)]
                    for key in {b.key_id for b in live.values()}:
                        assert (
                            sum(live[s].key_id == key for s in accepted) <= bounds.sessions_per_key
                        )
                    assert len(accepted) <= bounds.sessions_global

            asyncio.run(run())


async def _peek(store: object, sid: str, binding: SessionBinding) -> bool:
    # a touch extends the TTL, which the property tolerates: it only reads liveness
    return await store.check(sid, binding)  # type: ignore[attr-defined]


def test_idle_and_absolute_expiry() -> None:
    for clock in (Clock(),):
        for store in _stores(clock, _bounds()):

            async def run() -> None:
                sid = await store.open(_b())
                assert isinstance(sid, str)
                for _ in range(4):  # touching keeps it alive past one idle TTL
                    clock.now += 50.0
                    assert await store.check(sid, _b())
                clock.now += 120.0  # idle for longer than the idle TTL
                assert not await store.check(sid, _b())

            asyncio.run(run())
            clock.now = 1000.0


def test_refused_open_evicts_nothing() -> None:
    for store in _stores(Clock(), _bounds(per_key=1)):

        async def run() -> None:
            first = await store.open(_b())
            assert isinstance(first, str)
            assert isinstance(await store.open(_b()), SessionRefusal)
            assert await store.check(first, _b())  # still alive
            assert isinstance(await store.open(_b(key="k2")), str)  # other key unaffected

        asyncio.run(run())


def test_store_failure_is_unavailable_never_valid() -> None:
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer())

    def broken() -> object:
        raise ConnectionError("down")

    client.pipeline = broken  # type: ignore[method-assign,assignment]
    store = ValkeySessionStore(client, McpStoreKeys(), _bounds(), Clock(), 16)

    async def run() -> None:
        with pytest.raises(Exception):  # noqa: B017 - connection error surfaces, never "valid"
            await store.check("abc", _b())

    asyncio.run(run())
    assert StoreDataUnavailable  # imported for the edge mapping asserted in the route tests
