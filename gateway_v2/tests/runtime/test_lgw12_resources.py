"""LGW12 task 1.2 — unit tests for the GW12 stream-serving derivations (GW12 / sse-egress-pipeline).

# Validates: Requirements 9.1, 10.1

The GW12 design adds seven stream-serving bounds to :class:`~gateway_v2.runtime.resources.
ResourceContract` — ``inter_chunk_timeout_s``, ``idle_timeout_s``, ``write_timeout_s``,
``max_stream_duration_s``, ``max_cut_latency_s``, ``max_snapshot_age_s``, ``cancellation_bound_s``
— each a multiple of the request-path SLO (``target_p99_ms``) via the shared ``_stream_timeout_s``
helper. ``runtime/resources.py`` is the one module permitted a capacity literal, so these tests
read every multiplier from the module (``resources._*_MULT``) rather than restating a magic number:
the assertion is ``method() == p99_s * mult``, the floor is ``>= p99_s``, and a non-positive
derivation fails closed with :class:`CapacityUnavailable` (matching the shipped
``stream_buffer_bytes`` contract).

These are supporting unit tests (the design's Testing Strategy), not property tests: a handful of
concrete contracts plus a small seeded ``random.Random`` sweep over the serviceable SLO range. No
``hypothesis``; the derivations are pure and synchronous, so no event loop.
"""

from __future__ import annotations

import random

import gateway_v2.runtime.resources as resources
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals, snapshot

_MS_PER_S = resources._MS_PER_S

# (method-name, module multiplier) — the single source of truth for the seven GW12 bounds. Each
# multiplier is READ from the module, never restated, so a change to a literal cannot drift the
# test out of agreement with the contract it guards.
_STREAM_BOUNDS: tuple[tuple[str, float], ...] = (
    ("inter_chunk_timeout_s", resources._INTER_CHUNK_TIMEOUT_MULT),
    ("idle_timeout_s", resources._IDLE_TIMEOUT_MULT),
    ("write_timeout_s", resources._WRITE_TIMEOUT_MULT),
    ("max_stream_duration_s", resources._MAX_STREAM_DURATION_MULT),
    ("max_cut_latency_s", resources._MAX_CUT_LATENCY_MULT),
    ("max_snapshot_age_s", resources._MAX_SNAPSHOT_AGE_MULT),
    ("cancellation_bound_s", resources._CANCEL_BOUND_MULT),
)

# The snapshot keys GW12 adds alongside the shipped ``stream_buffer_bytes`` high-water source.
_SNAPSHOT_STREAM_KEYS: tuple[str, ...] = tuple(name for name, _ in _STREAM_BOUNDS)


def _contract(
    *,
    target_p99_ms: float = 20.0,
    utilization_cap: float = 0.75,
    per_worker_rss: int = 400 * 1024 * 1024,
) -> ResourceContract:
    """A serviceable contract fixture via ``from_signals`` (same shape as the LGW06/LGW19 tests).

    The signals clear ``from_signals``' refuse-to-start checks on both the CPU and RAM worker
    paths, so the contract is valid for any ``target_p99_ms`` the caller passes.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
    )


def test_each_bound_equals_p99_times_its_multiplier() -> None:
    """Every GW12 bound equals ``p99_s * mult`` for a typical contract (R9.1, R10.1)."""
    target_p99_ms = 20.0
    contract = _contract(target_p99_ms=target_p99_ms)
    p99_s = target_p99_ms / _MS_PER_S
    for name, mult in _STREAM_BOUNDS:
        got = getattr(contract, name)()
        assert got == p99_s * mult, f"{name}: expected p99_s*{mult}={p99_s * mult}, got {got}"


def test_bounds_track_p99_across_the_serviceable_slo_range() -> None:
    """The derivation is a pure function of ``target_p99_ms``: a seeded SLO sweep agrees (R9.1)."""
    seed = 0x12_01
    rng = random.Random(seed)
    for _ in range(256):
        target_p99_ms = rng.uniform(1.0, 500.0)
        contract = _contract(target_p99_ms=target_p99_ms)
        p99_s = target_p99_ms / _MS_PER_S
        for name, mult in _STREAM_BOUNDS:
            got = getattr(contract, name)()
            # Clamped up to one p99 period, but every shipped mult is >= 1.0 so the product wins.
            assert got == max(p99_s * mult, p99_s), (
                f"seed={seed:#x} p99_ms={target_p99_ms} {name}: "
                f"expected {max(p99_s * mult, p99_s)}, got {got}"
            )


def test_no_bound_falls_below_one_p99_period_floor() -> None:
    """Each bound is clamped up to a floor of one p99 period (never a sub-SLO timeout) (R9.1)."""
    seed = 0x12_02
    rng = random.Random(seed)
    for _ in range(256):
        target_p99_ms = rng.uniform(1.0, 500.0)
        contract = _contract(target_p99_ms=target_p99_ms)
        p99_s = target_p99_ms / _MS_PER_S
        for name, _mult in _STREAM_BOUNDS:
            got = getattr(contract, name)()
            assert got >= p99_s, (
                f"seed={seed:#x} p99_ms={target_p99_ms} {name}: "
                f"{got} fell below one p99 period {p99_s}"
            )


def test_floor_binds_when_the_multiplier_is_one() -> None:
    """A unit multiplier makes the floor and the product coincide — the clamp is exact, not above.

    ``max_snapshot_age_s`` ships at ``_MAX_SNAPSHOT_AGE_MULT == 1.0``, so its value is exactly one
    p99 period: the floor clamp is at its binding point (``max(p99_s * 1.0, p99_s) == p99_s``),
    which is what proves the clamp is a floor and not an additional inflation.
    """
    assert resources._MAX_SNAPSHOT_AGE_MULT == 1.0
    contract = _contract(target_p99_ms=37.0)
    p99_s = 37.0 / _MS_PER_S
    assert contract.max_snapshot_age_s() == p99_s


def test_non_positive_derivation_fails_closed() -> None:
    """A non-positive p99 period cannot produce a bound — the helper fails closed (R9.1, R10.1).

    ``from_signals`` refuses to start on a non-positive ``target_p99_ms``, so the only way to reach
    the ``_stream_timeout_s`` guard is to construct a contract directly with a zero period. Every
    GW12 bound then raises :class:`CapacityUnavailable` rather than returning a zero/negative
    timeout — the same fail-closed stance as the shipped ``stream_buffer_bytes``.
    """
    degenerate = ResourceContract(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        guard_capacity=None,
        target_p99_ms=0.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
        worker_override=None,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    for name, _mult in _STREAM_BOUNDS:
        try:
            getattr(degenerate, name)()
        except CapacityUnavailable:
            continue
        raise AssertionError(f"{name} did not fail closed on a non-positive p99 period")


def test_snapshot_surfaces_every_stream_bound_alongside_buffer_bytes() -> None:
    """``snapshot()`` exposes all seven GW12 bounds plus ``stream_buffer_bytes`` (R9.1, R10.1)."""
    contract = _contract()
    snap = snapshot(contract, logs=())

    assert "stream_buffer_bytes" in snap
    assert snap["stream_buffer_bytes"] == contract.stream_buffer_bytes(1)

    for name in _SNAPSHOT_STREAM_KEYS:
        assert name in snap, f"snapshot missing GW12 bound {name}"
        assert snap[name] == getattr(contract, name)(), (
            f"snapshot[{name}]={snap[name]!r} != contract.{name}()={getattr(contract, name)()!r}"
        )
