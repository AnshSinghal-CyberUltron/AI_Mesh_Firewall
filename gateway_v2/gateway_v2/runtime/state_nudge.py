"""Pub/sub nudges, without redis-py's `listen()`.

A nudge is a LATENCY hint, never the feed. Propagation is already bounded by the periodic
O(1) round; this only shortens how long a change goes unnoticed inside that bound. Nothing here
may ever be load-bearing for correctness, because pub/sub is lossy by construction.

What it does carry, which RC2's listener discarded, is `<kind>:<feed_seq>`. A worker whose cursor
is already at or past that position can skip the round entirely, so a nudge storm costs nothing.

R2-19 / D2 — why this is not a `listen()` loop
----------------------------------------------
redis-py's `listen()` re-reads its connection in a loop that never yields while the connection
answers "no message" immediately. On a dead socket — a keepalive `ETIMEDOUT` reads as "no
message" — it spun the event loop and grew memory until the kernel killed every worker. That was
measured live and fixed; the fix is this listener, and it must stay.

A connection is declared DEAD on any of three signatures:

* **error** — any transport or store fault.
* **no_wait** — `get_message(timeout=poll)` returned "no message" WITHOUT waiting, three times
  running. A healthy connection with nothing to say waits for the timeout. This is the spin
  signature, and it is detected by elapsed time rather than by trusting an exception, so it holds
  whatever the client library does.
* **silent** — nothing at all for 2 x ping, a PING's own pong included. A PING goes out after one
  ping interval of silence, so a live connection is never silent that long.

A dead connection is closed and re-subscribed after equal-jitter exponential backoff. After every
subscription the caller's nudge runs ONCE unprompted, to catch up on whatever was published while
this listener was away — and if that catch-up fails it is retried on every wakeup, so recovery
never waits for the periodic round.

At most `drain_max` messages are taken per wakeup and they coalesce into ONE nudge: a nudge
re-reads state by cursor, so nothing needs queueing. Both the idle path and the message path
yield to the loop, so neither silence nor a flood can monopolise it.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gateway_v2.domain.state import StateKind

Positions = Mapping[StateKind, int]
Nudge = Callable[[Positions], Awaitable[bool]]
"""Called with kind -> highest announced position. Returns True when it was handled."""

FAST_NONE_LIMIT = 3
"""Consecutive immediate "no message" answers that mean the connection is dead, not quiet."""

PING_PAYLOAD = b"amf-live"


@dataclass(frozen=True, slots=True)
class PushKnobs:
    ping_s: float = 5.0
    first_backoff_s: float = 0.1
    cap_backoff_s: float = 2.0
    drain_max: int = 64
    close_timeout_s: float = 1.0

    @property
    def poll_s(self) -> float:
        return self.ping_s / 4


DEFAULT_KNOBS = PushKnobs()


@dataclass(frozen=True, slots=True)
class ListenerCounters:
    """Plain counters. GW14d wires these into a metric family; nothing here depends on one."""

    subscribed: int = 0
    dead: int = 0
    nudges: int = 0
    nudge_failures: int = 0
    messages: int = 0
    pings: int = 0

    def plus(self, **fields: int) -> ListenerCounters:
        current = {
            "subscribed": self.subscribed,
            "dead": self.dead,
            "nudges": self.nudges,
            "nudge_failures": self.nudge_failures,
            "messages": self.messages,
            "pings": self.pings,
        }
        for name, delta in fields.items():
            current[name] += delta
        return ListenerCounters(**current)


def parse_nudge(payload: object) -> tuple[StateKind, int] | None:
    """`<kind>:<feed_seq>`. Anything else is ignored: a nudge is never trusted for correctness."""
    if isinstance(payload, bytes):
        text = payload.decode("utf-8", "replace")
    elif isinstance(payload, str):
        text = payload
    else:
        return None
    kind_name, _, position = text.partition(":")
    try:
        kind = StateKind(kind_name)
    except ValueError:
        return None
    try:
        return kind, int(position) if position else 0
    except ValueError:
        return kind, 0


class NudgeListener:
    """Subscribes to the updates channel and calls back with coalesced positions."""

    def __init__(
        self,
        client: Any,
        channel: str,
        *,
        knobs: PushKnobs = DEFAULT_KNOBS,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_dead: Callable[[str], None] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._client = client
        self._channel = channel
        self._knobs = knobs
        self._clock = clock
        self._sleep = sleep
        self._on_dead = on_dead
        self._rng = random.Random() if rng is None else rng
        self.counters = ListenerCounters()
        self.subscribed = False

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        return asyncio.get_running_loop().time()

    def backoff(self, attempt: int) -> float:
        """Equal jitter: uniform in [d/2, d], d = min(cap, first * 2 ** (attempt - 1))."""
        # 2.0 rather than 2: `int ** int` with a non-literal exponent is Any to mypy.
        doubling = 2.0 ** min(max(attempt - 1, 0), 30)
        step = min(self._knobs.cap_backoff_s, self._knobs.first_backoff_s * doubling)
        return step / 2 + self._rng.uniform(0, step / 2)

    async def run(self, nudge: Nudge, *, max_cycles: int | None = None) -> None:
        """Subscribe, deliver, recover. Loops forever unless `max_cycles` bounds it (tests)."""
        attempt = 0
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            pubsub = self._client.pubsub()
            lived_from: float | None = None
            try:
                await pubsub.subscribe(self._channel)
                lived_from = self._now()
                self.subscribed = True
                self.counters = self.counters.plus(subscribed=1)
                # Catch up on whatever was published while we were away; it is lost otherwise.
                owed = not await self._nudge(nudge, {})
                await self._pump(pubsub, nudge, owed)
            except Exception as exc:  # noqa: BLE001 - every transport fault is a dead connection
                self._dead("error", exc)
            finally:
                self.subscribed = False
                await self._close(pubsub)
            lived = 0.0 if lived_from is None else self._now() - lived_from
            # A connection that lived a while starts its backoff over; a flapping store does not.
            attempt = 1 if lived >= self._knobs.ping_s else attempt + 1
            await self._sleep(self.backoff(attempt))

    async def _pump(self, pubsub: Any, nudge: Nudge, owed: bool) -> None:
        """Deliver until the connection is dead (returns) or faults (raises)."""
        last_rx = pinged = self._now()
        fast = 0
        while True:
            started = self._now()
            message = await pubsub.get_message(timeout=self._knobs.poll_s)
            now = self._now()
            if message is None:
                if owed:
                    owed = not await self._nudge(nudge, {})
                if now - started < self._knobs.poll_s / 2:
                    fast += 1
                    await asyncio.sleep(0)  # never starve the loop, whatever the client does
                    if fast >= FAST_NONE_LIMIT:
                        self._dead("no_wait")
                        return
                else:
                    fast = 0
                if now - last_rx >= 2 * self._knobs.ping_s:
                    self._dead("silent")
                    return
                if now - last_rx >= self._knobs.ping_s and now - pinged >= self._knobs.ping_s:
                    await pubsub.ping(PING_PAYLOAD)
                    self.counters = self.counters.plus(pings=1)
                    pinged = now
                continue
            fast = 0
            positions = self._collect(message, {})
            for _ in range(max(self._knobs.drain_max - 1, 0)):
                more = await pubsub.get_message(timeout=0)
                if more is None:
                    break
                positions = self._collect(more, positions)
            if positions or owed:
                owed = not await self._nudge(nudge, positions)
            last_rx = self._now()  # our own processing is not silence
            await asyncio.sleep(0)  # nor may a flood monopolise the loop

    def _collect(
        self,
        message: Mapping[str, object],
        positions: dict[StateKind, int],
    ) -> dict[StateKind, int]:
        """Coalesce: keep the HIGHEST announced position per kind."""
        self.counters = self.counters.plus(messages=1)
        if message.get("type") != "message":
            return positions
        parsed = parse_nudge(message.get("data"))
        if parsed is None:
            return positions
        kind, position = parsed
        if position > positions.get(kind, -1):
            positions[kind] = position
        return positions

    async def _nudge(self, nudge: Nudge, positions: Positions) -> bool:
        try:
            handled = await nudge(positions)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the caller counts its own store errors
            self.counters = self.counters.plus(nudges=1, nudge_failures=1)
            return False
        self.counters = self.counters.plus(
            nudges=1, nudge_failures=0 if handled else 1,
        )
        return handled

    def _dead(self, reason: str, exc: BaseException | None = None) -> None:
        self.counters = self.counters.plus(dead=1)
        if self._on_dead is not None:
            detail = reason if exc is None else f"{reason}: {type(exc).__name__}: {exc}"
            self._on_dead(detail[:300])

    async def _close(self, pubsub: Any) -> None:
        try:
            await asyncio.wait_for(pubsub.aclose(), self._knobs.close_timeout_s)
        except Exception:  # noqa: BLE001 - closing a dead connection is allowed to fail
            pass
