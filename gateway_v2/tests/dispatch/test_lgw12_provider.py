"""LGW12 StubProviderClient unit tests (GW12, task 4.4).

Pins the two contracts the egress loop relies on from
``gateway_v2.dispatch.provider``:

1. ``StubProviderClient`` satisfies the ``@runtime_checkable`` ``ProviderClient``
   protocol — an ``isinstance`` check passes (R8.1).
2. Without an abort, ``open`` replays the full scripted sequence via ``async for``
   driven by ``asyncio.run``; with ``abort`` awaited after the first event, the
   iteration halts at the next event boundary so ONLY the pre-abort events are
   delivered (R8.2).

The stub is built from a scripted sequence of ``UpstreamEvent`` and a seeded
``random.Random`` (the stub's injected-randomness contract), so the scripts and
the abort point are deterministic (house idiom; no ``hypothesis``). Async paths
run through ``asyncio.run``.

_Design: Components §3; Testing Strategy._
_Validates: Requirements 8.1, 8.2, 8.5._
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

from gateway_v2.dispatch.provider import (
    ProviderClient,
    StubProviderClient,
    UpstreamEvent,
    UpstreamRequest,
)

_ITERATIONS = 10_000


def _run[T](coro: Awaitable[T]) -> T:
    """Drive one coroutine to completion (house idiom; no async test plugin)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _request() -> UpstreamRequest:
    """A minimal request — the stub replays its script regardless of this value."""
    return UpstreamRequest(destination="amf://upstream/org-test", body={})


def _random_event(rng: random.Random, *, final: bool) -> UpstreamEvent:
    """A random content ``UpstreamEvent`` with a ``(channel, delta)`` payload."""
    text = "".join(chr(rng.randint(0x20, 0x7E)) for _ in range(rng.randint(0, 8)))
    return UpstreamEvent(text_deltas=(("message", text),), final=final)


def _random_script(rng: random.Random, length: int) -> tuple[UpstreamEvent, ...]:
    """A script of ``length`` events; the last one carries ``final=True``."""
    return tuple(_random_event(rng, final=(k == length - 1)) for k in range(length))


def test_stub_is_instance_of_runtime_checkable_protocol() -> None:
    """``StubProviderClient`` satisfies the ``@runtime_checkable`` ``ProviderClient`` (R8.1)."""
    stub = StubProviderClient(script=(), rng=random.Random(0x120851))
    assert isinstance(stub, ProviderClient)


def test_full_script_replays_without_abort() -> None:
    """Without an abort, ``open`` yields every scripted event in order (R8.1).

    Swept over seeded random scripts of varied lengths; each ``async for`` drain
    (via ``asyncio.run``) must reproduce the script exactly.
    """
    seed = 0x120852
    rng = random.Random(seed)

    async def drain(client: StubProviderClient) -> list[UpstreamEvent]:
        got: list[UpstreamEvent] = []
        async for event in client.open(_request()):
            got.append(event)
        return got

    for i in range(_ITERATIONS):
        script = _random_script(rng, length=rng.randint(0, 6))
        stub = StubProviderClient(script=script, rng=rng)
        delivered = _run(drain(stub))
        assert tuple(delivered) == script, f"i={i} seed={seed:#x} replay diverged from script"


def test_abort_after_first_event_halts_iteration() -> None:
    """``abort`` awaited after the first event stops iteration at the next boundary (R8.2).

    Only the pre-abort events are delivered: the stub checks its one-way abort
    latch BEFORE each yield, so awaiting ``abort`` during the first iteration
    delivers exactly that first event and nothing after it. Scripts always have
    >= 2 events so "halted" is distinguishable from "exhausted".
    """
    seed = 0x120853
    rng = random.Random(seed)

    async def drain_aborting_after_first(
        client: StubProviderClient,
    ) -> list[UpstreamEvent]:
        got: list[UpstreamEvent] = []
        async for event in client.open(_request()):
            got.append(event)
            if len(got) == 1:
                # Abort mid-iteration; the next loop step must observe the latch.
                await client.abort()
        return got

    for i in range(_ITERATIONS):
        script = _random_script(rng, length=rng.randint(2, 8))
        stub = StubProviderClient(script=script, rng=rng)
        delivered = _run(drain_aborting_after_first(stub))
        assert len(delivered) == 1, (
            f"i={i} seed={seed:#x} abort delivered {len(delivered)} events, want 1"
        )
        assert delivered[0] == script[0], (
            f"i={i} seed={seed:#x} pre-abort event was not the first scripted event"
        )


def test_abort_before_iteration_delivers_nothing() -> None:
    """``abort`` awaited before the first yield halts immediately (R8.2, boundary case)."""
    seed = 0x120854
    rng = random.Random(seed)

    async def abort_then_drain(client: StubProviderClient) -> list[UpstreamEvent]:
        await client.abort()
        got: list[UpstreamEvent] = []
        async for event in client.open(_request()):
            got.append(event)
        return got

    for i in range(_ITERATIONS):
        script = _random_script(rng, length=rng.randint(1, 6))
        stub = StubProviderClient(script=script, rng=rng)
        delivered = _run(abort_then_drain(stub))
        assert delivered == [], f"i={i} seed={seed:#x} pre-iteration abort still delivered events"
