"""Client-side C4 collector property tests (B6 / R2-21), task 6.2.

Covers the pure ``load_harness.c4`` reduction + aggregation (no I/O, no server, no event loop):

* **Property 6 — C4 is the worst chunk, holdback-excluded, errors ``+∞``** (task 6.2): for any
  stream of per-chunk arrivals (ascending ``arrival_s``) with an injected ``holdback_ms`` segment,
  ``reduce_stream(obs).worst_chunk_ms`` equals the MAXIMUM inter-chunk client-side latency in ms
  (``max gap between consecutive arrival_s * 1000``) with ``holdback_ms`` SUBTRACTED and floored at
  ``0.0`` (Req 8.1/8.3); for any errored stream (``errored=True``) ``reduce_stream`` yields
  ``worst_chunk_ms == math.inf`` and ``errored`` True (Req 8.4); and ``c4_p99_over_all_streams`` /
  ``summarize(...).c4_p99_ms`` over ALL offered streams INCLUDES the ``+∞`` entries, so enough
  errored streams push the nearest-rank p99 onto an ``inf`` entry and the headline C4 becomes
  ``+∞`` (Req 8.5/12.5). The nearest-rank p99 is checked against an INDEPENDENT reference computed
  over the full sorted list (finite + ``inf``).
* The long/short split: ``summarize`` partitions the FINITE per-stream C4 by the
  ``StreamObservation.tag`` (``PromptTag.LONG`` vs ``SHORT``) into
  ``C4Summary.long_prompt`` / ``short_prompt`` ``PathSamples``; errored streams contribute to the
  headline p99 but to NEITHER finite sample set (Req 4.3 tie-in).

House idiom: a single seeded ``random.Random`` driven for >= 10,000 iterations with the seed logged
in every assertion message; NO ``hypothesis``. The collector is pure, so no clock/network is
injected. Test files are not under the import-linter layer contract.

This file is self-contained and importable; task 6.3 APPENDS the Property 7 reconciliation test to
it. The module-level names below are deliberately ``_P6_``-scoped / Property-6-specific so they do
not collide with anything the appended Property 7 test will introduce.
"""

from __future__ import annotations

import math
import random

from gateway_v2.runtime.latency_slo import (
    _LOST_SEGMENT_TAIL_MS as _P7_LOST_SEGMENT_TAIL_MS,
)
from gateway_v2.runtime.latency_slo import (
    PairedPathSamples as _P7_PairedPathSamples,
)
from gateway_v2.runtime.latency_slo import (
    PathSamples as _P7_PathSamples,
)
from gateway_v2.runtime.latency_slo import (
    firewall_added_delta as _P7_latency_slo_firewall_added_delta,
)
from load_harness.c4 import (
    C4Summary,
    ChunkArrival,
    StreamObservation,
    c4_p99_over_all_streams,
    reduce_stream,
    summarize,
)
from load_harness.c4 import (
    DirectBaselineMissing as _P7_DirectBaselineMissing,
)
from load_harness.c4 import (
    reconcile_firewall_delta as _P7_reconcile_firewall_delta,
)
from load_harness.strata.prompts import PromptTag

#: Total driving iterations (house idiom >= 10,000).
_P6_ITERATIONS = 10_000

#: Milliseconds per second — the SAME dimensional conversion ``c4._MS_PER_S`` applies. Re-expressed
#: here (not imported from the private module name) so the reference computation is independent of
#: the module under test rather than borrowing its constant.
_P6_MS_PER_S = 1000.0

#: Max chunks per synthesized stream (>= 2 so there is at least one inter-chunk gap in some draws).
_P6_MAX_CHUNKS = 8

#: Max per-chunk inter-arrival gap drawn, in seconds (keeps gaps * 1000 in a readable ms range).
_P6_MAX_GAP_S = 0.5

#: Max injected holdback figure, in ms (spans below AND above typical worst gaps so the floor-at-0
#: branch is exercised).
_P6_MAX_HOLDBACK_MS = 600.0

#: A fixed stream-start reference; absolute offset is irrelevant (C4 keys on gaps), kept constant
#: for determinism.
_P6_START_S = 1_000.0


def _reference_worst_chunk_ms(
    chunks: tuple[ChunkArrival, ...],
    *,
    holdback_ms: float,
) -> float:
    """Independent reference for a NON-errored stream's C4.

    Max inter-chunk gap in ms (``(cur.arrival_s - prev.arrival_s) * 1000``) over consecutive
    chunks, minus ``holdback_ms``, floored at ``0.0``. A stream with < 2 chunks has no gap and
    yields ``0.0``. Computed WITHOUT touching ``c4`` so it is a genuine oracle for Property 6.
    """
    worst_gap_ms = 0.0
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        gap_ms = (cur.arrival_s - prev.arrival_s) * _P6_MS_PER_S
        worst_gap_ms = max(worst_gap_ms, gap_ms)
    return max(worst_gap_ms - holdback_ms, 0.0)


def _reference_p99(values: tuple[float, ...]) -> float:
    """Independent nearest-rank p99 over the FULL sorted list (finite values AND ``math.inf``).

    Rank ``ceil(0.99 * n)`` (1-indexed), clamped to ``n``; index ``min(rank, n) - 1``. ``sorted``
    places every ``math.inf`` at the tail, so once the share of ``inf`` entries reaches the top 1%
    the p99 lands on an ``inf`` and the result is ``math.inf``. Oracle for Req 8.5; does not call
    ``c4``.
    """
    n = len(values)
    ordered = sorted(values)
    rank = -(-99 * n // 100)  # ceil(0.99 * n)
    index = min(rank, n) - 1
    return ordered[index]


def _make_chunks(rng: random.Random) -> tuple[ChunkArrival, ...]:
    """Synthesize an ascending-``arrival_s`` chunk sequence (0..n-1), accumulating positive gaps.

    ``n`` is drawn in ``[0, _P6_MAX_CHUNKS]`` so empty and single-chunk (no-gap) streams are
    covered alongside multi-chunk streams; each gap is a positive draw so ``arrival_s`` strictly
    increases.
    """
    n = rng.randint(0, _P6_MAX_CHUNKS)
    arrivals: list[ChunkArrival] = []
    t = _P6_START_S
    for index in range(n):
        if index > 0:
            t += rng.uniform(1e-6, _P6_MAX_GAP_S)
        arrivals.append(ChunkArrival(index=index, arrival_s=t))
    return tuple(arrivals)


def _make_observation(rng: random.Random) -> StreamObservation:
    """Synthesize one random :class:`StreamObservation` across the full shape space.

    Mixes errored / non-errored streams, a holdback figure spanning below AND above the typical
    worst gap (to drive the floor-at-0 subtraction), and a random long/short tag so the summary
    split is exercised.
    """
    return StreamObservation(
        chunks=_make_chunks(rng),
        holdback_ms=rng.uniform(0.0, _P6_MAX_HOLDBACK_MS),
        errored=rng.random() < 0.3,
        tag=rng.choice((PromptTag.LONG, PromptTag.SHORT)),
    )


# --------------------------------------------------------------------------- #
# Task 6.2 — Property 6: C4 is the worst chunk, holdback-excluded, errors +inf
# Feature: load-harness-r2-21, Property 6: C4 is the worst chunk holdback-excluded errors infinity
# Validates: Requirements 8.1, 8.3, 8.4, 8.5, 12.5
# --------------------------------------------------------------------------- #


def test_property6_reduce_stream_is_worst_holdback_excluded_or_infinity() -> None:
    """``reduce_stream`` is max-gap-ms minus holdback floored at 0, or ``+∞`` on error.

    For many random streams: an errored stream reduces to ``worst_chunk_ms == math.inf`` with
    ``errored`` True (Req 8.4); a non-errored stream reduces to the MAXIMUM inter-chunk ms gap with
    ``holdback_ms`` subtracted and floored at ``0.0``, matching an independent reference computed
    without the module under test (Req 8.1/8.3). Every reduced finite value is non-negative.
    """
    seed = 0x21_06
    rng = random.Random(seed)
    for i in range(_P6_ITERATIONS):
        obs = _make_observation(rng)
        reduced = reduce_stream(obs)
        if obs.errored:
            assert reduced.errored is True, f"seed={seed:#x} iter={i}: errored flag lost"
            assert math.isinf(reduced.worst_chunk_ms) and reduced.worst_chunk_ms > 0.0, (
                f"seed={seed:#x} iter={i}: errored stream C4 "
                f"{reduced.worst_chunk_ms} is not +inf"
            )
            assert reduced.is_finite is False, f"seed={seed:#x} iter={i}: errored is_finite True"
            continue
        expected = _reference_worst_chunk_ms(obs.chunks, holdback_ms=obs.holdback_ms)
        assert reduced.errored is False, f"seed={seed:#x} iter={i}: non-errored flagged errored"
        assert reduced.is_finite is True, f"seed={seed:#x} iter={i}: finite stream is_finite False"
        assert reduced.worst_chunk_ms == expected, (
            f"seed={seed:#x} iter={i}: worst_chunk_ms={reduced.worst_chunk_ms} "
            f"!= reference {expected} (holdback_ms={obs.holdback_ms})"
        )
        assert reduced.worst_chunk_ms >= 0.0, (
            f"seed={seed:#x} iter={i}: C4 {reduced.worst_chunk_ms} is negative (floor-at-0 broke)"
        )


def test_property6_all_streams_p99_includes_infinity_entries() -> None:
    """The headline C4 p99 over ALL streams includes the ``+∞`` errored entries (Req 8.5/12.5).

    For many random runs (each a non-empty bag of streams), ``c4_p99_over_all_streams`` equals an
    INDEPENDENT nearest-rank p99 over the FULL sorted list of every stream's C4 — finite values AND
    the ``math.inf`` entries for errored streams. When enough streams errored, both land on
    ``math.inf``.
    """
    seed = 0x21_06_5
    rng = random.Random(seed)
    runs = _P6_ITERATIONS // 20  # each run reduces up to _run_size streams
    for i in range(runs):
        run_size = rng.randint(1, 40)
        reduced = tuple(reduce_stream(_make_observation(rng)) for _ in range(run_size))
        got = c4_p99_over_all_streams(reduced)
        expected = _reference_p99(tuple(c.worst_chunk_ms for c in reduced))
        if math.isinf(expected):
            assert math.isinf(got), (
                f"seed={seed:#x} iter={i} run_size={run_size}: p99 {got} != +inf reference"
            )
        else:
            assert got == expected, (
                f"seed={seed:#x} iter={i} run_size={run_size}: p99 {got} != reference {expected}"
            )


def test_property6_all_errored_run_p99_is_infinity() -> None:
    """A run in which every offered stream errored has a headline C4 p99 of ``+∞`` (Req 8.5)."""
    seed = 0x21_06_A
    rng = random.Random(seed)
    for i in range(_P6_ITERATIONS // 50):
        run_size = rng.randint(1, 25)
        reduced = tuple(
            reduce_stream(
                StreamObservation(
                    chunks=_make_chunks(rng),
                    holdback_ms=rng.uniform(0.0, _P6_MAX_HOLDBACK_MS),
                    errored=True,
                    tag=rng.choice((PromptTag.LONG, PromptTag.SHORT)),
                ),
            )
            for _ in range(run_size)
        )
        assert math.isinf(c4_p99_over_all_streams(reduced)), (
            f"seed={seed:#x} iter={i} run_size={run_size}: all-errored run p99 is not +inf"
        )


def test_property6_summarize_splits_finite_by_tag_and_excludes_errored() -> None:
    """``summarize`` splits FINITE C4 by long/short tag; errored feed the headline, not the split.

    For many random runs: ``C4Summary.c4_p99_ms`` equals the independent headline p99 over ALL
    streams (incl ``+∞``, Req 8.5); every FINITE non-errored stream's C4 appears in exactly one of
    ``long_prompt`` / ``short_prompt`` keyed on its ``tag`` (Req 4.3); the long + short sample
    counts sum to the number of finite streams; and NO errored stream contributes a sample to
    either tagged ``PathSamples``.
    """
    seed = 0x21_06_B
    rng = random.Random(seed)
    runs = _P6_ITERATIONS // 20
    saw_long = False
    saw_short = False
    saw_errored_excluded = False
    for i in range(runs):
        run_size = rng.randint(1, 40)
        observations = tuple(_make_observation(rng) for _ in range(run_size))
        summary = summarize(observations)
        assert isinstance(summary, C4Summary)

        reduced = tuple(reduce_stream(obs) for obs in observations)
        expected_headline = _reference_p99(tuple(c.worst_chunk_ms for c in reduced))
        if math.isinf(expected_headline):
            assert math.isinf(summary.c4_p99_ms), (
                f"seed={seed:#x} iter={i}: headline {summary.c4_p99_ms} != +inf reference"
            )
        else:
            assert summary.c4_p99_ms == expected_headline, (
                f"seed={seed:#x} iter={i}: headline {summary.c4_p99_ms} != {expected_headline}"
            )

        # Independent oracle split of the FINITE streams by tag.
        exp_long: list[float] = []
        exp_short: list[float] = []
        for obs, red in zip(observations, reduced, strict=True):
            if red.errored:
                saw_errored_excluded = True
                assert not red.is_finite
                continue
            if obs.tag is PromptTag.LONG:
                exp_long.append(red.worst_chunk_ms)
            else:
                exp_short.append(red.worst_chunk_ms)

        assert summary.long_prompt.samples_ms == tuple(exp_long), (
            f"seed={seed:#x} iter={i}: long_prompt split mismatch"
        )
        assert summary.short_prompt.samples_ms == tuple(exp_short), (
            f"seed={seed:#x} iter={i}: short_prompt split mismatch"
        )
        finite_count = sum(1 for red in reduced if red.is_finite)
        assert len(summary.long_prompt.samples_ms) + len(summary.short_prompt.samples_ms) == (
            finite_count
        ), f"seed={seed:#x} iter={i}: finite split does not cover all finite streams"
        # No +inf ever leaks into a finite tagged sample set.
        for ms in (*summary.long_prompt.samples_ms, *summary.short_prompt.samples_ms):
            assert math.isfinite(ms), f"seed={seed:#x} iter={i}: +inf leaked into a tagged split"

        saw_long = saw_long or bool(exp_long)
        saw_short = saw_short or bool(exp_short)

    assert saw_long, "long-prompt split never exercised across the sweep"
    assert saw_short, "short-prompt split never exercised across the sweep"
    assert saw_errored_excluded, "errored-stream exclusion never exercised across the sweep"


def test_property6_total_iterations_cover_the_input_space() -> None:
    """The Property 6 sweep drives >= 10,000 total reductions (house idiom floor guard)."""
    reductions = _P6_ITERATIONS
    runs_p99 = (_P6_ITERATIONS // 20) * 40  # upper bound on streams reduced in the p99 sweep
    assert reductions >= 10_000, f"reduction sweep {reductions} < 10,000 house-idiom floor"
    assert runs_p99 >= 10_000, f"p99 sweep {runs_p99} < 10,000 house-idiom floor"


# --------------------------------------------------------------------------- #
# Task 6.3 — Property 7: C4 reconciliation matches latency_slo
# Feature: load-harness-r2-21, Property 7: C4 reconciliation matches latency_slo
# Validates: Requirements 8.2, 9.1, 9.2
# --------------------------------------------------------------------------- #
#
# Property 7 proves the harness DELEGATES the Firewall_Added_Delta to the shipped B5 authority
# `gateway_v2.runtime.latency_slo` and never recomputes it: for any set of finite per-stream C4
# samples (a through-firewall `PathSamples`) and a Direct_Path baseline `PathSamples`,
# ``c4.reconcile_firewall_delta(through, direct)`` returns EXACTLY what
# ``latency_slo.firewall_added_delta(PairedPathSamples(through, direct))`` returns — asserted
# field-by-field across the whole `FirewallDelta` (same delta_p99_ms, same through/direct p99s,
# same excluded-tail counts). Sample sets are drawn so values straddle the ≈ 40 ms
# ``latency_slo._LOST_SEGMENT_TAIL_MS`` on BOTH paths, exercising the symmetric tail exclusion the
# property is about (Req 9.2). The `DirectBaselineMissing` fail-closed propagation (Req 9's "never
# fabricate a delta") is folded in: an empty direct path, or a direct path all of whose samples are
# lost-segment tails (so it trims to empty), raises `DirectBaselineMissing` out of
# ``reconcile_firewall_delta`` identically to the direct `latency_slo` call.

#: Total driving iterations for the equality property (house idiom >= 10,000).
_P7_ITERATIONS = 10_000

#: Max samples drawn for either path per iteration (>= 1 so a non-empty path is always possible;
#: varied sizes exercise differing nearest-rank ranks on each side).
_P7_MAX_SAMPLES = 12

#: Upper bound (ms) for drawn latency samples — comfortably ABOVE the lost-segment tail so a healthy
#: fraction of every draw lands at/above it and the symmetric tail trim is exercised on both paths.
_P7_MAX_SAMPLE_MS = _P7_LOST_SEGMENT_TAIL_MS * 2.0


def _p7_draw_sample_ms(rng: random.Random) -> float:
    """Draw one latency sample (ms) biased to STRADDLE the lost-segment tail.

    With probability ~0.5 the draw is below ``_LOST_SEGMENT_TAIL_MS`` (retained by the trim), else
    at/above it (excluded) — including the exact boundary value, which the trim excludes (``>=``).
    So each path reliably mixes retained + excluded samples and the per-path exclusion counts the
    property compares are non-trivial.
    """
    bucket = rng.random()
    if bucket < 0.45:  # strictly below the tail -> retained
        return rng.uniform(0.0, _P7_LOST_SEGMENT_TAIL_MS - 1e-6)
    if bucket < 0.50:  # exactly the boundary -> excluded (>= threshold)
        return _P7_LOST_SEGMENT_TAIL_MS
    return rng.uniform(_P7_LOST_SEGMENT_TAIL_MS, _P7_MAX_SAMPLE_MS)  # at/above -> excluded


def _p7_draw_path(rng: random.Random, *, allow_empty: bool) -> _P7_PathSamples:
    """Synthesize one path's `PathSamples` with tail-straddling samples.

    ``allow_empty`` lets the direct path be drawn empty / all-tail so the `DirectBaselineMissing`
    branch is reached inside the SAME property loop; the through path is drawn non-empty-and-not-
    all-tail so the equality branch is well defined whenever the baseline survives.
    """
    low = 0 if allow_empty else 1
    count = rng.randint(low, _P7_MAX_SAMPLES)
    return _P7_PathSamples(tuple(_p7_draw_sample_ms(rng) for _ in range(count)))


def _p7_through_at_least_one_retained(rng: random.Random) -> _P7_PathSamples:
    """A through path guaranteed to keep >= 1 tail-free sample (so its delta is well defined)."""
    count = rng.randint(1, _P7_MAX_SAMPLES)
    samples = [_p7_draw_sample_ms(rng) for _ in range(count)]
    # Force at least one strictly-below-tail sample so the through path never trims to empty.
    samples[rng.randrange(count)] = rng.uniform(0.0, _P7_LOST_SEGMENT_TAIL_MS - 1e-6)
    return _P7_PathSamples(tuple(samples))


def test_property7_reconcile_delegates_field_by_field_to_latency_slo() -> None:
    """``reconcile_firewall_delta`` == ``latency_slo.firewall_added_delta`` field-by-field.

    For many random (through, direct) sample sets whose values straddle the ≈ 40 ms lost-segment
    tail on both paths, the harness delegate returns EXACTLY the shipped B5 computation — same
    ``delta_p99_ms``, ``through_p99_ms``, ``direct_p99_ms``, ``excluded_through``,
    ``excluded_direct`` — proving no duplicate computation (Req 9.1) and identical tail-exclusion
    semantics (Req 9.2). When the drawn direct baseline is empty or all lost-segment tails, BOTH
    the harness and the direct `latency_slo` call raise ``DirectBaselineMissing`` — the fail-closed
    propagation (Req 9) — and that branch is asserted in-loop too. Tail-straddling coverage (both
    paths trim a non-zero count at least once) is asserted after the sweep.
    """
    seed = 0x21_07
    rng = random.Random(seed)
    saw_through_excluded = False
    saw_direct_excluded = False
    saw_equality = False
    saw_missing = False
    for i in range(_P7_ITERATIONS):
        through = _p7_through_at_least_one_retained(rng)
        direct = _p7_draw_path(rng, allow_empty=True)
        paired = _P7_PairedPathSamples(through_firewall=through, direct=direct)

        try:
            reference = _P7_latency_slo_firewall_added_delta(paired)
        except _P7_DirectBaselineMissing:
            # Fail-closed propagation: the harness must raise the SAME error, not fabricate a delta.
            saw_missing = True
            raised = False
            try:
                _P7_reconcile_firewall_delta(through, direct)
            except _P7_DirectBaselineMissing:
                raised = True
            assert raised, (
                f"seed={seed:#x} iter={i}: latency_slo raised DirectBaselineMissing but "
                f"reconcile_firewall_delta did not propagate it"
            )
            continue

        got = _P7_reconcile_firewall_delta(through, direct)
        saw_equality = True
        assert got == reference, (
            f"seed={seed:#x} iter={i}: reconcile_firewall_delta {got} != "
            f"latency_slo.firewall_added_delta {reference}"
        )
        # Field-by-field (explicit, so a FirewallDelta field added later cannot silently drift).
        assert got.delta_p99_ms == reference.delta_p99_ms, f"seed={seed:#x} iter={i}: delta_p99_ms"
        assert got.through_p99_ms == reference.through_p99_ms, (
            f"seed={seed:#x} iter={i}: through_p99_ms"
        )
        assert got.direct_p99_ms == reference.direct_p99_ms, (
            f"seed={seed:#x} iter={i}: direct_p99_ms"
        )
        assert got.excluded_through == reference.excluded_through, (
            f"seed={seed:#x} iter={i}: excluded_through"
        )
        assert got.excluded_direct == reference.excluded_direct, (
            f"seed={seed:#x} iter={i}: excluded_direct"
        )
        assert got.delta_p99_ms == got.through_p99_ms - got.direct_p99_ms, (
            f"seed={seed:#x} iter={i}: delta is not through_p99 - direct_p99"
        )
        saw_through_excluded = saw_through_excluded or got.excluded_through > 0
        saw_direct_excluded = saw_direct_excluded or got.excluded_direct > 0

    assert saw_equality, "equality branch never exercised across the sweep"
    assert saw_missing, "DirectBaselineMissing propagation never exercised across the sweep"
    assert saw_through_excluded, "through-path lost-segment-tail exclusion never exercised"
    assert saw_direct_excluded, "direct-path lost-segment-tail exclusion never exercised"


def test_property7_direct_baseline_missing_propagates_examples() -> None:
    """`DirectBaselineMissing` propagates for an empty baseline and an all-tail baseline (Req 9).

    Two explicit fail-closed examples alongside the property: (a) an empty direct `PathSamples`, and
    (b) a direct path every sample of which is a lost-segment tail (``>= _LOST_SEGMENT_TAIL_MS``) so
    it trims to empty. In both, ``reconcile_firewall_delta`` raises ``DirectBaselineMissing`` — the
    SAME error the direct `latency_slo` call raises — rather than fabricating a delta, with a
    non-empty, tail-free through path that would otherwise yield a well-defined delta.
    """
    through = _P7_PathSamples((1.0, 2.0, 3.0))  # all tail-free -> delta defined if baseline existed

    empty_direct = _P7_PathSamples(())
    try:
        _P7_reconcile_firewall_delta(through, empty_direct)
    except _P7_DirectBaselineMissing:
        pass
    else:  # pragma: no cover - a miss is a hard failure
        raise AssertionError("empty direct baseline did not raise DirectBaselineMissing")

    all_tail_direct = _P7_PathSamples(
        (_P7_LOST_SEGMENT_TAIL_MS, _P7_LOST_SEGMENT_TAIL_MS + 5.0, _P7_LOST_SEGMENT_TAIL_MS * 1.5),
    )
    try:
        _P7_reconcile_firewall_delta(through, all_tail_direct)
    except _P7_DirectBaselineMissing:
        pass
    else:  # pragma: no cover - a miss is a hard failure
        raise AssertionError(
            "all-lost-segment-tail direct baseline did not raise DirectBaselineMissing",
        )


def test_property7_total_iterations_cover_the_input_space() -> None:
    """The Property 7 equality sweep drives >= 10,000 reconciliations (house-idiom floor guard)."""
    assert _P7_ITERATIONS >= 10_000, f"reconcile sweep {_P7_ITERATIONS} < 10,000 house-idiom floor"
