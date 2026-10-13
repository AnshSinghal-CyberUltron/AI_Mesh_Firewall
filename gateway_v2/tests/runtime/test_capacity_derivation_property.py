# Feature: observability-contract, Property 9: Directory capacity derives from the contract
# Validates: Requirements 2.7, 3.3, 4.2, 9.3, 15
"""Property 9 — the metrics capacity derivations are a pure function of the contract fields.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any ``ResourceContract``, ``metrics_directory_slots()`` / ``metrics_top_k()`` /
``metrics_window_s()`` / ``event_log_rate()`` are deterministic functions of the contract fields
(the same contract yields the same values on repeated calls); scaling a field scales the derivation
monotonically; ``metrics_top_k() <= metrics_directory_slots()`` always holds; and each fails closed
with ``CapacityUnavailable`` below its minimum. The capacity-literals AST gate (run in the gate
sweep) verifies the complementary "no literal outside resources.py" half of this property.
"""

from __future__ import annotations

import random

from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import (
    CapacityUnavailable,
    ResourceContract,
    from_signals,
)

_SEED = 0x0B5E_0009
_ITERATIONS = 10_000

_MIB = 1024 * 1024


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _contract(
    *,
    fd_limit: int,
    cpu_quota: float,
    memory_limit: int,
    target_p99_ms: float,
    utilization_cap: float,
    per_worker_rss: int,
    worker_override: int | None = None,
) -> ResourceContract:
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
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
        worker_override=worker_override,
    )


def test_property_9_capacity_derivations_are_deterministic_and_bounded() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        fd_limit = rng.randint(256, 8192)
        # cpu_quota * utilization_cap must round to >= 1 worker for the contract to construct, so
        # keep the product comfortably above one across the chosen utilization caps.
        cpu_quota = rng.choice([4.0, 8.0, 16.0])
        memory_limit = rng.randint(512, 2048) * _MIB * 8
        target_p99_ms = rng.choice([5.0, 10.0, 20.0, 50.0, 100.0])
        utilization_cap = rng.choice([0.5, 0.6, 0.75, 0.9])
        per_worker_rss = rng.randint(64, 256) * _MIB

        contract = _contract(
            fd_limit=fd_limit,
            cpu_quota=cpu_quota,
            memory_limit=memory_limit,
            target_p99_ms=target_p99_ms,
            utilization_cap=utilization_cap,
            per_worker_rss=per_worker_rss,
        )

        slots = contract.metrics_directory_slots()
        k = contract.metrics_top_k()
        window = contract.metrics_window_s()
        rate = contract.event_log_rate()

        # Determinism: same contract => same values (R9/R15 bounded-by-construction).
        assert contract.metrics_directory_slots() == slots, _ctx(_SEED, i, note="slots nondet")
        assert contract.metrics_top_k() == k, _ctx(_SEED, i, note="k nondet")
        assert contract.metrics_window_s() == window, _ctx(_SEED, i, note="window nondet")
        assert contract.event_log_rate() == rate, _ctx(_SEED, i, note="rate nondet")

        # Minimums hold (each fails closed below these; see the fail-closed sub-test).
        assert slots >= 1, _ctx(_SEED, i, note="slots below min", slots=slots)
        assert k >= 1, _ctx(_SEED, i, note="k below min", k=k)
        assert window > 0.0, _ctx(_SEED, i, note="window not positive", window=window)
        assert rate >= 1, _ctx(_SEED, i, note="rate below min", rate=rate)

        # K is a fixed fraction of slots and never exceeds it (R3.3).
        assert k <= slots, _ctx(_SEED, i, note="k > slots", k=k, slots=slots)

        # Window is a pure function of the SLO alone: scaling target_p99_ms scales the window.
        scaled = _contract(
            fd_limit=fd_limit,
            cpu_quota=cpu_quota,
            memory_limit=memory_limit,
            target_p99_ms=target_p99_ms * 2.0,
            utilization_cap=utilization_cap,
            per_worker_rss=per_worker_rss,
        )
        assert scaled.metrics_window_s() > window, _ctx(
            _SEED, i, note="window did not scale", base=window, scaled=scaled.metrics_window_s()
        )

        # Slots derive from the connection budget: a larger fd_limit never shrinks the directory.
        bigger_fds = _contract(
            fd_limit=fd_limit * 2,
            cpu_quota=cpu_quota,
            memory_limit=memory_limit,
            target_p99_ms=target_p99_ms,
            utilization_cap=utilization_cap,
            per_worker_rss=per_worker_rss,
        )
        assert bigger_fds.metrics_directory_slots() >= slots, _ctx(
            _SEED,
            i,
            note="slots shrank with more fds",
            base=slots,
            bigger=bigger_fds.metrics_directory_slots(),
        )


def test_property_9_fail_closed_below_minimum() -> None:
    """Each derivation fails closed with ``CapacityUnavailable`` below its minimum (R2.7).

    A contract whose connection budget rounds to zero slots cannot hold a single tenant, so
    ``metrics_directory_slots()`` (and the K that derives from it) must refuse rather than return 0.
    ``metrics_window_s()`` is positive whenever ``target_p99_ms`` is (via ``from_signals``),
    so its fail-closed branch is covered structurally by the guard; the slots/K path is the one a
    serviceable-but-tiny contract can actually drive below the floor.
    """
    # workers() consumes fds; with fd_limit barely above the worker count the connection budget
    # floors to 0 and metrics_directory_slots() must raise.
    tiny = _contract(
        fd_limit=2,
        cpu_quota=4.0,
        memory_limit=1024 * _MIB * 8,
        target_p99_ms=20.0,
        utilization_cap=0.5,
        per_worker_rss=64 * _MIB,
        worker_override=1,
    )
    raised = False
    try:
        tiny.metrics_directory_slots()
    except CapacityUnavailable:
        raised = True
    assert raised, "metrics_directory_slots() did not fail closed on a zero-slot budget"

    # K derives from slots, so it fails closed for the same contract.
    raised_k = False
    try:
        tiny.metrics_top_k()
    except CapacityUnavailable:
        raised_k = True
    assert raised_k, "metrics_top_k() did not fail closed when slots are below minimum"
