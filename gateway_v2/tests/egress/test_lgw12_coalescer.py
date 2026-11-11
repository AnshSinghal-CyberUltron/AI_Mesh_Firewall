"""LGW12 bounded-coalescer memory-bound property (GW12, task 3.2).

# Feature: sse-egress-pipeline, Property 1: Memory-bound invariant
# Validates: Requirements 4.4, 5.3, 5.4

Property 1 (Memory-bound invariant). Over random arrival / offer / release
sequences across random active-stream counts, the streaming egress path never
holds more unflushed payload memory than the contract permits:

* a single stream's :meth:`Coalescer.buffered` never exceeds its
  :meth:`Coalescer.high_water` at ANY observation point (R4.4); and
* the total buffered across ``N`` in-process coalescers stays within
  ``N x stream_buffer_bytes(N)`` -- the ``Active_Streams x per-stream ceiling``
  aggregate bound (R5.4); and
* an :meth:`Coalescer.offer` that returns ``False`` (backpressure) buffers
  nothing -- the buffered count is unchanged across the refused offer (R4.3,
  which underwrites R4.4); and
* fail-closed admission below one byte: when the derived high-water drops below
  one byte, :meth:`Coalescer.admit` returns a verdict carrying
  ``posture.STREAM_BUFFER_UNAVAILABLE`` and buffers zero payload bytes (R4.5).

The property is exercised with a seeded ``random.Random`` driven for
``_ITERATIONS`` (>= 10,000) iterations -- NO hypothesis. The seed is logged in
every failure message so a counterexample is reproducible.

Layering note: ``egress`` sits below ``detect``; this test imports only the
coalescer, the ``ResourceContract`` capacity authority, and ``domain.posture`` --
no scanner, no provider.
"""

from __future__ import annotations

import random

from gateway_v2.domain import posture
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

#: Property-test iteration budget (house idiom: seeded ``random.Random`` >= 10,000, no hypothesis).
_ITERATIONS = 10_000

#: Fixed top-level seed; logged in every failure message for reproducibility.
_SEED = 0x12_01

#: Upper bound on the per-iteration operation count (arrivals / offers / releases).
_MAX_OPS = 40
#: Upper bound on the number of concurrent in-process coalescers in one iteration.
_MAX_ACTIVE_STREAMS = 24


def _serviceable_contract(rng: random.Random) -> ResourceContract:
    """A random but serviceable :class:`ResourceContract` built via :func:`from_signals`.

    Ranges mirror ``tests/admit/test_lgw06_chunk_derivation.py`` so ``from_signals`` is computable
    (at least one worker on both the CPU and RAM paths); this is the real capacity authority, so the
    coalescer's high-water is a genuine contract derivation rather than a hand-picked constant.
    """
    utilization_cap = rng.uniform(0.25, 0.95)
    cpu_quota = rng.uniform(1.0 / utilization_cap + 1.0, 32.0)
    per_worker_rss = rng.randint(128, 512) * 1024 * 1024
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


def test_property1_memory_bound_invariant() -> None:
    """Property 1: buffered never exceeds high-water; aggregate within Active_Streams x ceiling.

    # Feature: sse-egress-pipeline, Property 1: Memory-bound invariant
    # Validates: Requirements 4.4, 5.3, 5.4
    """
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        contract = _serviceable_contract(rng)

        # A fixed Active_Streams count for this iteration: every coalescer sees the SAME live
        # count, so the per-stream ceiling is identical and the aggregate bound is exactly
        # ``n x stream_buffer_bytes(n)`` (R5.4).
        n = rng.randint(1, _MAX_ACTIVE_STREAMS)

        def _active() -> int:
            return n  # noqa: B023 -- bound per-iteration on purpose (closes over this n)

        per_stream_ceiling = contract.stream_buffer_bytes(n)
        aggregate_bound = n * per_stream_ceiling

        coalescers = [Coalescer(contract=contract, active_streams=_active) for _ in range(n)]

        # Every coalescer admits cleanly (serviceable contract => high-water >= 1 byte).
        for c in coalescers:
            verdict = c.admit()
            assert verdict.code is None, (
                f"seed={_SEED:#x} iter={i} n={n}: serviceable contract rejected admission "
                f"(code={verdict.code!r}, high_water={verdict.high_water})"
            )
            assert verdict.high_water == per_stream_ceiling, (
                f"seed={_SEED:#x} iter={i} n={n}: admit high_water {verdict.high_water} "
                f"!= contract ceiling {per_stream_ceiling}"
            )

        # A random arrival / offer / release schedule across the N coalescers. Deltas can exceed
        # the ceiling (so backpressure fires), and releases can over-drain (clamped at zero).
        ops = rng.randint(1, _MAX_OPS)
        for _ in range(ops):
            idx = rng.randrange(n)
            coalescer = coalescers[idx]
            if rng.random() < 0.6:
                # Arrival + offer. Size spans below, at, and above the ceiling.
                nbytes = rng.randint(0, max(1, per_stream_ceiling * 2))
                before = coalescer.buffered()
                accepted = coalescer.offer(nbytes)
                after = coalescer.buffered()
                # R4.3: a refused offer (backpressure) must not have grown the buffer.
                if not accepted:
                    assert after == before, (
                        f"seed={_SEED:#x} iter={i} n={n}: refused offer of {nbytes} grew buffer "
                        f"{before} -> {after} (R4.3)"
                    )
                else:
                    assert after == before + max(0, nbytes), (
                        f"seed={_SEED:#x} iter={i} n={n}: accepted offer of {nbytes} moved buffer "
                        f"{before} -> {after} by the wrong amount (R4.2)"
                    )
            else:
                # Downstream consumed: release (may over-drain; coalescer clamps at zero).
                nbytes = rng.randint(0, max(1, per_stream_ceiling * 2))
                coalescer.release(nbytes)
                assert coalescer.buffered() >= 0, (
                    f"seed={_SEED:#x} iter={i} n={n}: release drove buffered negative"
                )

            # Observation point: assert the invariants after EVERY operation.
            total = 0
            for c in coalescers:
                buffered = c.buffered()
                # R4.4: a single stream never exceeds its high-water.
                assert buffered <= c.high_water(), (
                    f"seed={_SEED:#x} iter={i} n={n}: buffered {buffered} exceeded high_water "
                    f"{c.high_water()} (R4.4)"
                )
                total += buffered
            # R5.4: aggregate across the N in-process coalescers stays within the
            # Active_Streams x per-stream ceiling bound.
            assert total <= aggregate_bound, (
                f"seed={_SEED:#x} iter={i} n={n}: aggregate buffered {total} exceeded "
                f"n x stream_buffer_bytes(n) = {aggregate_bound} (R5.4)"
            )


def test_property1_fail_closed_admit_below_one_byte() -> None:
    """Property 1 (fail-closed half, R4.5): sub-byte ceiling => reject + buffer zero.

    # Feature: sse-egress-pipeline, Property 1: Memory-bound invariant
    # Validates: Requirements 4.4, 5.3, 5.4

    When ``stream_buffer_bytes`` raises ``CapacityUnavailable`` (the derived per-stream ceiling is
    below one byte -- a tiny usable memory divided across a huge ``Active_Streams`` count), the
    coalescer must fail closed: ``admit()`` returns ``STREAM_BUFFER_UNAVAILABLE`` with
    ``high_water == 0`` and the stream buffers zero payload bytes. Driven over random
    serviceable contracts, each pushed below the one-byte floor by a sufficiently large
    active-stream count.
    """
    rng = random.Random(_SEED ^ 0x5A5A)
    for i in range(_ITERATIONS):
        contract = _serviceable_contract(rng)
        usable = contract.memory_limit * contract.utilization_cap

        # Choose an Active_Streams count large enough that floor(usable / streams) < 1, i.e.
        # streams > usable. This drives stream_buffer_bytes below the one-byte floor so it raises
        # CapacityUnavailable and admit() must fail closed (R4.5).
        starving_streams = int(usable) + rng.randint(1, 1_000)

        def _active() -> int:
            return starving_streams  # noqa: B023 -- bound per-iteration on purpose

        coalescer = Coalescer(contract=contract, active_streams=_active)
        verdict = coalescer.admit()

        assert verdict.code == posture.STREAM_BUFFER_UNAVAILABLE, (
            f"seed={_SEED ^ 0x5A5A:#x} iter={i} streams={starving_streams}: sub-byte ceiling did "
            f"not fail closed (code={verdict.code!r}, R4.5)"
        )
        assert verdict.high_water == 0, (
            f"seed={_SEED ^ 0x5A5A:#x} iter={i} streams={starving_streams}: fail-closed verdict "
            f"carried non-zero high_water {verdict.high_water} (R4.5)"
        )
        # Buffers zero payload bytes: a rejected stream holds nothing.
        assert coalescer.buffered() == 0, (
            f"seed={_SEED ^ 0x5A5A:#x} iter={i} streams={starving_streams}: rejected stream "
            f"buffered {coalescer.buffered()} payload bytes (R4.5)"
        )
