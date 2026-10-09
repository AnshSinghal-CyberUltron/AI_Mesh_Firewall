"""Bounded-queue depth/age tests (R2-07 / GW19), tasks 8.1 / 8.2.

Covers :class:`gateway_v2.admit.queues.BoundedQueue` (and, where it shares the depth-bound
invariant, :class:`gateway_v2.admit.queues.BackpressureBuffer`):

* **Property 4 — queue-depth bound** (task 8.2): for every bounded queue at every step of any
  enqueue/dequeue sequence the current depth never exceeds the declared max, and an over-max
  enqueue sheds at the door (``offer`` returns ``False`` and enqueues nothing).
* **Unit tests** (task 8.1): depth + oldest-item age are read against the injected clock
  (Req 7.3 / 7.4), an empty queue reads ``(0, 0.0)``, FIFO order holds.

House idiom: seeded ``random.Random`` loop of >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. Time is driven by an injected fake clock so age readings are
deterministic. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

from gateway_v2.admit.queues import BackpressureBuffer, BoundedQueue

_ITERATIONS = 10_000


def _clock(box: dict[str, float]) -> object:
    return lambda: box["t"]


# --------------------------------------------------------------------------- #
# Task 8.2 — Property 4: queue-depth bound
# Feature: admission-control, Property 4
# Validates: Requirements 7.1, 7.2, 10.1, 10.2
# --------------------------------------------------------------------------- #


def test_property4_depth_never_exceeds_declared_max() -> None:
    """For every random enqueue/dequeue step, depth <= max and an over-max enqueue sheds."""
    seed = 0x19_04
    rng = random.Random(seed)
    box = {"t": 0.0}
    for i in range(_ITERATIONS):
        max_depth = rng.randint(1, 32)
        queue = BoundedQueue(max_depth, clock=_clock(box))  # type: ignore[arg-type]
        steps = rng.randint(1, 60)
        for _ in range(steps):
            box["t"] += rng.uniform(0.0, 1.0)
            if rng.random() < 0.6:
                accepted = queue.offer(object(), now=box["t"])
                # Core invariant: an enqueue is accepted iff the queue was below the bound.
                if queue.depth > max_depth:
                    raise AssertionError(
                        f"seed={seed:#x} iter={i} depth {queue.depth} exceeded max {max_depth}"
                    )
                if not accepted:
                    assert queue.is_full(), (
                        f"seed={seed:#x} iter={i} offer refused while not full"
                    )
            elif queue.depth > 0:
                queue.pop()
            # Invariant checked at EVERY step (Property 4).
            assert queue.depth <= max_depth, (
                f"seed={seed:#x} iter={i} depth {queue.depth} > max {max_depth}"
            )
            assert queue.depth >= 0, f"seed={seed:#x} iter={i} negative depth"


def test_property4_overfull_enqueue_sheds_at_the_door() -> None:
    """Once at the declared max, every further offer sheds (returns False, enqueues nothing)."""
    seed = 0x19_04A
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        max_depth = rng.randint(1, 16)
        queue = BoundedQueue(max_depth)
        for _ in range(max_depth):
            assert queue.offer(object()) is True
        assert queue.is_full(), f"seed={seed:#x} iter={i} not full at max"
        # Any number of further offers must all shed at the door; depth stays pinned at max.
        for _ in range(rng.randint(1, 10)):
            assert queue.offer(object()) is False, (
                f"seed={seed:#x} iter={i} over-max enqueue was accepted"
            )
            assert queue.depth == max_depth, (
                f"seed={seed:#x} iter={i} depth drifted past max"
            )


def test_property4_backpressure_buffer_never_exceeds_credit() -> None:
    """A credit buffer never buffers past its byte credit across any offer/drain sequence."""
    seed = 0x19_04B
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        credit = rng.randint(1, 4096)
        buffer = BackpressureBuffer(credit)
        for _ in range(rng.randint(1, 40)):
            if rng.random() < 0.65:
                chunk = rng.randint(1, 2048)
                receipt = buffer.offer(chunk)
                assert buffer.buffered_bytes <= credit, (
                    f"seed={seed:#x} iter={i} buffered {buffer.buffered_bytes} > credit {credit}"
                )
                if not receipt.accepted:
                    # Refused only because it would exceed the credit (backpressure engaged).
                    assert buffer.buffered_bytes + chunk > credit, (
                        f"seed={seed:#x} iter={i} refused a chunk that fit"
                    )
            else:
                buffer.drain(rng.randint(1, 2048))
            assert 0 <= buffer.buffered_bytes <= credit, (
                f"seed={seed:#x} iter={i} buffered out of range"
            )


# --------------------------------------------------------------------------- #
# Task 8.1 — unit tests: depth + oldest-item age (Req 7.3 / 7.4)
# --------------------------------------------------------------------------- #


def test_empty_queue_reads_zero_depth_and_zero_age() -> None:
    """An empty queue reports depth 0 and age 0.0 (a real zero, never absent)."""
    box = {"t": 10.0}
    queue = BoundedQueue(4, clock=_clock(box))  # type: ignore[arg-type]
    assert queue.depth == 0
    assert queue.oldest_age_s() == 0.0


def test_oldest_age_read_against_injected_clock() -> None:
    """Oldest-item age is (now - enqueued_at) read against the injected clock (Req 7.4)."""
    box = {"t": 100.0}
    queue = BoundedQueue(4, clock=_clock(box))  # type: ignore[arg-type]
    queue.offer("first")           # enqueued at t=100
    box["t"] = 103.5
    queue.offer("second")          # enqueued at t=103.5
    # Oldest item is "first", enqueued at 100; age at now=103.5 is 3.5 s.
    assert queue.oldest_age_s() == 3.5
    box["t"] = 110.0
    assert queue.oldest_age_s() == 10.0
    # After popping the oldest, the next item's age is measured from ITS enqueue instant.
    assert queue.pop() == "first"
    assert queue.oldest_age_s() == 110.0 - 103.5


def test_depth_tracks_offer_and_pop() -> None:
    """Depth reflects enqueues and dequeues; FIFO order holds (Req 7.3)."""
    queue = BoundedQueue(3)
    assert queue.depth == 0
    queue.offer("a")
    queue.offer("b")
    assert queue.depth == 2
    assert queue.pop() == "a"
    assert queue.pop() == "b"
    assert queue.depth == 0


def test_max_depth_below_one_is_rejected() -> None:
    """A declared max below one is itself a refuse-to-serve (no zero-capacity queue)."""
    import pytest

    from gateway_v2.admit.queues import QueueFull

    with pytest.raises(QueueFull):
        BoundedQueue(0)
