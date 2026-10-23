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


# --------------------------------------------------------------------------- #
# Task 23.1 — BoundedQueue.remove: slot-exact release keeps the deques aligned
# Feature: v2-enforcement-leaks (Group D), Property 10
# Validates: Requirements 2.11
# --------------------------------------------------------------------------- #


def test_remove_present_returns_true_and_drops_depth() -> None:
    """Removing a present item returns True, drops depth by one and keeps ages aligned."""
    box = {"t": 100.0}
    queue = BoundedQueue(4, clock=_clock(box))  # type: ignore[arg-type]
    queue.offer("a", now=100.0)
    queue.offer("b", now=101.0)
    queue.offer("c", now=102.0)
    assert queue.depth == 3
    # Remove the MIDDLE item: the oldest ("a") must still drive oldest_age_s.
    assert queue.remove("b") is True
    assert queue.depth == 2
    box["t"] = 110.0
    # Oldest remaining is "a" (enqueued at 100); not "b"'s stale instant.
    assert queue.oldest_age_s() == 10.0
    # FIFO order of the rest is preserved: "a" then "c".
    assert queue.pop() == "a"
    assert queue.pop() == "c"
    assert queue.depth == 0


def test_remove_absent_returns_false_and_changes_nothing() -> None:
    """Removing an absent item returns False and mutates neither deque."""
    box = {"t": 100.0}
    queue = BoundedQueue(4, clock=_clock(box))  # type: ignore[arg-type]
    queue.offer("a", now=100.0)
    queue.offer("b", now=101.0)
    assert queue.remove("missing") is False
    assert queue.depth == 2
    box["t"] = 105.0
    assert queue.oldest_age_s() == 5.0
    assert queue.pop() == "a"
    assert queue.pop() == "b"


def test_remove_one_of_two_equal_items_leaves_one() -> None:
    """With two equal items (the content-hash collision case), remove drops only one."""
    box = {"t": 50.0}
    queue = BoundedQueue(4, clock=_clock(box))  # type: ignore[arg-type]
    queue.offer("dup", now=50.0)
    queue.offer("other", now=51.0)
    queue.offer("dup", now=52.0)
    assert queue.depth == 3
    # First remove drops the FIRST (oldest) "dup" at index 0.
    assert queue.remove("dup") is True
    assert queue.depth == 2
    box["t"] = 60.0
    # Oldest remaining is "other" (enqueued at 51), proving index-0 "dup" went, not the later one.
    assert queue.oldest_age_s() == 9.0
    # One "dup" still present; a second remove finds it, a third does not.
    assert queue.remove("dup") is True
    assert queue.depth == 1
    assert queue.remove("dup") is False
    assert queue.depth == 1
    assert queue.pop() == "other"


def test_remove_keeps_deques_aligned_under_random_interleaving() -> None:
    """A seeded offer/pop/remove sequence keeps _items and _enqueued_at index-aligned.

    After every step the two parallel deques have equal length, depth stays within the bound, and
    oldest_age_s reflects the oldest SURVIVING item's enqueue instant (never a removed item's).
    """
    seed = 0x23_01
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        max_depth = rng.randint(1, 16)
        box = {"t": 0.0}
        queue = BoundedQueue(max_depth, clock=_clock(box))  # type: ignore[arg-type]
        # A plain-list shadow model carrying (id, enqueued_at), kept in lock-step with the queue.
        shadow: list[tuple[int, float]] = []
        for _ in range(rng.randint(1, 60)):
            box["t"] += rng.uniform(0.1, 2.0)
            roll = rng.random()
            if roll < 0.5:
                # Offer a value drawn from a small pool so equal ids collide sometimes.
                value = rng.randint(0, 4)
                accepted = queue.offer(value, now=box["t"])
                if accepted:
                    shadow.append((value, box["t"]))
                else:
                    assert len(shadow) == max_depth, (
                        f"seed={seed:#x} iter={i} refused while shadow not full"
                    )
            elif roll < 0.75 and shadow:
                popped = queue.pop()
                expected = shadow.pop(0)
                assert popped == expected[0], (
                    f"seed={seed:#x} iter={i} pop {popped!r} != shadow {expected[0]!r}"
                )
            else:
                target = rng.randint(0, 4)
                removed = queue.remove(target)
                index = next(
                    (j for j, (v, _) in enumerate(shadow) if v == target), None
                )
                assert removed is (index is not None), (
                    f"seed={seed:#x} iter={i} remove({target}) returned {removed}"
                )
                if index is not None:
                    shadow.pop(index)
            # The two deques must stay index-aligned at every step.
            assert len(queue._items) == len(queue._enqueued_at), (
                f"seed={seed:#x} iter={i} deques misaligned: "
                f"{len(queue._items)} items vs {len(queue._enqueued_at)} ages"
            )
            assert queue.depth == len(shadow), (
                f"seed={seed:#x} iter={i} depth {queue.depth} != shadow {len(shadow)}"
            )
            assert queue.depth <= max_depth, (
                f"seed={seed:#x} iter={i} depth {queue.depth} > max {max_depth}"
            )
            # oldest_age_s reflects the oldest surviving item's enqueue instant.
            if shadow:
                assert queue.oldest_age_s(now=box["t"]) == box["t"] - shadow[0][1], (
                    f"seed={seed:#x} iter={i} oldest age misaligned after remove"
                )
            else:
                assert queue.oldest_age_s(now=box["t"]) == 0.0, (
                    f"seed={seed:#x} iter={i} non-empty age on empty queue"
                )
