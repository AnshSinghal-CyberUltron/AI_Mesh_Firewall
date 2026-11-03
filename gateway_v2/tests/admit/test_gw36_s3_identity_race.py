"""GW36 S3, N18: a fill in flight while its key's delta is applied is never cached stale.

The fetch reads the "store" at a point the schedule chooses, then waits; the schedule may apply
a delta for that key (revoke, add, or an unrelated key) before the fetch completes. Whatever the
interleaving, once a delta has been applied the cache must answer the post-delta truth: a key
revoked by an applied delta is never served afterwards, and a key added by one is never denied.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from gateway_v2.admit.identity import IdentityCache
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, StoreDataUnavailable, Version

KEY = "k" * 64
OTHER = "o" * 64


def _principal(feed_seq: int) -> Principal:
    return Principal(key_id="id", org_id="acme", rate_per_s=0.0, burst=0.0, feed_seq=feed_seq)


def _record(key: str, feed_seq: int, *, deleted: bool) -> SignedRecord:
    return SignedRecord(
        kind=StateKind.KEY,
        key=key,
        version=Version(1, feed_seq),
        feed_seq=feed_seq,
        content_hash="h",
        deleted=deleted,
        op=StateOp.REVOKE if deleted else StateOp.PUT,
        signature="s",
        body=b"{}",
    )


async def _one(rng: random.Random, where: str) -> None:
    live = rng.random() < 0.5  # the store's truth before the delta
    feed_seq = 5
    cache = IdentityCache(negative_ttl_s=60.0)
    cache.mark_applied(feed_seq)
    store = {"live": live, "seq": feed_seq}
    read_done = asyncio.Event()
    release = asyncio.Event()
    reads = 0

    async def fetch(_key: str) -> Principal | None:
        nonlocal reads
        reads += 1
        seen_live, seen_seq = store["live"], store["seq"]  # the read happens HERE
        if reads == 1:
            read_done.set()
            await release.wait()
        return _principal(int(seen_seq)) if seen_live else None

    task = asyncio.create_task(cache.resolve(KEY, fetch))
    await read_done.wait()
    action = rng.choice(("flip", "other", "none"))
    if action == "flip":
        store["live"], store["seq"] = not live, feed_seq + 1
        cache.on_delta([_record(KEY, feed_seq + 1, deleted=live)], feed_seq + 1)
    elif action == "other":
        cache.on_delta([_record(OTHER, feed_seq + 1, deleted=True)], feed_seq + 1)
    release.set()
    try:
        await task
    except StoreDataUnavailable:
        return  # fail closed is always allowed
    truth = bool(store["live"])
    for _ in range(2):  # the cached answer, then again from cache
        answer = await cache.resolve(KEY, fetch)
        assert (answer is not None) == truth, f"{where} action={action} live={live}"


def test_an_in_flight_fill_never_caches_the_pre_delta_answer() -> None:
    seed = 0x36_5318
    rng = random.Random(seed)

    async def go() -> None:
        for iteration in range(10_000):
            await _one(rng, f"seed={seed} iteration={iteration}")

    asyncio.run(go())


def test_a_key_touched_twice_mid_read_fails_closed() -> None:
    async def go() -> None:
        cache = IdentityCache()
        calls = 0

        async def fetch(_key: str) -> Principal | None:
            nonlocal calls
            calls += 1
            cache.on_delta([_record(KEY, calls, deleted=False)], calls)  # touched every read
            await asyncio.sleep(0)
            return _principal(0)

        with pytest.raises(StoreDataUnavailable):
            await cache.resolve(KEY, fetch)
        assert calls == 2, "one retry, then fail closed"

    asyncio.run(go())
