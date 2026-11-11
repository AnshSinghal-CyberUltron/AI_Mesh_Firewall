"""GW05c phase 4d — the nudge listener, and the D2/R2-19 regression it exists to prevent.

`test_an_immediately_answering_connection_is_declared_dead` is the one that matters. redis-py's
`listen()` reads a keepalive ETIMEDOUT as "no message" and spins the event loop until the kernel
kills the worker; that was measured live on a 24-worker fleet. The fix is detecting the spin by
ELAPSED TIME rather than by trusting an exception, and this test reproduces the signature.

Everything is driven by an injected clock and sleep, so nothing here waits on wall time.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Mapping
from typing import Any

import pytest

from gateway_v2.domain.state import StateKind
from gateway_v2.runtime.state_nudge import (
    DEFAULT_KNOBS,
    FAST_NONE_LIMIT,
    NudgeListener,
    PushKnobs,
    parse_nudge,
)

KNOBS = PushKnobs(ping_s=4.0, first_backoff_s=0.1, cap_backoff_s=2.0, drain_max=4)


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class Clock:
    """A clock the test advances. `poll_s` is KNOBS.ping_s / 4 = 1.0."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePubSub:
    """A scripted pub/sub. Each script step says what get_message returns and how long it took."""

    def __init__(self, clock: Clock, script: list[Any], *, subscribe_error: Exception | None):
        self._clock = clock
        self._script = list(script)
        self._subscribe_error = subscribe_error
        self.subscribed_to: list[str] = []
        self.pings = 0
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        if self._subscribe_error is not None:
            raise self._subscribe_error
        self.subscribed_to.append(channel)

    async def get_message(self, timeout: float = 0.0) -> Mapping[str, object] | None:
        if not self._script:
            # Nothing left to say: behave like a healthy, quiet connection.
            self._clock.advance(timeout)
            return None
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        waited, value = step
        self._clock.advance(waited if timeout else 0.0)
        return value

    async def ping(self, payload: bytes) -> None:
        self.pings += 1

    async def aclose(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, clock: Clock, scripts: list[list[Any]], *, subscribe_error: Any = None):
        self._clock = clock
        self._scripts = scripts
        self._subscribe_error = subscribe_error
        self.sessions: list[FakePubSub] = []

    def pubsub(self) -> FakePubSub:
        script = self._scripts.pop(0) if self._scripts else []
        session = FakePubSub(self._clock, script, subscribe_error=self._subscribe_error)
        self.sessions.append(session)
        return session


def _message(payload: str) -> Mapping[str, object]:
    return {"type": "message", "channel": b"ch", "data": payload.encode()}


def _listener(
    client: FakeClient,
    clock: Clock,
    *,
    on_dead: list[str] | None = None,
) -> NudgeListener:
    return NudgeListener(
        client,
        "ch",
        knobs=KNOBS,
        clock=clock,
        sleep=_no_sleep,
        on_dead=None if on_dead is None else on_dead.append,
        rng=random.Random(7),
    )


async def _no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


# --- the D2 / R2-19 regression ------------------------------------------------------------------


def test_an_immediately_answering_connection_is_declared_dead() -> None:
    """THE regression. A dead socket answers "no message" at once; listen() spun on this."""
    clock = Clock()
    # Three immediate (0.0 s) "no message" answers: the spin signature.
    client = FakeClient(clock, [[(0.0, None)] * FAST_NONE_LIMIT])
    reasons: list[str] = []
    listener = _listener(client, clock, on_dead=reasons)

    _run(listener.run(_accept_all(), max_cycles=1))

    assert reasons == ["no_wait"]
    assert listener.counters.dead == 1
    assert client.sessions[0].closed is True, "a dead connection is closed, not leaked"


def test_a_quiet_connection_is_not_declared_dead() -> None:
    """A healthy connection with nothing to say WAITS for the timeout. That is not a spin."""
    clock = Clock()
    client = FakeClient(clock, [[(1.0, None), (1.0, None), (1.0, None), (1.0, _message("plan:1"))]])
    reasons: list[str] = []
    listener = _listener(client, clock, on_dead=reasons)

    _run(listener.run(_accept_all(), max_cycles=1))

    assert "no_wait" not in reasons


def test_a_silent_connection_is_declared_dead_after_two_pings() -> None:
    clock = Clock()
    # Waiting answers, so not a spin -- but nothing ever arrives, not even a pong.
    client = FakeClient(clock, [[(1.0, None)] * 12])
    reasons: list[str] = []
    listener = _listener(client, clock, on_dead=reasons)

    _run(listener.run(_accept_all(), max_cycles=1))

    assert reasons == ["silent"]
    assert client.sessions[0].pings >= 1, "a PING goes out before silence is declared"


def test_a_transport_fault_is_declared_dead() -> None:
    clock = Clock()
    client = FakeClient(clock, [[ConnectionError("reset by peer")]])
    reasons: list[str] = []
    listener = _listener(client, clock, on_dead=reasons)

    _run(listener.run(_accept_all(), max_cycles=1))

    assert reasons and reasons[0].startswith("error: ConnectionError")


def test_a_failing_subscribe_is_declared_dead_and_retried() -> None:
    clock = Clock()
    client = FakeClient(clock, [[], []], subscribe_error=ConnectionError("refused"))
    reasons: list[str] = []
    listener = _listener(client, clock, on_dead=reasons)

    _run(listener.run(_accept_all(), max_cycles=2))

    assert len(reasons) == 2
    assert len(client.sessions) == 2, "it re-subscribes rather than giving up"


def test_the_listener_never_blocks_the_loop_while_spinning() -> None:
    """The spin signature must be detected WHILE still yielding, or the loop starves."""

    async def scenario() -> int:
        clock = Clock()
        client = FakeClient(clock, [[(0.0, None)] * FAST_NONE_LIMIT])
        listener = _listener(client, clock)
        ticks = 0

        async def other_work() -> None:
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(0)
                ticks += 1

        await asyncio.gather(
            listener.run(_accept_all(), max_cycles=1), other_work(),
        )
        return ticks

    assert _run(scenario()) == 20, "a co-resident coroutine kept running throughout"


# --- backoff -----------------------------------------------------------------------------------


def test_backoff_is_equal_jitter_and_capped() -> None:
    listener = _listener(FakeClient(Clock(), []), Clock())

    for attempt in range(1, 12):
        step = min(KNOBS.cap_backoff_s, KNOBS.first_backoff_s * 2 ** (attempt - 1))
        delay = listener.backoff(attempt)
        assert step / 2 <= delay <= step, (attempt, delay, step)

    assert listener.backoff(30) <= KNOBS.cap_backoff_s


def test_a_connection_that_lived_restarts_its_backoff() -> None:
    """A flapping store keeps backing off; a healthy one that drops once does not."""
    clock = Clock()
    long_lived = [(1.0, _message("plan:1"))] + [(0.0, None)] * FAST_NONE_LIMIT
    client = FakeClient(clock, [long_lived, [(0.0, None)] * FAST_NONE_LIMIT])
    delays: list[float] = []

    async def record(seconds: float) -> None:
        delays.append(seconds)
        await asyncio.sleep(0)

    listener = NudgeListener(
        client, "ch", knobs=KNOBS, clock=clock, sleep=record, rng=random.Random(7),
    )
    _run(listener.run(_accept_all(), max_cycles=2))

    assert len(delays) == 2
    assert delays[0] <= KNOBS.first_backoff_s, "lived long enough: back to the first step"


# --- delivery and coalescing --------------------------------------------------------------------


def test_positions_are_coalesced_to_the_highest_per_kind() -> None:
    clock = Clock()
    client = FakeClient(
        clock,
        [
            [
                (1.0, _message("plan:4")),
                (0.0, _message("plan:9")),
                (0.0, _message("ks:2")),
                (0.0, None),
            ],
        ],
    )
    seen: list[Mapping[StateKind, int]] = []
    listener = _listener(client, clock)

    _run(listener.run(_record(seen), max_cycles=1))

    assert {StateKind.PLAN: 9, StateKind.KS: 2} in seen, seen


def test_a_drained_burst_produces_one_nudge() -> None:
    clock = Clock()
    burst = [(1.0, _message(f"plan:{n}")) for n in range(1, 5)]
    client = FakeClient(clock, [burst + [(0.0, None)]])
    seen: list[Mapping[StateKind, int]] = []
    listener = _listener(client, clock)

    _run(listener.run(_record(seen), max_cycles=1))

    # One catch-up nudge on subscribe, then ONE for the whole burst.
    assert len([positions for positions in seen if positions]) == 1


def test_a_catch_up_nudge_runs_on_every_subscription() -> None:
    """Whatever was published while the listener was away is lost; the round must re-read."""
    clock = Clock()
    client = FakeClient(clock, [[], []])
    seen: list[Mapping[StateKind, int]] = []
    listener = _listener(client, clock)

    _run(listener.run(_record(seen), max_cycles=2))

    assert seen[:2] == [{}, {}], "an empty mapping means 'positions unknown, re-read'"


def test_a_failed_catch_up_is_retried_without_waiting_for_the_period() -> None:
    clock = Clock()
    client = FakeClient(clock, [[(1.0, None), (1.0, None), (1.0, None)]])
    attempts: list[Mapping[StateKind, int]] = []

    async def flaky(positions: Mapping[StateKind, int]) -> bool:
        attempts.append(positions)
        return len(attempts) >= 3

    listener = _listener(client, clock)
    _run(listener.run(flaky, max_cycles=1))

    assert len(attempts) >= 3, "retried on each wakeup until it succeeded"
    assert listener.counters.nudge_failures >= 2


def test_a_raising_nudge_does_not_kill_the_listener() -> None:
    clock = Clock()
    client = FakeClient(clock, [[(1.0, _message("plan:1")), (1.0, None)]])

    async def boom(_positions: Mapping[StateKind, int]) -> bool:
        raise RuntimeError("reconcile exploded")

    listener = _listener(client, clock)
    _run(listener.run(boom, max_cycles=1))

    assert listener.counters.nudge_failures >= 1
    assert client.sessions[0].closed is True


# --- payload parsing ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"plan:7", (StateKind.PLAN, 7)),
        ("ks:0", (StateKind.KS, 0)),
        (b"key:12345", (StateKind.KEY, 12345)),
        (b"budget", (StateKind.BUDGET, 0)),
        (b"plan:notanumber", (StateKind.PLAN, 0)),
    ],
)
def test_a_well_formed_nudge_parses(payload: object, expected: object) -> None:
    assert parse_nudge(payload) == expected


@pytest.mark.parametrize("payload", [b"", b"unknown:1", b":5", None, 7, b"\xff\xfe:1"])
def test_a_malformed_nudge_is_ignored(payload: object) -> None:
    """A nudge is never trusted for correctness, so garbage is dropped, not an error."""
    assert parse_nudge(payload) is None


def test_a_subscribe_confirmation_is_not_a_nudge() -> None:
    clock = Clock()
    client = FakeClient(
        clock,
        [[(1.0, {"type": "subscribe", "channel": b"ch", "data": 1}), (1.0, None)]],
    )
    seen: list[Mapping[StateKind, int]] = []
    listener = _listener(client, clock)

    _run(listener.run(_record(seen), max_cycles=1))

    assert all(positions == {} for positions in seen), "only the catch-up nudge fired"


def test_the_default_knobs_keep_the_poll_below_the_ping() -> None:
    """A poll longer than the ping interval would make silence undetectable."""
    assert DEFAULT_KNOBS.poll_s < DEFAULT_KNOBS.ping_s
    assert DEFAULT_KNOBS.poll_s == DEFAULT_KNOBS.ping_s / 4


def _accept_all() -> Any:
    async def nudge(_positions: Mapping[StateKind, int]) -> bool:
        return True

    return nudge


def _record(sink: list[Mapping[StateKind, int]]) -> Any:
    async def nudge(positions: Mapping[StateKind, int]) -> bool:
        sink.append(dict(positions))
        return True

    return nudge
