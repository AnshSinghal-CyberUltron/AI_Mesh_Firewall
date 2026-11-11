# Feature: observability-contract, Property 1: No request-path raise on overflow
# Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 12.3, 12.4
"""Property 1 — ``CardinalityDirectory.observe()`` NEVER raises on the request path (R2-10 core).

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations, a counterexample
with its seed printed on failure.

For any sequence of per-tenant observations of any length against a directory of any derived
capacity, no ``observe()`` raises; every observation beyond capacity (or one that forces an internal
failure) increments the Overflow_Counter by EXACTLY one; the directory size never exceeds its
capacity; and an existing-key update never allocates a new slot. The generators include the
adversarial inputs the design names: duplicate keys, a key that forces an internal failure, and a
capacity of 1.
"""

from __future__ import annotations

import random

from gateway_v2.edge.cardinality import CardinalityDirectory

_SEED = 0x0B5E_0001
_ITERATIONS = 10_000


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


class _FailingKey(str):
    """A tenant key whose ``__hash__`` raises — forces an internal failure inside ``observe()``.

    The never-raise guard must catch ANY internal failure (not just capacity exhaustion) and route
    it to the Overflow_Counter (R2.2). A key that cannot be hashed exercises the ``except`` branch:
    the dict membership test / insert raises, and the guard must still return cleanly and count the
    drop. ``str`` subclass so the type signature is honoured.
    """

    def __hash__(self) -> int:  # noqa: D401 - deliberately raises
        raise RuntimeError("forced internal failure in observe()")


def test_property_1_no_request_path_raise_on_overflow() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        capacity = rng.randint(1, 12)
        # K <= capacity, honoured as the contract guarantees; irrelevant to observe() but kept sane.
        k = rng.randint(1, capacity)
        directory = CardinalityDirectory(_capacity=capacity, _k=k)

        n_ops = rng.randint(0, 60)
        # A small key universe so duplicates and over-capacity both occur frequently.
        universe = [f"t{j}" for j in range(rng.randint(1, capacity + 6))]

        distinct_keys: set[str] = set()
        expected_overflow = 0

        for _op in range(n_ops):
            roll = rng.random()
            magnitude = rng.randint(0, 1000)
            if roll < 0.08:
                # Adversarial: a key that forces an internal failure. Must count as overflow,
                # never raise, never allocate.
                key_obj: str = _FailingKey("boom")
                size_before = len(directory._slots)
                directory.observe(key_obj, magnitude)
                assert len(directory._slots) == size_before, _ctx(
                    _SEED, i, note="failing key allocated a slot", size_before=size_before
                )
                expected_overflow += 1
                continue

            key = rng.choice(universe)
            size_before = len(directory._slots)
            existed = key in distinct_keys

            directory.observe(key, magnitude)

            if existed:
                # Existing-key update: no new slot, cannot overflow (R2.6 / design invariant).
                assert len(directory._slots) == size_before, _ctx(
                    _SEED, i, note="existing-key update allocated", key=key
                )
            elif size_before >= capacity:
                # Absent key, directory full: size-check-before-insert routes to overflow by one.
                assert len(directory._slots) == size_before, _ctx(
                    _SEED, i, note="full directory grew", key=key
                )
                expected_overflow += 1
            else:
                # Absent key, free slot: allocate exactly one slot.
                distinct_keys.add(key)
                assert len(directory._slots) == size_before + 1, _ctx(
                    _SEED, i, note="free-slot insert did not grow by one", key=key
                )

            # The hard invariant on EVERY op: size never exceeds capacity.
            assert len(directory._slots) <= capacity, _ctx(
                _SEED, i, note="size exceeded capacity", size=len(directory._slots), cap=capacity
            )

        # Overflow_Counter equals the count of over-capacity + internally-failed observations,
        # exactly (R2.4 / R12.3), and is non-negative (a real zero before any overflow — R2.5).
        assert directory.overflow_total() == expected_overflow, _ctx(
            _SEED,
            i,
            note="overflow_total mismatch",
            got=directory.overflow_total(),
            want=expected_overflow,
        )
        assert directory.overflow_total() >= 0, _ctx(_SEED, i, note="negative overflow")
        assert len(directory._slots) <= capacity, _ctx(_SEED, i, note="final size over capacity")
