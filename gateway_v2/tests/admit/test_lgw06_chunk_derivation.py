"""Property 9 — Lease_Chunk and Low_Watermark are contract-derived (R2-09 / GW06), task 6.3 / 6.4.

# Feature: budget-lease, Property 9
# Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5

For any :class:`~gateway_v2.runtime.resources.ResourceContract`, the Lease_Chunk
(:func:`~gateway_v2.admit.quota.derive_lease_chunk`) and the Low_Watermark
(:func:`~gateway_v2.admit.quota.derive_low_watermark`) are **pure functions of the contract's
measured inputs only** — ``q_safe`` and the contract fields — and the watermark is a function of
the chunk alone. An unset / non-positive ``q_safe`` refuses to start (:class:`CapacityUnset`), and
an uncomputable or sub-unit chunk refuses with :class:`CapacityUnavailable`; the module holds no
capacity literal (the existing ``tests/gates/test_lgw19_admit_capacity_literals.py`` asserts that).

House idiom: a seeded ``random.Random`` loop over >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. These functions are pure and synchronous, so no event loop.
"""

from __future__ import annotations

import math
import random

from gateway_v2.admit.quota import derive_lease_chunk, derive_low_watermark
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_ITERATIONS = 10_000
_WATERMARK_FRACTION = 0.25  # mirrors quota._WATERMARK_FRACTION (test-local copy, not imported)


def _contract(rng: random.Random) -> ResourceContract:
    """Build a random but serviceable ResourceContract from random hardware signals + SLO.

    Ranges are chosen so ``from_signals`` + ``queue_depth`` are computable (the contract refuses to
    start on a box too small to serve one request): ``cpu_quota * utilization_cap >= 1`` so at least
    one worker is detected, and ``memory_limit`` holds several ``per_worker_rss`` slots. The
    derivation's refuse-to-start paths (unset ``q_safe``) are exercised with the dedicated case
    below.
    """
    utilization_cap = rng.uniform(0.25, 0.95)
    # Ensure cpu-detected workers >= 1: cpu_quota * utilization_cap >= 1 (plus a margin).
    cpu_quota = rng.uniform(1.0 / utilization_cap + 1.0, 32.0)
    per_worker_rss = rng.randint(128, 512) * 1024 * 1024
    # Ensure ram-detected workers >= 1: memory_limit * utilization_cap / rss >= 1 (plus a margin),
    # so the RSS multiplier must clear 1/utilization_cap.
    rss_multiplier = rng.randint(int(1.0 / utilization_cap) + 2, 24)
    memory_limit = per_worker_rss * rss_multiplier
    fd_limit = rng.randint(256, 65_536)
    signals = HardwareSignals(
        cpu_quota=cpu_quota,
        memory_limit=memory_limit,
        fd_limit=fd_limit,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=rng.uniform(5.0, 200.0),
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
    )


def test_property9_chunk_is_pure_function_of_contract_and_qsafe() -> None:
    """``derive_lease_chunk`` is deterministic in ``(contract, q_safe)`` and matches the formula."""
    seed = 0x06_09
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        contract = _contract(rng)
        q_safe = rng.uniform(0.1, 5_000.0)

        chunk = derive_lease_chunk(contract, q_safe=q_safe)
        # Purity: calling again with the same inputs yields the identical chunk (no hidden state).
        again = derive_lease_chunk(contract, q_safe=q_safe)
        assert chunk == again, f"seed={seed:#x} iter={i} derive_lease_chunk not pure"

        # It equals the documented contract-derived formula exactly (no literal size anywhere).
        raw = math.ceil(q_safe * (contract.target_p99_ms / 1000.0))
        expected = min(raw, contract.queue_depth(q_safe))
        assert chunk == expected, (
            f"seed={seed:#x} iter={i} chunk={chunk} != min(raw={raw}, "
            f"queue_depth={contract.queue_depth(q_safe)})"
        )
        assert chunk >= 1, f"seed={seed:#x} iter={i} a serviceable contract gave chunk<1"


def test_property9_chunk_bounded_by_contract_in_flight_depth() -> None:
    """The chunk never exceeds the contract's declared in-flight depth (``queue_depth(q_safe)``)."""
    seed = 0x06_09_02
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        contract = _contract(rng)
        q_safe = rng.uniform(0.1, 5_000.0)
        chunk = derive_lease_chunk(contract, q_safe=q_safe)
        bound = contract.queue_depth(q_safe)
        assert chunk <= bound, (
            f"seed={seed:#x} iter={i} chunk={chunk} exceeds in-flight bound={bound}"
        )


def test_property9_unset_or_nonpositive_qsafe_refuses_to_start() -> None:
    """An unset / non-positive ``q_safe`` raises ``CapacityUnset`` — never guesses a chunk."""
    seed = 0x06_09_03
    rng = random.Random(seed)
    for i in range(2_000):
        contract = _contract(rng)
        for bad in (None, 0.0, -rng.uniform(0.01, 1_000.0)):
            try:
                derive_lease_chunk(contract, q_safe=bad)
            except CapacityUnset:
                continue
            msg = f"seed={seed:#x} iter={i} q_safe={bad!r} did not refuse to start"
            raise AssertionError(msg)


# --------------------------------------------------------------------------- #
# Task 6.4 (watermark half) — derive_low_watermark is a function of the chunk only
# --------------------------------------------------------------------------- #


def test_watermark_is_function_of_chunk_only() -> None:
    """``derive_low_watermark`` depends on the chunk alone and lies in ``[0, chunk)``."""
    seed = 0x06_09_04
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        chunk = rng.randint(1, 100_000)
        mark = derive_low_watermark(chunk)
        # Determinism in the chunk only: same chunk => same watermark (no other input exists).
        assert mark == derive_low_watermark(chunk), f"seed={seed:#x} iter={i} not pure in chunk"
        assert 0 <= mark < chunk, (
            f"seed={seed:#x} iter={i} watermark={mark} not strictly below chunk={chunk}"
        )
        expected = min(math.floor(_WATERMARK_FRACTION * chunk), chunk - 1)
        assert mark == expected, (
            f"seed={seed:#x} iter={i} watermark={mark} != expected={expected} for chunk={chunk}"
        )


def test_watermark_sub_unit_chunk_refuses() -> None:
    """A sub-unit chunk has no valid watermark and refuses (fail-closed)."""
    for bad in (0, -1, -1000):
        try:
            derive_low_watermark(bad)
        except CapacityUnavailable:
            continue
        msg = f"derive_low_watermark({bad}) did not refuse"
        raise AssertionError(msg)


def test_watermark_examples() -> None:
    """Concrete boundary examples pin the exact watermark values."""
    assert derive_low_watermark(1) == 0  # floor(0.25) = 0
    assert derive_low_watermark(4) == 1  # floor(1.0) = 1
    assert derive_low_watermark(8) == 2  # floor(2.0) = 2
    assert derive_low_watermark(100) == 25  # floor(25.0) = 25
