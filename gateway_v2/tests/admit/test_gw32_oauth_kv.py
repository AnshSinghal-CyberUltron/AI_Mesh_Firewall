"""GW32: the OAuth KV Protocol behaves the same over the memory twin and fakeredis."""

from __future__ import annotations

import asyncio

import fakeredis
import pytest

from gateway_v2.admit.oauth_store import MemoryKV, ValkeyKV
from gateway_v2.domain.state import StoreDataUnavailable


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


def _kvs(clock: Clock):  # type: ignore[no-untyped-def]
    yield MemoryKV(clock)
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    yield ValkeyKV(client, 16)


def test_put_get_take_is_single_use() -> None:
    for kv in _kvs(Clock()):

        async def run() -> None:
            await kv.put("k", {"a": 1}, 10)
            assert await kv.get("k") == {"a": 1}
            results = await asyncio.gather(kv.take("k"), kv.take("k"), kv.take("k"))
            assert sorted(r is not None for r in results) == [False, False, True]
            assert await kv.get("k") is None

        asyncio.run(run())


def test_counter_peek_and_sets() -> None:
    for kv in _kvs(Clock()):

        async def run() -> None:
            assert await kv.peek("c") == 0
            assert [await kv.incr("c", 10) for _ in range(3)] == [1, 2, 3]
            assert await kv.peek("c") == 3
            await kv.sadd("s", "a", 10)
            await kv.sadd("s", "b", 10)
            await kv.srem("s", "a")
            assert await kv.smembers("s") == {"b"}
            await kv.delete("s")

        asyncio.run(run())


def test_memory_expiry_and_failure_injection() -> None:
    clock = Clock()
    kv = MemoryKV(clock)

    async def run() -> None:
        await kv.put("k", {"a": 1}, 5)
        clock.now += 6
        assert await kv.get("k") is None
        kv.fail = True
        with pytest.raises(StoreDataUnavailable):
            await kv.get("k")

    asyncio.run(run())
