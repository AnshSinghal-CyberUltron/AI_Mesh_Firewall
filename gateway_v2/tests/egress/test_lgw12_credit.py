"""Property 2: Credit conservation (GW12 / sse-egress-pipeline), task 3.3.

This is the single property-based test for ``CreditFlowControl`` in
``gateway_v2.egress.backpressure``. It drives a seeded ``random.Random`` for
``_ITERATIONS`` (>= 10,000) rounds, with NO hypothesis, over random
``grant_on_consume`` / ``spend`` interleavings and asserts the credit-conservation
invariant holds with ZERO deviation at EVERY observation point.

The invariant (R5.7): ``outstanding + consumed == granted`` always. ``outstanding``
is DERIVED (``granted - consumed``) and never stored, so the equality is structural;
this test proves it empirically across a large randomized operation space and also
pins the three sub-behaviours the invariant leans on:

* ``grant_on_consume(N)`` grants EXACTLY ``N`` for ``N > 0`` and EXACTLY ``0`` for
  ``N <= 0`` (R5.2 grant-exactly-N, R5.3 grant-zero-when-idle), moving ``granted`` by
  exactly the amount it returns;
* ``outstanding()`` is NEVER negative -- ``spend`` clamps at the granted total (R5.1
  reads only consume outstanding credit, so it can never over-spend);
* ``may_read()`` is True iff ``outstanding() > 0`` AND ``buffered < high_water`` (R5.1
  gates upstream reads on both outstanding credit and an un-full coalescer buffer).

A real contract-derived ``high_water`` (via ``from_signals`` + ``stream_buffer_bytes``)
feeds the ``may_read`` dimension so the gate is exercised against the shipped
high-water source, not a hand-picked constant.

Test files are not under the import-linter layer contract, so the direct imports of
``runtime`` + ``egress`` here are allowed.
"""

# Feature: sse-egress-pipeline, Property 2: Credit conservation
# Validates: Requirements 5.7

from __future__ import annotations

import random

from gateway_v2.egress.backpressure import CreditFlowControl, CreditState
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

#: >= 10,000 iterations (house idiom, no hypothesis).
_ITERATIONS = 10_000
#: Operations applied per credit stream before it is discarded and a fresh one begins.
_OPS_PER_STREAM = 24


def _contract(
    *,
    target_p99_ms: float = 20.0,
    utilization_cap: float = 0.75,
    per_worker_rss: int = 400 * 1024 * 1024,
) -> ResourceContract:
    """A serviceable contract via ``from_signals`` (same shape as the LGW06/LGW12 tests).

    The signals clear ``from_signals``' refuse-to-start checks on both the CPU and RAM
    worker paths, so ``stream_buffer_bytes`` returns a real derived high-water that feeds
    the ``may_read`` dimension of this property.
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


def _assert_conservation(credit: CreditFlowControl, *, seed: int, where: str) -> None:
    """At every observation point: outstanding + consumed == granted, zero deviation (R5.7)."""
    state: CreditState = credit.state()
    outstanding = credit.outstanding()
    # The derived ``outstanding`` on the frozen observation and the live accessor agree.
    assert state.outstanding == outstanding, (
        f"seed={seed:#x} {where}: state.outstanding={state.outstanding} != "
        f"outstanding()={outstanding}"
    )
    # The invariant, with ZERO tolerance.
    assert outstanding + state.consumed == state.granted, (
        f"seed={seed:#x} {where}: outstanding({outstanding}) + consumed({state.consumed}) "
        f"!= granted({state.granted})"
    )
    # Outstanding is never negative (spend clamps at the granted total, R5.1).
    assert outstanding >= 0, (
        f"seed={seed:#x} {where}: outstanding({outstanding}) is negative"
    )


def test_credit_conservation() -> None:
    """outstanding + consumed = granted with zero deviation across random op sequences (R5.7).

    A seeded ``random.Random`` drives >= 10,000 operations split across fresh credit
    streams. Each operation is a random ``grant_on_consume`` or ``spend`` (including the
    non-positive / idle cases), and after EVERY operation the conservation invariant,
    grant-exactly-N / grant-zero-when-idle, non-negativity, and the ``may_read`` gate are
    all re-checked.
    """
    seed = 0x12_02
    rng = random.Random(seed)
    # The high-water that feeds the ``may_read`` gate -- a real contract-derived bound.
    high_water = _contract().stream_buffer_bytes(active_streams=8)
    assert high_water >= 1, f"seed={seed:#x} fixture high_water must be serve-able"

    credit = CreditFlowControl()
    _assert_conservation(credit, seed=seed, where="initial")
    assert credit.outstanding() == 0, f"seed={seed:#x} a fresh stream holds no credit"

    ops = 0
    while ops < _ITERATIONS:
        # Fresh stream so the counters do not grow without bound over 10k ops; the
        # invariant must hold identically from a zero baseline each time.
        credit = CreditFlowControl()
        _assert_conservation(credit, seed=seed, where=f"stream-start@{ops}")

        for _ in range(_OPS_PER_STREAM):
            if ops >= _ITERATIONS:
                break
            ops += 1

            # A random amount spanning the idle / non-positive band and positive values.
            amount = rng.randint(-4, 4096)
            choice = rng.random()

            if choice < 0.55:
                # grant_on_consume: grants EXACTLY N for N > 0, EXACTLY 0 for N <= 0.
                before = credit.state().granted
                granted = credit.grant_on_consume(amount)
                expected = amount if amount > 0 else 0
                assert granted == expected, (
                    f"seed={seed:#x} op={ops}: grant_on_consume({amount}) returned "
                    f"{granted}, expected {expected} (R5.2/R5.3)"
                )
                # ``granted`` moved by exactly the returned amount -- no hidden drift.
                assert credit.state().granted == before + expected, (
                    f"seed={seed:#x} op={ops}: granted total moved by "
                    f"{credit.state().granted - before}, expected {expected}"
                )
            else:
                # spend: consumes outstanding credit, clamped so outstanding stays >= 0.
                before = credit.state()
                outstanding_before = credit.outstanding()
                credit.spend(amount)
                after = credit.state()
                # granted is untouched by a spend.
                assert after.granted == before.granted, (
                    f"seed={seed:#x} op={ops}: spend changed granted "
                    f"{before.granted}->{after.granted}"
                )
                if amount <= 0:
                    # Non-positive spend is a no-op.
                    assert after.consumed == before.consumed, (
                        f"seed={seed:#x} op={ops}: non-positive spend({amount}) moved "
                        f"consumed {before.consumed}->{after.consumed}"
                    )
                else:
                    # consumed rises by at most the request and never past granted.
                    expected_consumed = min(before.granted, before.consumed + amount)
                    assert after.consumed == expected_consumed, (
                        f"seed={seed:#x} op={ops}: spend({amount}) consumed "
                        f"{after.consumed}, expected {expected_consumed}"
                    )
                    # Clamp holds: never consumed more outstanding than was available.
                    assert after.consumed - before.consumed <= outstanding_before, (
                        f"seed={seed:#x} op={ops}: spend over-consumed outstanding"
                    )

            # The invariant, re-checked after EVERY single operation.
            _assert_conservation(credit, seed=seed, where=f"op={ops}")

            # may_read is True iff outstanding > 0 AND buffered < high_water (R5.1).
            outstanding = credit.outstanding()
            for buffered in (0, high_water - 1, high_water, high_water + 1):
                if buffered < 0:
                    continue
                expected_read = outstanding > 0 and buffered < high_water
                got_read = credit.may_read(buffered, high_water)
                assert got_read is expected_read, (
                    f"seed={seed:#x} op={ops}: may_read(buffered={buffered}, "
                    f"high_water={high_water}) = {got_read}, expected {expected_read} "
                    f"(outstanding={outstanding}) (R5.1)"
                )

    assert ops == _ITERATIONS, (
        f"seed={seed:#x} drove {ops} operations, expected {_ITERATIONS}"
    )
