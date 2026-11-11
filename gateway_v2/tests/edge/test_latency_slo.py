"""Property + example tests for the Firewall_Added_Delta SLO definition (GW16b / B5).

Covers the pure `runtime/latency_slo.py` half: the symmetric ≈ 40 ms lost-segment tail-trim
(`exclude_lost_segment_tails`), the paired-path delta (`firewall_added_delta`), the absent-baseline
error (`DirectBaselineMissing`), and the contract-bound SLO assert (`assert_within_slo`).

House idiom: a seeded `random.Random`, NO hypothesis, >= 10,000 iterations, one property <-> one
test. Each oracle is re-derived INDEPENDENTLY in this file (a plain nearest-rank p99 and an explicit
`>= 40.0` tail filter) rather than calling the module's private helpers, so the test checks the
module against an independent definition, not against itself.
"""

from __future__ import annotations

import math
import random

import pytest

from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.latency_slo import (
    DirectBaselineMissing,
    FirewallDelta,
    PairedPathSamples,
    PathSamples,
    assert_within_slo,
    exclude_lost_segment_tails,
    firewall_added_delta,
)
from gateway_v2.runtime.resources import ResourceContract, from_signals

#: Property-test iteration budget (house idiom: seeded ``random.Random`` >= 10,000, no hypothesis).
_ITERATIONS = 10_000

#: Fixed top-level seed; logged in every failure message for reproducibility.
_SEED = 0x16B_5

#: The ≈ 40 ms lost-segment tail threshold, re-stated here as the INDEPENDENT oracle constant.
#: Deliberately NOT imported from the module so the test re-derives the exclusion rule itself.
_TAIL_MS = 40.0

#: Upper bound on the per-iteration sample count (kept small so 10k iterations stay fast).
_MAX_SAMPLES = 24


# --------------------------------------------------------------------------------------------------
# Independent oracles (re-derived here; do NOT call the module's private helpers).
# --------------------------------------------------------------------------------------------------


def _oracle_retained(samples_ms: tuple[float, ...]) -> tuple[float, ...]:
    """Samples strictly below the tail threshold (the independent exclusion oracle)."""
    return tuple(ms for ms in samples_ms if ms < _TAIL_MS)


def _oracle_excluded_count(samples_ms: tuple[float, ...]) -> int:
    """Count of samples at or above the tail threshold (independent oracle)."""
    return sum(1 for ms in samples_ms if ms >= _TAIL_MS)


def _oracle_p99(samples_ms: tuple[float, ...]) -> float:
    """Nearest-rank p99 of a non-empty tuple, re-derived independently of the module.

    rank = ceil(0.99 * n) (1-indexed); index = min(rank, n) - 1 over the sorted samples.
    """
    n = len(samples_ms)
    if n == 0:
        raise ValueError("oracle p99 undefined for empty input")
    ordered = sorted(samples_ms)
    rank = math.ceil(0.99 * n)
    index = min(rank, n) - 1
    return ordered[index]


# --------------------------------------------------------------------------------------------------
# Cheap, intelligently-constrained generators.
# --------------------------------------------------------------------------------------------------


def _gen_sample(rng: random.Random) -> float:
    """One latency sample biased to exercise the tail boundary.

    Mixes well-below-threshold values, well-above-threshold tail values, and values clustered on
    the exact 40.0 boundary (just-below / exactly-at / just-above) so the `< 40.0` vs `>= 40.0`
    split is hammered every iteration.
    """
    bucket = rng.random()
    if bucket < 0.45:
        return rng.uniform(0.0, 39.9)  # clearly retained
    if bucket < 0.80:
        return rng.uniform(40.0, 250.0)  # clearly a tail
    # Boundary cluster: exactly 40.0, just below, just above.
    return rng.choice((_TAIL_MS, _TAIL_MS - 1e-9, _TAIL_MS + 1e-9, 39.999_999, 40.000_001))


def _gen_samples(rng: random.Random, *, min_len: int = 0) -> tuple[float, ...]:
    """A tuple of samples whose length is in [min_len, _MAX_SAMPLES]."""
    length = rng.randint(min_len, _MAX_SAMPLES)
    return tuple(_gen_sample(rng) for _ in range(length))


def _gen_nonempty_tail_free(rng: random.Random) -> tuple[float, ...]:
    """A non-empty tuple guaranteed to retain >= 1 sample after a tail trim.

    Used to build paired paths whose tail-free p99 is defined (so Property 4 has a real delta).
    Guarantees at least one strictly-below-threshold sample, then adds an arbitrary mix.
    """
    guaranteed = (rng.uniform(0.0, 39.9),)
    extra = _gen_samples(rng, min_len=0)
    pool = list(guaranteed + extra)
    rng.shuffle(pool)
    return tuple(pool)


def _gen_all_tail(rng: random.Random) -> tuple[float, ...]:
    """A non-empty tuple whose every sample is at or above the threshold (trims to empty)."""
    length = rng.randint(1, _MAX_SAMPLES)
    return tuple(rng.uniform(40.0, 250.0) for _ in range(length))


def _serviceable_contract(rng: random.Random, *, target_p99_ms: float) -> ResourceContract:
    """A serviceable `ResourceContract` via `from_signals` with a caller-chosen SLO bound.

    Ranges mirror the shipped LGW06/LGW12 property fixtures so `from_signals` is computable (at
    least one worker on both the CPU and RAM paths); `target_p99_ms` is the only knob Property 6
    varies, so the bound under test is a genuine contract field, not a hand-picked literal.
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
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
    )


# ==================================================================================================
# Task 1.3 — Property 1/2/3 (tail exclusion)
# ==================================================================================================


# Feature: edge-gw16b, Property 1: Tail exclusion never raises a path's p99
# Validates: Requirements 5.2
def test_property1_tail_exclusion_never_raises_p99() -> None:
    """The p99 of the tail-free samples is <= the p99 of the full samples.

    Both p99s are computed with the independent nearest-rank oracle. The comparison is skipped
    whenever either side is empty (p99 undefined); the trimmed-empty / full-empty cases are asserted
    separately so no iteration is silently vacuous.
    """
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        full = _gen_samples(rng, min_len=0)
        trimmed_path, _ = exclude_lost_segment_tails(PathSamples(full))
        trimmed = trimmed_path.samples_ms

        # The trimmed set is exactly the retained oracle (independent check feeds the comparison).
        assert trimmed == _oracle_retained(full), f"seed={_SEED:#x} i={i} full={full}"

        if not full:
            assert trimmed == (), f"seed={_SEED:#x} i={i}: empty in -> empty out"
            continue
        if not trimmed:
            # All-tail (or empty): nothing retained; p99 comparison is undefined, so just assert
            # the trim removed everything.
            assert _oracle_retained(full) == (), f"seed={_SEED:#x} i={i} full={full}"
            continue

        full_p99 = _oracle_p99(full)
        trimmed_p99 = _oracle_p99(trimmed)
        assert trimmed_p99 <= full_p99, (
            f"seed={_SEED:#x} i={i} full_p99={full_p99} trimmed_p99={trimmed_p99} full={full}"
        )


# Feature: edge-gw16b, Property 2: Tail exclusion is idempotent
# Validates: Requirements 5.2
def test_property2_tail_exclusion_idempotent() -> None:
    """Applying the tail-trim twice yields the same samples as applying it once (f(f(x))==f(x))."""
    rng = random.Random(_SEED ^ 0x2)
    for i in range(_ITERATIONS):
        full = _gen_samples(rng, min_len=0)
        once, _ = exclude_lost_segment_tails(PathSamples(full))
        twice, excluded_second = exclude_lost_segment_tails(once)
        assert twice.samples_ms == once.samples_ms, (
            f"seed={_SEED ^ 0x2:#x} i={i} once={once.samples_ms} twice={twice.samples_ms}"
        )
        # Idempotence corollary: re-trimming an already-trimmed set excludes nothing.
        assert excluded_second == 0, f"seed={_SEED ^ 0x2:#x} i={i} re-excluded={excluded_second}"


# Feature: edge-gw16b, Property 3: Excluded samples are exactly the at-or-above-threshold samples
# Validates: Requirements 5.2
def test_property3_excluded_are_exactly_at_or_above_threshold() -> None:
    """Excluded count == #(samples >= 40.0); every retained sample is strictly < 40.0."""
    rng = random.Random(_SEED ^ 0x3)
    for i in range(_ITERATIONS):
        full = _gen_samples(rng, min_len=0)
        trimmed_path, excluded = exclude_lost_segment_tails(PathSamples(full))

        assert excluded == _oracle_excluded_count(full), (
            f"seed={_SEED ^ 0x3:#x} i={i} excluded={excluded} full={full}"
        )
        assert all(ms < _TAIL_MS for ms in trimmed_path.samples_ms), (
            f"seed={_SEED ^ 0x3:#x} i={i} retained={trimmed_path.samples_ms}"
        )
        # Conservation: retained + excluded == input length.
        assert len(trimmed_path.samples_ms) + excluded == len(full), (
            f"seed={_SEED ^ 0x3:#x} i={i} full={full}"
        )


# ==================================================================================================
# Task 1.4 — Property 4/5/6 (delta / baseline / SLO)
# ==================================================================================================


# Feature: edge-gw16b, Property 4: Delta is the difference of tail-free path p99s
# Validates: Requirements 5.1, 5.2
def test_property4_delta_is_difference_of_tail_free_p99s() -> None:
    """delta_p99_ms == through_p99_ms - direct_p99_ms, each p99 after symmetric tail exclusion.

    Both paths are generated non-empty-after-trim so the delta is always defined; the expected p99s
    are re-derived with the independent oracle over the independently-retained samples.
    """
    rng = random.Random(_SEED ^ 0x4)
    for i in range(_ITERATIONS):
        through = _gen_nonempty_tail_free(rng)
        direct = _gen_nonempty_tail_free(rng)
        paired = PairedPathSamples(
            through_firewall=PathSamples(through),
            direct=PathSamples(direct),
        )
        result = firewall_added_delta(paired)

        exp_through_p99 = _oracle_p99(_oracle_retained(through))
        exp_direct_p99 = _oracle_p99(_oracle_retained(direct))
        exp_delta = exp_through_p99 - exp_direct_p99

        ctx = f"seed={_SEED ^ 0x4:#x} i={i} through={through} direct={direct}"
        assert result.through_p99_ms == exp_through_p99, ctx
        assert result.direct_p99_ms == exp_direct_p99, ctx
        assert result.delta_p99_ms == exp_delta, ctx
        # The returned exclusion counts match the independent oracle too.
        assert result.excluded_through == _oracle_excluded_count(through), ctx
        assert result.excluded_direct == _oracle_excluded_count(direct), ctx


# Feature: edge-gw16b, Property 5: Absent direct baseline is an error, never an absolute latency
# Validates: Requirements 5.6
def test_property5_absent_direct_baseline_raises() -> None:
    """A direct path that is empty OR all-tail raises `DirectBaselineMissing`, yielding no delta."""
    rng = random.Random(_SEED ^ 0x5)
    for i in range(_ITERATIONS):
        # The through path is non-empty-after-trim so the ONLY defect is the missing baseline.
        through = _gen_nonempty_tail_free(rng)
        direct = () if rng.random() < 0.5 else _gen_all_tail(rng)
        paired = PairedPathSamples(
            through_firewall=PathSamples(through),
            direct=PathSamples(direct),
        )
        with pytest.raises(DirectBaselineMissing):
            firewall_added_delta(paired)
        # Independent oracle agrees the baseline has no tail-free sample.
        assert _oracle_retained(direct) == (), f"seed={_SEED ^ 0x5:#x} i={i} direct={direct}"


# Feature: edge-gw16b, Property 6: assert_within_slo honours the contract bound
# Validates: Requirements 5.4, 5.5
def test_property6_assert_within_slo_honours_contract_bound() -> None:
    """assert_within_slo raises iff delta_p99_ms > contract.target_p99_ms."""
    rng = random.Random(_SEED ^ 0x6)
    for i in range(_ITERATIONS):
        target_p99_ms = rng.uniform(5.0, 200.0)
        contract = _serviceable_contract(rng, target_p99_ms=target_p99_ms)
        # The contract may clamp/normalise target_p99_ms; read the authoritative bound back.
        bound = contract.target_p99_ms

        # Draw a delta that straddles the bound (below, exactly at, and above).
        delta_p99 = rng.uniform(bound - 50.0, bound + 50.0)
        if rng.random() < 0.2:
            delta_p99 = bound  # exercise the exact-boundary (<= bound must NOT raise)
        delta = FirewallDelta(
            delta_p99_ms=delta_p99,
            through_p99_ms=max(delta_p99, 0.0),
            direct_p99_ms=0.0,
            excluded_through=0,
            excluded_direct=0,
        )

        ctx = f"seed={_SEED ^ 0x6:#x} i={i} delta={delta_p99} bound={bound}"
        if delta_p99 > bound:
            with pytest.raises(ValueError, match="exceeds the SLO bound"):
                assert_within_slo(delta, contract)
        else:
            assert_within_slo(delta, contract) is None  # must not raise  # noqa: B015
            assert delta_p99 <= bound, ctx


# ==================================================================================================
# Task 1.5 — example / unit tests (plain asserts, NOT the property idiom)
# ==================================================================================================


def test_no_tail_set_exclusion_is_a_noop() -> None:
    """A sample set with no tail is returned unchanged with a zero exclusion count."""
    samples = PathSamples((1.0, 5.5, 12.0, 39.9))
    trimmed, excluded = exclude_lost_segment_tails(samples)
    assert trimmed.samples_ms == (1.0, 5.5, 12.0, 39.9)
    assert excluded == 0


def test_all_tail_set_trims_to_empty() -> None:
    """A set where every sample is a tail trims to empty with the full count excluded."""
    samples = PathSamples((40.0, 55.0, 120.0))
    trimmed, excluded = exclude_lost_segment_tails(samples)
    assert trimmed.samples_ms == ()
    assert excluded == 3


def test_exact_40ms_boundary_at_or_above_dropped_below_retained() -> None:
    """The exact 40.0 boundary is a tail (>= dropped); strictly-below is retained."""
    samples = PathSamples((39.999_999, 40.0, 40.000_001, 10.0))
    trimmed, excluded = exclude_lost_segment_tails(samples)
    assert trimmed.samples_ms == (39.999_999, 10.0)
    assert excluded == 2  # exactly-40.0 and just-above are both dropped
    assert all(ms < 40.0 for ms in trimmed.samples_ms)


def test_direct_baseline_missing_message_names_the_baseline() -> None:
    """An empty direct path raises `DirectBaselineMissing` naming the required baseline."""
    paired = PairedPathSamples(
        through_firewall=PathSamples((5.0, 6.0, 7.0)),
        direct=PathSamples(()),
    )
    with pytest.raises(DirectBaselineMissing) as excinfo:
        firewall_added_delta(paired)
    assert "Direct_Path baseline" in str(excinfo.value)


def test_direct_all_tail_also_raises_direct_baseline_missing() -> None:
    """A direct path that is all-tail (no tail-free baseline) raises `DirectBaselineMissing`."""
    paired = PairedPathSamples(
        through_firewall=PathSamples((5.0, 6.0)),
        direct=PathSamples((40.0, 90.0)),
    )
    with pytest.raises(DirectBaselineMissing):
        firewall_added_delta(paired)


def test_through_path_empty_is_measurement_error_not_a_bogus_delta() -> None:
    """An empty through path with a present baseline raises ValueError (not the baseline error)."""
    paired = PairedPathSamples(
        through_firewall=PathSamples(()),
        direct=PathSamples((5.0, 6.0, 7.0)),
    )
    with pytest.raises(ValueError) as excinfo:
        firewall_added_delta(paired)
    assert not isinstance(excinfo.value, DirectBaselineMissing)
    assert "through-firewall" in str(excinfo.value)


def test_through_path_all_tail_is_measurement_error() -> None:
    """A through path that is all-tail with a present baseline raises a measurement ValueError."""
    paired = PairedPathSamples(
        through_firewall=PathSamples((40.0, 100.0)),
        direct=PathSamples((5.0, 6.0, 7.0)),
    )
    with pytest.raises(ValueError) as excinfo:
        firewall_added_delta(paired)
    assert not isinstance(excinfo.value, DirectBaselineMissing)
    assert "through-firewall" in str(excinfo.value)


def test_delta_computed_from_pathsamples_reads_no_gateway_histogram() -> None:
    """Structural proof: the module exposes no metrics/exposition/histogram symbol or import.

    The delta is defined to be client-side only (R5.3): there must be no code path that reads the
    gateway's own request-latency histogram. Assert structurally that the module exports no such
    symbol, binds no such name, and IMPORTS no metrics/exposition module. (The module's docstrings
    deliberately NAME the histogram to document that it avoids it — the proof is about imports and
    code symbols, not prose, so this test parses the AST rather than grepping the text.)
    """
    import ast

    import gateway_v2.runtime.latency_slo as mod

    public = set(mod.__all__)
    assert public == {
        "DirectBaselineMissing",
        "FirewallDelta",
        "PairedPathSamples",
        "PathSamples",
        "assert_within_slo",
        "exclude_lost_segment_tails",
        "firewall_added_delta",
    }
    # No histogram/metrics/exposition symbol bound at module scope.
    forbidden = ("histogram", "exposition", "metrics", "registry", "scrape")
    for name in vars(mod):
        lowered = name.lower()
        assert not any(f in lowered for f in forbidden), f"forbidden symbol {name!r} in module"

    # Parse the source AST: no import pulls in a metrics/exposition/histogram module, and no
    # name/attribute REFERENCED in code (as opposed to docstrings/comments) is a histogram symbol.
    src = mod.__file__
    assert src is not None
    with open(src, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                low = alias.name.lower()
                assert not any(f in low for f in forbidden), f"forbidden import {alias.name!r}"
        elif isinstance(node, ast.ImportFrom):
            low = (node.module or "").lower()
            assert not any(f in low for f in forbidden), f"forbidden import-from {node.module!r}"
        elif isinstance(node, ast.Attribute):
            low = node.attr.lower()
            assert not any(f in low for f in forbidden), f"forbidden attribute {node.attr!r}"
        elif isinstance(node, ast.Name):
            low = node.id.lower()
            assert not any(f in low for f in forbidden), f"forbidden name {node.id!r}"


def test_assert_within_slo_passes_within_bound_and_raises_over_budget() -> None:
    """A within-bound delta passes; an over-budget delta raises naming the delta and the bound."""
    rng = random.Random(0xB5)
    contract = _serviceable_contract(rng, target_p99_ms=20.0)
    bound = contract.target_p99_ms

    within = FirewallDelta(
        delta_p99_ms=bound - 1.0,
        through_p99_ms=bound - 1.0,
        direct_p99_ms=0.0,
        excluded_through=0,
        excluded_direct=0,
    )
    assert assert_within_slo(within, contract) is None

    over = FirewallDelta(
        delta_p99_ms=bound + 5.0,
        through_p99_ms=bound + 5.0,
        direct_p99_ms=0.0,
        excluded_through=0,
        excluded_direct=0,
    )
    with pytest.raises(ValueError) as excinfo:
        assert_within_slo(over, contract)
    message = str(excinfo.value)
    assert str(over.delta_p99_ms) in message  # names the measured delta
    assert str(bound) in message  # names the bound


def test_delta_example_difference_of_tail_free_p99s() -> None:
    """A concrete end-to-end delta: tails trimmed on both paths, then p99 - p99."""
    through = PathSamples((1.0, 2.0, 3.0, 4.0, 200.0))  # 200.0 is a tail, trimmed
    direct = PathSamples((1.0, 1.0, 1.0, 1.0, 90.0))  # 90.0 is a tail, trimmed
    result = firewall_added_delta(
        PairedPathSamples(through_firewall=through, direct=direct),
    )
    # Independent oracle over the retained samples.
    exp_through = _oracle_p99((1.0, 2.0, 3.0, 4.0))
    exp_direct = _oracle_p99((1.0, 1.0, 1.0, 1.0))
    assert result.through_p99_ms == exp_through
    assert result.direct_p99_ms == exp_direct
    assert result.delta_p99_ms == exp_through - exp_direct
    assert result.excluded_through == 1
    assert result.excluded_direct == 1
