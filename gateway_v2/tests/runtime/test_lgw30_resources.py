"""LGW30 task 2.2 — unit tests for the GW30 vector capacity derivations + fail-closed paths.

# Validates: Requirements 6.5, 11.2, 11.6

GW30 adds three vector-store bounds to :class:`~gateway_v2.runtime.resources.ResourceContract`
via :class:`~gateway_v2.runtime.resources_vector.VectorBoundsMixin`:
``vector_operation_timeout_s`` (``p99_s * _VECTOR_OP_TIMEOUT_MULT``), ``vector_max_result_count``
(``connection_budget() * _VECTOR_MAX_RESULT_COUNT_MULT`` clamped into
``[1, _VECTOR_MAX_RESULT_COUNT]``) and ``vector_max_result_bytes``
(``floor(per_worker_rss * _VECTOR_MAX_RESULT_BYTES_SHARE)`` clamped into
``[1, _VECTOR_MAX_RESULT_BYTES]``).

``runtime/resources.py`` is the one module permitted a capacity literal, so these tests READ every
multiplier, share and ceiling from the module (``resources._VECTOR_*``) rather than restating a
magic number — "no literal bound asserted outside the contract": the clamp ceilings are the
contract's own ``_VECTOR_MAX_RESULT_COUNT`` / ``_VECTOR_MAX_RESULT_BYTES``, never a bare ``10_000``
or ``10_485_760`` in this file.

The fail-closed stance matches the shipped contract: a non-positive derived bound raises
:class:`CapacityUnavailable`; an unset supplying signal (a non-finite ``target_p99_ms``, the
``None``-signal stance ``_guard_pool`` takes) raises :class:`CapacityUnset`.

Supporting unit tests (the design's Testing Strategy), not property tests: a handful of concrete
contracts. The derivations are pure and synchronous, so no event loop and no ``hypothesis``.
"""

from __future__ import annotations

import math

import pytest

import gateway_v2.runtime.resources as resources
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_MS_PER_S = resources._MS_PER_S

# Bounds read from the one module permitted capacity literals — never restated here.
_OP_TIMEOUT_MULT = resources._VECTOR_OP_TIMEOUT_MULT
_COUNT_MULT = resources._VECTOR_MAX_RESULT_COUNT_MULT
_COUNT_CEILING = resources._VECTOR_MAX_RESULT_COUNT
_BYTES_SHARE = resources._VECTOR_MAX_RESULT_BYTES_SHARE
_BYTES_CEILING = resources._VECTOR_MAX_RESULT_BYTES


def _signals(
    *,
    cpu_quota: float = 4.0,
    memory_limit: int,
    fd_limit: int,
) -> HardwareSignals:
    return HardwareSignals(
        cpu_quota=cpu_quota,
        memory_limit=memory_limit,
        fd_limit=fd_limit,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )


def _contract(
    *,
    target_p99_ms: float = 20.0,
    utilization_cap: float = 0.75,
    per_worker_rss: int = 100 * 1024 * 1024,
    fd_limit: int = 4096,
    cpu_quota: float = 4.0,
) -> ResourceContract:
    """A serviceable contract via ``from_signals`` (same shape as the LGW12/LGW06/LGW19 tests).

    ``memory_limit`` is sized generously off ``per_worker_rss`` so the RAM worker path never binds
    the derivation under test; the caller tunes ``fd_limit`` / ``per_worker_rss`` to steer the
    count / bytes derivation into or out of its clamp range.
    """
    signals = _signals(
        cpu_quota=cpu_quota,
        memory_limit=per_worker_rss * 32,
        fd_limit=fd_limit,
    )
    return from_signals(
        signals,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
    )


def _degenerate(*, target_p99_ms: float, per_worker_rss: int, fd_limit: int) -> ResourceContract:
    """Construct a contract directly, bypassing ``from_signals``' refuse-to-start checks.

    ``from_signals`` rejects a non-positive / non-finite ``target_p99_ms`` and a sub-worker
    ``fd_limit`` before a bound can be read, so the only way to reach a fail-closed guard inside an
    accessor is to build the contract directly — the same technique the shipped LGW12 resources
    test uses for its non-positive-period case.
    """
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 32,
        fd_limit=fd_limit,
        guard_capacity=None,
        target_p99_ms=target_p99_ms,
        utilization_cap=0.75,
        per_worker_rss=per_worker_rss,
        worker_override=None,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )


# --------------------------------------------------------------------------------------------
# vector_operation_timeout_s
# --------------------------------------------------------------------------------------------


def test_operation_timeout_equals_p99_times_mult() -> None:
    """``vector_operation_timeout_s() == p99_s * _VECTOR_OP_TIMEOUT_MULT`` (R6.5)."""
    target_p99_ms = 20.0
    contract = _contract(target_p99_ms=target_p99_ms)
    p99_s = target_p99_ms / _MS_PER_S
    assert contract.vector_operation_timeout_s() == p99_s * _OP_TIMEOUT_MULT


def test_operation_timeout_tracks_p99_across_contracts() -> None:
    """The timeout is a pure function of ``target_p99_ms`` — a few concrete SLOs agree (R6.5)."""
    for target_p99_ms in (1.0, 12.5, 37.0, 250.0, 500.0):
        contract = _contract(target_p99_ms=target_p99_ms)
        p99_s = target_p99_ms / _MS_PER_S
        assert contract.vector_operation_timeout_s() == p99_s * _OP_TIMEOUT_MULT


def test_operation_timeout_non_positive_derivation_fails_closed() -> None:
    """A zero p99 period yields a non-positive bound → :class:`CapacityUnavailable` (R6.5, R11.6).

    ``from_signals`` refuses to start on a non-positive ``target_p99_ms``, so the guard is reached
    only through a directly-built contract with a zero period.
    """
    degenerate = _degenerate(
        target_p99_ms=0.0,
        per_worker_rss=100 * 1024 * 1024,
        fd_limit=4096,
    )
    with pytest.raises(CapacityUnavailable):
        degenerate.vector_operation_timeout_s()


def test_operation_timeout_unset_signal_raises_capacity_unset() -> None:
    """A non-finite ``target_p99_ms`` is an unset SLO signal → :class:`CapacityUnset` (R11.6).

    The accessor refuses to hand back a guessed bound when the request-path SLO it derives from is
    unset, the same ``None``-signal stance ``_guard_pool`` takes (NaN and both infinities).
    """
    for p99 in (math.nan, math.inf, -math.inf):
        degenerate = _degenerate(
            target_p99_ms=p99,
            per_worker_rss=100 * 1024 * 1024,
            fd_limit=4096,
        )
        with pytest.raises(CapacityUnset):
            degenerate.vector_operation_timeout_s()


# --------------------------------------------------------------------------------------------
# vector_max_result_count — clamp into [1, _VECTOR_MAX_RESULT_COUNT]
# --------------------------------------------------------------------------------------------


def test_max_result_count_clamps_to_ceiling() -> None:
    """A large connection budget clamps DOWN to ``_VECTOR_MAX_RESULT_COUNT`` (R11.2).

    ``fd_limit`` is sized so ``connection_budget()`` far exceeds the ceiling; the accessor returns
    exactly the module ceiling, never the larger raw budget.
    """
    contract = _contract(fd_limit=20_000)
    assert contract.connection_budget() * _COUNT_MULT > _COUNT_CEILING
    assert contract.vector_max_result_count() == _COUNT_CEILING


def test_max_result_count_in_range_is_the_raw_derivation() -> None:
    """A mid-range budget passes through un-clamped, inside ``[1, _VECTOR_MAX_RESULT_COUNT]``."""
    contract = _contract(fd_limit=512)
    derived = contract.connection_budget() * _COUNT_MULT
    assert 1 <= derived <= _COUNT_CEILING
    got = contract.vector_max_result_count()
    assert got == derived
    assert 1 <= got <= _COUNT_CEILING


def test_max_result_count_below_one_fails_closed() -> None:
    """A budget below one result fails closed with :class:`CapacityUnavailable` (R11.6).

    ``connection_budget() = floor(max(fd_limit - workers, 0) * utilization_cap)``; an ``fd_limit``
    at or below the worker count drives the budget to zero, below the ``>= 1`` floor.
    """
    degenerate = _degenerate(
        target_p99_ms=20.0,
        per_worker_rss=100 * 1024 * 1024,
        fd_limit=4,
    )
    assert degenerate.connection_budget() * _COUNT_MULT < 1
    with pytest.raises(CapacityUnavailable):
        degenerate.vector_max_result_count()


# --------------------------------------------------------------------------------------------
# vector_max_result_bytes — clamp into [1, _VECTOR_MAX_RESULT_BYTES]
# --------------------------------------------------------------------------------------------


def test_max_result_bytes_clamps_to_ceiling() -> None:
    """A large per-worker RSS clamps DOWN to ``_VECTOR_MAX_RESULT_BYTES`` (R11.2).

    One GiB of per-request memory budgets a byte share above the 10 MiB ceiling; the accessor
    returns exactly the module ceiling, never the larger raw share.
    """
    contract = _contract(per_worker_rss=1024 * 1024 * 1024)
    assert math.floor(contract.per_worker_rss * _BYTES_SHARE) > _BYTES_CEILING
    assert contract.vector_max_result_bytes() == _BYTES_CEILING


def test_max_result_bytes_in_range_is_the_raw_derivation() -> None:
    """A mid-range RSS passes through un-clamped, inside ``[1, _VECTOR_MAX_RESULT_BYTES]``."""
    per_worker_rss = 100 * 1024 * 1024
    contract = _contract(per_worker_rss=per_worker_rss)
    derived = math.floor(per_worker_rss * _BYTES_SHARE)
    assert 1 <= derived <= _BYTES_CEILING
    got = contract.vector_max_result_bytes()
    assert got == derived
    assert 1 <= got <= _BYTES_CEILING


def test_max_result_bytes_below_one_fails_closed() -> None:
    """A per-worker RSS whose byte share floors below one fails closed (R11.6).

    ``floor(per_worker_rss * _VECTOR_MAX_RESULT_BYTES_SHARE) < 1`` when the RSS is tiny; the
    accessor raises :class:`CapacityUnavailable` rather than returning a zero-byte ceiling.
    """
    # Largest RSS whose byte share still floors below one: ``rss < 1 / share``.
    tiny_rss = math.ceil(1.0 / _BYTES_SHARE) - 1
    assert math.floor(tiny_rss * _BYTES_SHARE) < 1
    degenerate = _degenerate(
        target_p99_ms=20.0,
        per_worker_rss=tiny_rss,
        fd_limit=4096,
    )
    with pytest.raises(CapacityUnavailable):
        degenerate.vector_max_result_bytes()
