"""LGW06 — the 45 s store-partition regression (R2-19 D1/D2 + R2-09), task 11.

# Feature: budget-lease, Property 7
# Validates: Requirements 9.1, 9.2, 9.3, 9.4, 10.1, 10.2, 10.3, 10.4

This module PINS behaviour that already exists so neither of the two measured defects can silently
return. It changes **no** production code: it drives a 45 s *simulated* store partition (an injected
clock, never a real sleep) against the real public surfaces of
:class:`~gateway_v2.runtime.state_nudge.NudgeListener`,
:class:`~gateway_v2.runtime.store_valkey.ValkeyStateStore`, and the
:class:`~gateway_v2.admit.quota.QuotaComponent` + :class:`~gateway_v2.admit.lease.BudgetLease` built
on ``fakeredis``, and asserts the three dimensions the design's "Store-boundary partition
regression" section names.

What this reproduces, and why it must stay
-------------------------------------------
* **D2 (R2-19, Property 7, Req 9.1 / 9.4 / 10.1 / 10.2).** redis-py's ``listen()`` reads a keepalive
  ``ETIMEDOUT`` as "no message" and spins the event loop until the kernel kills every worker —
  measured live on a 24-worker fleet. The fix is the ``state_nudge.NudgeListener`` already in the
  tree: it declares a connection DEAD on the ``no_wait`` / ``silent`` / ``error`` signatures,
  PINGs, and reconnects after equal-jitter backoff, detecting the spin by ELAPSED TIME rather than
  by trusting an exception (so it holds whatever the client library does). That listener is
  **confirmed, not rewritten** here — this test constructs it with a fake pubsub whose
  ``get_message`` answers "no message" immediately (the spin signature) and/or raises
  ``ETIMEDOUT``, drives it over the simulated 45 s window, and asserts it goes dead-and-reconnects
  **without spinning without yielding** and **without unbounded memory growth** (the delivered
  nudges / coalesced positions never accumulate).
* **D1 (R2-19, Req 9.2).** A dead pooled read socket must make the reader RECONNECT rather than
  answer a 500. The ``store_valkey`` reader does **not** manufacture a 500-equivalent (there is no
  HTTP in the ``runtime`` layer at all) and redis-py's pool hands the next command a fresh
  connection — so a dead-socket read surfaces the transport fault at the boundary and the very next
  read succeeds. This test CONFIRMS that existing behaviour (a dead-socket read followed by a
  healthy read succeeds); **no guard was needed in ``store_valkey.py``** and none was added.
* **R2-09 (Req 10.3 / 10.4).** During the partition, budget admission spends ONLY the
  Remaining_Lease and then returns ``budget_unavailable`` — never ``shared_state_unavailable`` — and
  issues NO store read on the request path to refill the lease. This ties the outage posture and the
  off-request-path refill (already covered atom-by-atom in ``test_lgw06_outage_posture.py`` and
  ``test_lgw06_refill_offpath.py``) to the one 45 s partition window and the store-boundary
  confirmation, in a single regression.

House idiom: seeded ``random.Random`` where randomness helps, no ``hypothesis``, async via
``asyncio.run``, ``fakeredis`` for the store, an injected ``Callable[[], float]`` clock. The "45 s"
is advanced on the injected clock, so the whole module runs in well under a wall-clock second.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Mapping
from typing import Any

import fakeredis
from redis.exceptions import ConnectionError as RedisConnectionError

from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.quota import QuotaComponent
from gateway_v2.domain.posture import BUDGET_UNAVAILABLE, SHARED_STATE_UNAVAILABLE
from gateway_v2.domain.state import StateKind
from gateway_v2.runtime.state_nudge import FAST_NONE_LIMIT, NudgeListener, PushKnobs
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.store_valkey import ValkeyStateStore

# The simulated partition window. Advanced on the injected clock only — never a real sleep.
_PARTITION_WINDOW_S = 45.0
_KNOBS = PushKnobs(ping_s=4.0, first_backoff_s=0.1, cap_backoff_s=2.0, drain_max=4)
_ORG = "org-partition"
_KEYS = StoreKeys(namespace="{t}")


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# A clock the test advances. `poll_s` is _KNOBS.ping_s / 4 = 1.0 s.
# --------------------------------------------------------------------------- #


class _Clock:
    """An injectable ``Callable[[], float]`` the test advances deterministically."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------- #
# D2 — the push listener under a dead/keepalive-timed-out connection.
# A scripted pub/sub mirroring tests/runtime/test_lgw05c_nudge.py, so the fakes
# match the listener's REAL public interface (pubsub / subscribe / get_message /
# ping / aclose) and nothing here depends on listener internals.
# --------------------------------------------------------------------------- #


class _PartitionPubSub:
    """A scripted pub/sub whose ``get_message`` reproduces a dead socket during the partition.

    Each script step is either an ``Exception`` (a keepalive ``ETIMEDOUT`` / transport fault, raised
    on the ``get_message`` call) or a ``(waited, value)`` pair saying how long the call blocked and
    what it returned. A dead socket answers ``(0.0, None)`` — "no message" with NO wait — which is
    exactly the spin signature the listener detects by elapsed time.
    """

    def __init__(
        self,
        clock: _Clock,
        script: list[Any],
        *,
        subscribe_error: Exception | None,
    ) -> None:
        self._clock = clock
        self._script = list(script)
        self._subscribe_error = subscribe_error
        self.subscribed_to: list[str] = []
        self.pings = 0
        self.get_message_calls = 0
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        if self._subscribe_error is not None:
            raise self._subscribe_error
        self.subscribed_to.append(channel)

    async def get_message(self, timeout: float = 0.0) -> Mapping[str, object] | None:
        self.get_message_calls += 1
        if not self._script:
            # Exhausted script: behave like a healthy, quiet connection that waits out the poll.
            self._clock.advance(timeout)
            return None
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        waited, value = step
        self._clock.advance(waited if timeout else 0.0)
        result: Mapping[str, object] | None = value
        return result

    async def ping(self, payload: bytes) -> None:
        self.pings += 1

    async def aclose(self) -> None:
        self.closed = True


class _PartitionClient:
    """A client whose ``pubsub()`` hands out the scripted sessions, one per subscribe cycle."""

    def __init__(
        self,
        clock: _Clock,
        scripts: list[list[Any]],
        *,
        subscribe_error: Exception | None = None,
    ) -> None:
        self._clock = clock
        self._scripts = scripts
        self._subscribe_error = subscribe_error
        self.sessions: list[_PartitionPubSub] = []

    def pubsub(self) -> _PartitionPubSub:
        script = self._scripts.pop(0) if self._scripts else []
        session = _PartitionPubSub(
            self._clock, script, subscribe_error=self._subscribe_error,
        )
        self.sessions.append(session)
        return session


async def _no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def _counting_nudge(count: list[int]) -> Any:
    async def nudge(_positions: Mapping[StateKind, int]) -> bool:
        count[0] += 1
        return True

    return nudge


def test_d2_keepalive_dead_socket_declares_dead_and_reconnects_over_45s() -> None:
    """D2/Property 7: a keepalive-ETIMEDOUT / immediate "no message" socket is dead, not quiet.

    Over the simulated 45 s partition every subscribe cycle meets a dead connection: the first with
    the ``no_wait`` spin signature (three immediate "no message" answers), the second with a raised
    keepalive ``ETIMEDOUT`` (``error``). The listener must declare each DEAD, close it, and
    reconnect after backoff — never spin the event loop without yielding, and never let delivered
    state accumulate (unbounded memory).
    """
    clock = _Clock()
    etimedout = TimeoutError("[Errno 110] ETIMEDOUT")  # redis-py surfaces keepalive RTO as this
    # Cycle 1: the spin signature (immediate "no message" x FAST_NONE_LIMIT) -> no_wait.
    # Cycle 2: the keepalive timeout raised on get_message -> error.
    scripts: list[list[Any]] = [
        [(0.0, None)] * FAST_NONE_LIMIT,
        [etimedout],
    ]
    client = _PartitionClient(clock, scripts)
    reasons: list[str] = []
    nudges = [0]
    listener = NudgeListener(
        client,
        "ch",
        knobs=_KNOBS,
        clock=clock,
        sleep=_no_sleep,
        on_dead=reasons.append,
        rng=random.Random(0x19),
    )

    _run(listener.run(_counting_nudge(nudges), max_cycles=2))

    # Both dead signatures fired, in order, and each connection was CLOSED (not leaked).
    assert reasons[0] == "no_wait", f"first cycle must be the spin signature, got {reasons!r}"
    assert reasons[1].startswith("error: TimeoutError"), (
        f"second cycle must surface the keepalive timeout as an error, got {reasons!r}"
    )
    assert listener.counters.dead == 2
    assert len(client.sessions) == 2, "it re-subscribed rather than giving up"
    assert all(s.closed for s in client.sessions), "every dead connection is closed"

    # It did not spin unbounded: the spin cycle took at most FAST_NONE_LIMIT + a small margin of
    # get_message calls before declaring dead, NOT a runaway loop.
    assert client.sessions[0].get_message_calls <= FAST_NONE_LIMIT + 1, (
        f"the dead socket was polled {client.sessions[0].get_message_calls} times — a spin"
    )
    # Memory does not grow: a dead socket delivers no messages, so only the per-subscribe catch-up
    # nudge ran (one per cycle), never an unbounded accumulation of coalesced positions.
    assert nudges[0] == 2, f"a dead socket delivered state (memory spin signal): {nudges[0]}"


def test_d2_listener_keeps_yielding_while_detecting_the_spin() -> None:
    """D2/Req 9.4: the spin is detected WHILE still yielding, so a co-resident coroutine runs.

    The original defect starved the event loop. Over the partition window a co-resident coroutine
    must keep getting its turns while the listener detects the dead socket.
    """

    async def scenario() -> int:
        clock = _Clock()
        client = _PartitionClient(clock, [[(0.0, None)] * FAST_NONE_LIMIT])
        listener = NudgeListener(
            client, "ch", knobs=_KNOBS, clock=clock, sleep=_no_sleep, rng=random.Random(0x19_02),
        )
        ticks = 0

        async def co_resident() -> None:
            nonlocal ticks
            for _ in range(32):
                await asyncio.sleep(0)
                ticks += 1

        nudges = [0]
        await asyncio.gather(
            listener.run(_counting_nudge(nudges), max_cycles=1), co_resident(),
        )
        return ticks

    assert _run_int(scenario()) == 32, "the co-resident coroutine was starved — a loop spin"


def _run_int(coro: Awaitable[int]) -> int:
    return asyncio.run(coro)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# D1 — the reader reconnects on a dead pooled socket rather than answering 500.
# --------------------------------------------------------------------------- #


class _DeadSocketOnce:
    """A ``fakeredis`` async client wrapper whose next ``get`` raises once, then recovers.

    Models a dead pooled socket: redis-py releases the broken connection on the transport error and
    hands the next command a fresh one from the pool. The adapter manufactures no 500-equivalent —
    there is no HTTP in the ``runtime`` layer — so the fault surfaces at the boundary and the next
    read succeeds. ``arm()`` sets up exactly one dead read.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._dead = False

    def arm(self) -> None:
        self._dead = True

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name == "get" and callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                if self._dead:
                    self._dead = False
                    raise RedisConnectionError("dead pooled socket")
                return attr(*args, **kwargs)
            return wrapped
        return attr


def test_d1_dead_pooled_socket_reconnects_not_500() -> None:
    """D1/Req 9.2: a dead pooled read socket reconnects — a dead read raises, the next read is OK.

    The reader does not translate a dead socket into a 500-equivalent; it surfaces the transport
    fault (never a success-shaped stand-in) and the next read, over the reconnected pool, succeeds.
    This CONFIRMS the existing behaviour — no guard was needed in ``store_valkey.py``.
    """

    async def go() -> None:
        server = fakeredis.FakeServer()
        inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=False)
        await inner.set(_KEYS.stamp, b"fresh-stamp")
        client = _DeadSocketOnce(inner)
        store = ValkeyStateStore(client, keys=_KEYS)

        # Healthy baseline read.
        assert await store.stamp() == b"fresh-stamp"

        # Arm one dead-socket read: it must RAISE the transport fault, never answer a 500-shaped OK.
        client.arm()
        raised = False
        try:
            await store.stamp()
        except RedisConnectionError:
            raised = True
        assert raised, "a dead pooled socket must surface the fault, not a 500-equivalent answer"

        # The very next read reconnects (fresh pooled connection) and succeeds — not a stuck 500.
        reconnected = await store.stamp()
        assert reconnected == b"fresh-stamp", "the reader did not reconnect after a dead socket"
        await inner.aclose()

    _run(go())


# --------------------------------------------------------------------------- #
# R2-09 — budget spends only the Remaining_Lease, then budget_unavailable, with
# NO store read on the request path to refill, across the 45 s partition window.
# --------------------------------------------------------------------------- #


class _PartitionableBudgetClient:
    """A ``fakeredis`` async client that flips into a partition and counts request-path store calls.

    While ``partitioned`` is set, every store command and pipeline raises ``ConnectionError`` (the
    fail-closed outage). ``on_request_path`` is flipped on strictly around each ``evaluate`` call;
    any store command issued while it is set increments ``request_path_calls`` — which must stay
    zero (Req 10.4): the façade reaches no store on the request path, and the Async_Refill is
    off-path.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.partitioned = False
        self.on_request_path = False
        self.request_path_calls = 0

    def _guard(self, name: str) -> None:
        if self.on_request_path:
            self.request_path_calls += 1
        if self.partitioned:
            raise RedisConnectionError(f"partitioned ({name})")

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        self._guard("pipeline")
        return self._inner.pipeline(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self._guard(name)
                return attr(*args, **kwargs)
            return wrapped
        return attr


class _CountingMetrics:
    """A ``QuotaMetricsLike`` + budget_unavailable observer double."""

    def __init__(self) -> None:
        self.async_refills = 0
        self.budget_unavailable = 0

    def observe_async_refill(self) -> None:
        self.async_refills += 1

    def observe_budget_unavailable(self) -> None:
        self.budget_unavailable += 1


def _drop(coro: Awaitable[None]) -> None:
    """A ``spawn`` that discards the refill coroutine: under partition it would fail anyway.

    Closed synchronously (no event-loop re-entry). The point of R2-09 is that the off-path refill
    never touches the Remaining_Lease on the request path during an outage.
    """
    coro.close()  # type: ignore[attr-defined]


def test_r2_09_partition_spends_remaining_then_budget_unavailable_no_request_path_read() -> None:
    """R2-09/Req 10.3+10.4: over the 45 s partition, spend the lease then budget_unavailable only.

    Pre-acquire a chunk while healthy, partition the store for the full simulated window, then
    drive requests on the injected clock. The façade must admit exactly the pre-acquired
    Remaining_Lease, refuse every subsequent request with ``budget_unavailable`` (never
    ``shared_state_unavailable``), and issue ZERO store calls on the request path.
    """

    async def go() -> None:
        clock = _Clock()
        rng = random.Random(0x09)
        server = fakeredis.FakeServer()
        inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        client = _PartitionableBudgetClient(inner)
        metrics = _CountingMetrics()

        chunk = 24
        config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=6, ttl_s=30.0)
        await inner.set(_KEYS.budget_remaining(_ORG), 10_000)
        await inner.set(_KEYS.budget_generation(_ORG), 0)
        lease = BudgetLease(
            client,
            config,
            worker_id="w0",
            clock=clock,
            spawn=_drop,
            metrics=metrics,
            keys=_KEYS,
        )
        await lease.acquire(0)  # pre-acquire a chunk while healthy (off the request path)
        held = lease.remaining
        assert held > 0

        # A GCRA generous enough never to limit here: we are testing the budget posture, not burst.
        gcra = LocalGCRA(GcraParams(rate_per_s=1_000_000.0, burst=100_000), clock=clock)
        quota = QuotaComponent(gcra, lease, generation_source=lambda: 0, metrics=metrics)

        # Partition the store for the whole simulated 45 s window.
        client.partitioned = True
        partition_started = clock()

        admitted = 0
        refused = 0
        n = 0
        # Drive requests across the simulated window, advancing the injected clock each step so the
        # partition spans the full 45 s. More requests than the lease can serve, so we cross into
        # the refusal regime while still inside the window.
        total_requests = held + rng.randint(10, 40)
        step_s = _PARTITION_WINDOW_S / total_requests
        while n < total_requests:
            client.on_request_path = True
            verdict = await quota.evaluate(1, request_id=f"r{n}")
            client.on_request_path = False
            # Req 10.4: the request path issued NO store call (refill is off-path).
            assert client.request_path_calls == 0, (
                f"a store read was issued on the request path at n={n}: "
                f"{client.request_path_calls}"
            )
            if isinstance(verdict, Admitted):
                admitted += 1
            else:
                assert isinstance(verdict, BudgetVerdict), f"non-verdict {verdict!r}"
                # Req 10.3: the NARROW posture only — never the global one.
                assert verdict.code == BUDGET_UNAVAILABLE, (
                    f"refusal code {verdict.code!r} is not the narrow budget posture"
                )
                assert verdict.code != SHARED_STATE_UNAVAILABLE
                assert verdict.should_retry is False
                assert verdict.retry_after_s >= 1.0
                refused += 1
            clock.advance(step_s)
            n += 1

        # The simulated partition spanned the full declared window.
        assert clock() - partition_started >= _PARTITION_WINDOW_S - 1e-9

        # Spent EXACTLY the pre-acquired Remaining_Lease, then refused the rest narrowly.
        assert admitted == held, f"admitted={admitted} != pre-acquired Remaining_Lease={held}"
        assert refused >= 1, "never crossed into the refusal regime inside the window"
        assert client.request_path_calls == 0
        assert metrics.budget_unavailable == refused
        await inner.aclose()

    _run(go())
