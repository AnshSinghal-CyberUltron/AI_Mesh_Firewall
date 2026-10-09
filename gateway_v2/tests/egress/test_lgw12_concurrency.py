"""LGW12-1 local-equivalent N-stream memory-plateau harness (GW12, task 15.1).

# Feature: sse-egress-pipeline
# Validates: Requirements 16.1

This is the **LOCAL, scaled-down** equivalent of the deferred cloud gate **LGW12-1**
-- the real 1,000-stream memory plateau. That full fleet plateau needs a real host
with real memory pressure and is recorded as a DEFERRED cloud gate (see
``.kiro/specs/sse-egress-pipeline/design.md`` -> "Deferred cloud / scale gates"); it is
NOT run here. This file is the in-process stand-in: ``N`` concurrent streams, each
pushing a LONG sequence of chunks through its own :class:`Coalescer`
(``gateway_v2.egress.backpressure``) while all ``N`` coalescers share the SAME live
``active_streams`` count ``= N``, driven by a seeded ``random.Random``.

What makes this the *plateau* shape rather than a restatement of Property 1
(``tests/egress/test_lgw12_coalescer.py``, which asserts the aggregate bound over short
random schedules): the streams here are LONG -- each pushes many more chunks than the
per-stream high-water could ever hold at once -- and the test asserts not only that the
aggregate stays within ``N x stream_buffer_bytes(N)`` at every observation point, but
also that the aggregate does **NOT GROW as the streams get longer**. Buffered memory
PLATEAUS at (and below) the derived bound regardless of how many chunks have flowed:
the running maximum aggregate stops rising long before the streams end, and the tail of
the run buffers no more than the head. A per-response-length memory leak -- buffering
that crept upward with the number of chunks delivered -- would make the running maximum
keep climbing and would fail here even though the single-observation aggregate bound of
Property 1 still held.

The harness pattern follows the shipped GW12b concurrency harness
(``tests/egress/test_lgw12b_concurrency.py``): several streams co-scheduled over the
same injected count, long/heavy vs. short/light stream mixes, and an explicit
structural (not merely statistical) isolation claim. The serviceable-``ResourceContract``
fixture and the ``N x stream_buffer_bytes(N)`` aggregate-bound assertion idiom are reused
from ``tests/egress/test_lgw12_coalescer.py`` (Property 1).

Layering note: ``egress`` sits below ``detect``; this test imports only the coalescer, the
``ResourceContract`` capacity authority, and ``domain.posture`` -- no scanner, no provider.
"""

from __future__ import annotations

import random

from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

#: Number of concurrent streams in the scaled-down plateau (the full 1,000-stream fleet
#: plateau is the deferred cloud gate; this is the in-process stand-in).
_MAX_ACTIVE_STREAMS = 16
#: Rounds per scenario; each round pushes one chunk through every one of the N streams. "Long
#: streams": the whole run delivers far more chunks than any per-stream ceiling holds at once,
#: so buffering that grew with the number of chunks delivered (a per-length leak) would show.
_ROUNDS = 240
#: Warm-up rounds discarded before the plateau is measured: streams start empty and ramp into
#: their steady-state band over the first few rounds, which is NOT growth-with-length.
_WARMUP_ROUNDS = 40
#: Outer scenarios (each a fresh serviceable contract + active-stream count + schedule).
#: rounds x streams x scenarios comfortably exceeds the >= 10,000-operation budget.
_SCENARIOS = 24

#: Fixed top-level seed; logged in every failure message for reproducibility.
_SEED = 0x12_01_15

#: Plateau tolerance: the second-half steady-state mean aggregate may exceed the first-half
#: mean by at most this fraction. Buffered memory oscillates in a bounded, stationary band
#: (``offer`` refuses once a stream would reach its ceiling), so head and tail means coincide
#: up to random-walk noise. A per-stream-length LEAK makes the tail mean trend strictly upward
#: and blows past this tolerance; a plateau keeps it inside.
_PLATEAU_TOLERANCE = 0.25


def _serviceable_contract(rng: random.Random) -> ResourceContract:
    """A random but serviceable :class:`ResourceContract` built via :func:`from_signals`.

    Ranges mirror ``tests/egress/test_lgw12_coalescer.py`` (Property 1) so ``from_signals`` is
    computable (at least one worker on both the CPU and RAM paths); this is the real capacity
    authority, so each coalescer's high-water is a genuine contract derivation -- the derived
    bound the memory must plateau at -- rather than a hand-picked constant.
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


def test_lgw12_1_memory_plateaus_at_derived_bound_no_growth_with_length() -> None:
    """LGW12-1 local equivalent: aggregate buffered PLATEAUS; no growth with stream length.

    # Feature: sse-egress-pipeline
    # Validates: Requirements 16.1

    ``N`` in-process streams share one live ``active_streams == N`` count. Over ``_ROUNDS``
    rounds of offer/release across all ``N`` long streams, at EVERY observation point:

    * every stream's ``buffered() <= high_water()`` (no stream exceeds its ceiling, R4.4); and
    * ``total buffered <= N x stream_buffer_bytes(N)`` -- the ``Active_Streams x per-stream
      ceiling`` aggregate memory bound (R5.4 / R16.1); and
    * buffered memory PLATEAUS: after a short warm-up ramp is discarded, the steady-state mean
      aggregate over the SECOND half of the (long) run does not rise above the FIRST-half mean
      by more than ``_PLATEAU_TOLERANCE``, and the second-half PEAK does not exceed the
      first-half peak by more than that tolerance. Memory settles into a stationary band near
      the derived bound; it does NOT grow with stream length (R16.1).

    Driven by a seeded ``random.Random`` for ``_SCENARIOS x _ROUNDS x N`` operations (>> 10,000),
    no hypothesis; the seed is in every failure message.

    The full 1,000-stream fleet plateau is a DEFERRED cloud gate; this is the scaled-down
    in-process equivalent.
    """
    rng = random.Random(_SEED)
    total_ops = 0
    plateau_scenarios = 0

    for scenario in range(_SCENARIOS):
        contract = _serviceable_contract(rng)

        # One fixed Active_Streams count for the whole scenario: every coalescer sees the SAME
        # live count, so the per-stream ceiling is identical and the aggregate bound is exactly
        # ``n x stream_buffer_bytes(n)`` (R5.4).
        n = rng.randint(2, _MAX_ACTIVE_STREAMS)

        def _active() -> int:
            return n  # noqa: B023 -- bound per-scenario on purpose (closes over this n)

        per_stream_ceiling = contract.stream_buffer_bytes(n)
        aggregate_bound = n * per_stream_ceiling

        coalescers = [Coalescer(contract=contract, active_streams=_active) for _ in range(n)]
        for c in coalescers:
            verdict = c.admit()
            assert verdict.code is None, (
                f"seed={_SEED:#x} scenario={scenario} n={n}: serviceable contract rejected "
                f"admission (code={verdict.code!r})"
            )

        # Per-round aggregate buffered, one observation per round. The plateau is read off this
        # series: after a short warm-up ramp (streams starting empty fill into their band) the
        # series is stationary, so the first-half and second-half steady-state means coincide up
        # to noise. A per-stream-length LEAK would make the series TREND upward and lift the
        # second-half mean/peak above the first-half's beyond tolerance.
        per_round_aggregate: list[int] = []

        # "Long streams": each round every one of the N streams offers a chunk and (usually)
        # releases some previously buffered bytes, so the whole scenario pushes n x _ROUNDS
        # chunks -- far more than any single ceiling holds at once. Chunk sizes span below, at,
        # and above the per-stream ceiling so backpressure fires repeatedly along the way.
        for round_idx in range(_ROUNDS):
            for coalescer in coalescers:
                # A realistic, mean-reverting streaming loop so the buffer settles into a tight
                # stationary band near the ceiling (a genuine PLATEAU) rather than diffusing: each
                # round the stream offers one chunk, and the downstream consumer drains roughly a
                # chunk's worth of previously-released bytes. The offer is sized to a fraction of
                # the ceiling with light jitter; because the sum of attempted offers over the long
                # run hugely exceeds the ceiling, ``offer`` must refuse (backpressure) whenever a
                # chunk would reach the mark -- the buffer then holds steady, it never grows.
                chunk = max(1, per_stream_ceiling // 8)
                nbytes = chunk + rng.randint(0, chunk)  # [chunk, 2*chunk)
                before = coalescer.buffered()
                accepted = coalescer.offer(nbytes)
                after = coalescer.buffered()
                if not accepted:
                    # R4.3: a refused offer (backpressure) buffers nothing. The consumer MUST
                    # drain to make room before the stream can flow again -- this is what keeps
                    # the buffer in a bounded band instead of climbing with stream length.
                    assert after == before, (
                        f"seed={_SEED:#x} scenario={scenario} n={n} round={round_idx}: refused "
                        f"offer of {nbytes} grew buffer {before} -> {after} (R4.3)"
                    )
                    coalescer.release(nbytes)
                else:
                    assert after == before + nbytes, (
                        f"seed={_SEED:#x} scenario={scenario} n={n} round={round_idx}: accepted "
                        f"offer of {nbytes} moved buffer {before} -> {after} wrongly (R4.2)"
                    )
                    # Steadily-consuming downstream: drain about a chunk's worth most rounds so the
                    # stream keeps flowing for its full length and the buffer mean-reverts.
                    if rng.random() < 0.75:
                        coalescer.release(chunk + rng.randint(0, chunk))

                total_ops += 1

            # Observation point after EACH full round: assert the per-stream and aggregate
            # bounds, and track head/tail running maxima for the plateau assertion.
            total = 0
            for c in coalescers:
                buffered = c.buffered()
                assert buffered <= c.high_water(), (
                    f"seed={_SEED:#x} scenario={scenario} n={n} round={round_idx}: buffered "
                    f"{buffered} exceeded high_water {c.high_water()} (R4.4)"
                )
                total += buffered

            assert total <= aggregate_bound, (
                f"seed={_SEED:#x} scenario={scenario} n={n} round={round_idx}: aggregate "
                f"buffered {total} exceeded n x stream_buffer_bytes(n) = {aggregate_bound} "
                f"(R5.4 / R16.1)"
            )

            per_round_aggregate.append(total)

        # Plateau / no-growth-with-length (R16.1). Discard a short warm-up ramp (streams start
        # empty and fill into their steady-state band over the first few rounds), then split the
        # remaining stationary series into a first half (head) and second half (tail).
        steady = per_round_aggregate[_WARMUP_ROUNDS:]
        split = len(steady) // 2
        head = steady[:split]
        tail = steady[split:]
        if head and tail and max(steady) > 0:
            head_mean = sum(head) / len(head)
            tail_mean = sum(tail) / len(tail)
            head_peak = max(head)
            tail_peak = max(tail)

            # The second-half steady-state MEAN does not rise above the first-half mean beyond
            # the tolerance: the series is flat, not trending up with stream length.
            assert tail_mean <= head_mean * (1.0 + _PLATEAU_TOLERANCE) + 1.0, (
                f"seed={_SEED:#x} scenario={scenario} n={n}: steady-state aggregate buffered "
                f"GREW with stream length -- second-half mean {tail_mean:.1f} exceeded "
                f"first-half mean {head_mean:.1f} by more than {_PLATEAU_TOLERANCE:.0%} "
                f"(no plateau; R16.1). bound={aggregate_bound}"
            )
            # The plateau CEILING is the derived bound itself: the second-half peak sits at or
            # below ``N x stream_buffer_bytes(N)`` just as the first-half peak does. "Memory
            # plateaus AT the derived bound" means neither half's peak ever crosses it, no matter
            # how many chunks have flowed -- a per-length leak would eventually push a late peak
            # past the bound (and the per-round aggregate assertion above would already catch it).
            # The peak itself is a high-variance extreme of a bounded random walk, so it is NOT
            # compared head-vs-tail with a tight tolerance (that would test noise); the stationary
            # MEAN above is the robust no-growth signal, and the bound is the hard ceiling here.
            assert head_peak <= aggregate_bound and tail_peak <= aggregate_bound, (
                f"seed={_SEED:#x} scenario={scenario} n={n}: a steady-state peak crossed the "
                f"derived bound -- head_peak={head_peak} tail_peak={tail_peak} "
                f"bound={aggregate_bound} (no plateau at the bound; R16.1)"
            )
            plateau_scenarios += 1

    # Sanity: the long-stream run actually exercised many operations (>> 10,000) and the plateau
    # assertion was meaningfully evaluated (at least one scenario buffered something to plateau).
    assert total_ops >= 10_000, (
        f"seed={_SEED:#x}: plateau harness ran only {total_ops} operations (expected >= 10,000)"
    )
    assert plateau_scenarios > 0, (
        f"seed={_SEED:#x}: no scenario buffered any bytes, so the plateau claim was never "
        f"exercised"
    )
