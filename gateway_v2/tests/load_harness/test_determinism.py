"""Determinism tests for the load-harness generator (B6 / R2-21), task 4.10.

Covers the pure generation spine only (no I/O, no store, no event loop): the open-loop arrival
schedule (``gateway_v2.load_harness.arrivals.build_schedule`` /
:class:`~load_harness.arrivals.ArrivalSchedule`) and the request-plan composer
(``gateway_v2.load_harness.request_plan.plan_requests`` /
:class:`~load_harness.request_plan.PlannedRequest`), layered over the five pure strata generators
(``strata.keys`` / ``strata.tenants`` / ``strata.prompts`` / ``strata.retry`` /
``strata.secret_output``) and the ``config`` run spec.

* **Property 10 — determinism: same seed + clock ⇒ identical schedule + strata** (task 4.10): for
  any seed and any injected clock, two INDEPENDENT runs of the generator — each with a freshly
  seeded ``random.Random(seed)`` and the same fixed injected clock — produce an identical
  ``ArrivalSchedule`` and an identical tuple of ``PlannedRequest`` strata assignments. Equality is
  by VALUE: every harness value type is a frozen (``@dataclass(frozen=True, slots=True)``)
  dataclass, so ``==`` compares field-by-field (recursively through the nested ``Arrival``,
  ``KeyAssignment``, ``TenantAssignment``, ``PromptPick``, ``SecretOutputPlan`` values), and two
  byte-identical generations compare equal. Driven over many seeds × varied ``RunSpec`` (varied
  enabled strata, shares, and arrival mode) for >= 10,000 total iterations.
* **Negative control** (sanity, not a hard invariant): a DIFFERENT seed generally yields a
  different schedule/plan. This guards against a trivially-constant generator that would make the
  determinism assertions pass vacuously. It only asserts that inequality holds for AT LEAST ONE
  differing seed across the sweep, since a specific seed pair could coincide.

House idiom: seeded ``random.Random`` loops of >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. Every generation path is a pure function of ``(run_spec,
rng, clock, populations)`` keyed by the injected ``random.Random`` and injected ``Clock``, so the
machine is deterministic and offline-reproducible (no running server). Populations are id-only
(cheap) so building 10k+ schedules + plans is fast. Test files are not under the import-linter
layer contract.
"""

from __future__ import annotations

import random

from load_harness import request_plan
from load_harness.arrivals import ArrivalSchedule, build_schedule
from load_harness.config import (
    ArrivalMode,
    RunSpec,
    StrataConfig,
    default_ladder_config,
    default_tolerances,
)
from load_harness.request_plan import Populations
from load_harness.strata import keys as keys_stratum
from load_harness.strata import prompts as prompts_stratum
from load_harness.strata import retry as retry_stratum
from load_harness.strata import tenants as tenants_stratum

# >= 10,000 total iterations. Each iteration builds TWO schedules and TWO plan tuples from scratch
# with small arrival counts and id-only populations, so the whole sweep is fast.
_ITERATIONS = 12_000

# A fixed injected clock: a constant start reference. Determinism holds for the same clock, and a
# constant clock makes the two independent runs share an identical start_s by construction (the
# point of the property is the RNG-driven draws being reproducible under that fixed clock).
_FIXED_START_S = 1_234.5


def _fixed_clock() -> float:
    """The injected clock used throughout — a constant start reference (never wall-clock)."""
    return _FIXED_START_S


# Small arrival counts keep each of the 12k iterations cheap while still exercising a multi-draw
# sequence across every enabled stratum.
_MIN_COUNT = 0
_MAX_COUNT = 12

# The sanctioned long-prompt shares (mirrors config); any is valid once the stratum is enabled.
_LONG_SHARES = (0.01, 0.05, 0.25)


def _build_run_spec(rng: random.Random, seed: int) -> RunSpec:
    """A varied ``RunSpec`` for this iteration: random enabled strata + shares + arrival mode.

    Varying which strata are enabled, their shares, and the arrival mode across iterations exercises
    the determinism contract over the whole configured input space (not just one fixed config). The
    ``RunSpec`` itself is a frozen value; ``seed`` is carried on it so the generation boundary can
    rebuild a fresh ``random.Random(seed)`` for each independent run.
    """
    mode = ArrivalMode.POISSON if rng.random() < 0.5 else ArrivalMode.CONSTANT
    strata = StrataConfig(
        multi_key=rng.random() < 0.5,
        long_prompt=rng.random() < 0.5,
        long_prompt_share=rng.choice(_LONG_SHARES),
        retry=rng.random() < 0.5,
        retry_share=rng.random(),
        aggressive_retry_share=rng.random() * 0.3,
        secret_output=rng.random() < 0.5,
        secret_output_share=rng.random(),
        tenant_scale=rng.random() < 0.5,
    )
    return RunSpec(
        offered_rate_per_s=rng.uniform(1.0, 500.0),
        arrival_mode=mode,
        seed=seed,
        strata=strata,
        tolerances=default_tolerances(),
        ladder=default_ladder_config(),
    )


def _build_populations(strata: StrataConfig) -> Populations:
    """A pre-built id-only ``Populations`` bundle: a member is set iff its stratum is enabled.

    Key/tenant populations use the exact GW20b floors (≥ 2,000 keys / ≥ 200 orgs; ≥ 10,000 tenants)
    via the hard builders — id-only, so cheap to construct 12k times. A disabled stratum's member is
    ``None`` (the composer reads a member only when the corresponding flag is set). The prompt
    corpus / retry policy are tiny frozen values.
    """
    key_population = (
        keys_stratum.build_key_population(2_000, 200) if strata.multi_key else None
    )
    tenant_population = (
        tenants_stratum.build_tenant_population(10_000) if strata.tenant_scale else None
    )
    prompt_corpus = prompts_stratum.default_corpus() if strata.long_prompt else None
    retry_policy = (
        retry_stratum.RetryPolicy(
            base_share=strata.retry_share,
            aggressive_share=strata.aggressive_retry_share,
        )
        if strata.retry
        else None
    )
    return Populations(
        key_population=key_population,
        tenant_population=tenant_population,
        prompt_corpus=prompt_corpus,
        retry_policy=retry_policy,
    )


def _generate(
    run_spec: RunSpec, count: int, populations: Populations
) -> tuple[ArrivalSchedule, tuple[request_plan.PlannedRequest, ...]]:
    """One INDEPENDENT generation run: fresh ``random.Random(seed)`` + the fixed clock.

    Builds the arrival schedule, then composes the planned requests over that schedule, threading a
    single freshly-seeded ``random.Random`` so the whole run is one reproducible draw sequence.
    """
    rng = random.Random(run_spec.seed)
    schedule = build_schedule(
        mode=run_spec.arrival_mode,
        offered_rate_per_s=run_spec.offered_rate_per_s,
        count=count,
        rng=rng,
        clock=_fixed_clock,
    )
    plans = request_plan.plan_requests(
        run_spec, schedule, rng, populations=populations
    )
    return schedule, plans


# --------------------------------------------------------------------------- #
# Task 4.10 — Property 10: determinism — same seed + clock ⇒ identical schedule + strata
# Feature: load-harness-r2-21, Property 10: determinism same seed and clock yields identical
#   schedule and strata
# Validates: Requirements 13.1, 13.2, 13.3
# --------------------------------------------------------------------------- #


def test_property10_determinism_same_seed_and_clock_yields_identical_schedule_and_strata() -> None:
    """Two independent runs with the same seed + clock produce identical schedule + plans.

    For many seeds and varied ``RunSpec`` (enabled strata, shares, arrival mode): generating the
    arrival schedule and the planned-request strata assignments twice — each from a fresh
    ``random.Random(seed)`` and the same fixed injected clock — yields byte-identical results.
    Equality is checked by VALUE (``==``): the frozen+slots dataclasses compare field-by-field
    through their nested frozen strata values, so equal values prove identical generation.

    A per-iteration negative control records whether a DIFFERENT seed changed the output; the sweep
    asserts afterward that this happened at least once, so the determinism assertions cannot pass
    vacuously on a trivially-constant generator.
    """
    seed = 0x10_DE
    rng = random.Random(seed)
    saw_difference = False

    for i in range(_ITERATIONS):
        iter_seed = rng.randrange(1, 2**31)
        run_spec = _build_run_spec(rng, iter_seed)
        count = rng.randint(_MIN_COUNT, _MAX_COUNT)
        populations = _build_populations(run_spec.strata)

        schedule_a, plans_a = _generate(run_spec, count, populations)
        schedule_b, plans_b = _generate(run_spec, count, populations)

        # Property 10: same seed + same clock + same config ⇒ identical schedule (by value).
        assert schedule_a == schedule_b, (
            f"seed={seed:#x} iter={i} iter_seed={iter_seed} count={count}: "
            f"ArrivalSchedule differed across independent runs with identical seed+clock"
        )
        # ... and identical planned-request strata assignments (by value).
        assert plans_a == plans_b, (
            f"seed={seed:#x} iter={i} iter_seed={iter_seed} count={count}: "
            f"PlannedRequest tuple differed across independent runs with identical seed+clock"
        )
        # Length sanity: one planned request per arrival.
        assert len(plans_a) == count, (
            f"seed={seed:#x} iter={i}: expected {count} planned requests, got {len(plans_a)}"
        )

        # Negative control (sanity): a DIFFERENT seed usually changes the output. Only record it;
        # a specific seed pair could coincide, so the hard assertion is on the whole-sweep OR below.
        if count > 0:
            other_spec = RunSpec(
                offered_rate_per_s=run_spec.offered_rate_per_s,
                arrival_mode=run_spec.arrival_mode,
                seed=(iter_seed % (2**31 - 1)) + 1,
                strata=run_spec.strata,
                tolerances=run_spec.tolerances,
                ladder=run_spec.ladder,
            )
            schedule_c, plans_c = _generate(other_spec, count, populations)
            if schedule_c != schedule_a or plans_c != plans_a:
                saw_difference = True

    # Guard against a trivially-constant generator: across the whole sweep, at least one differing
    # seed must have produced a different schedule/plan. (Not a per-iteration invariant.)
    assert saw_difference, (
        f"seed={seed:#x}: no differing seed ever changed the output across {_ITERATIONS} "
        f"iterations — the generator may be trivially constant, making determinism vacuous"
    )


# --------------------------------------------------------------------------- #
# Task 4.10 — representative determinism examples (not PBT)
# Feature: load-harness-r2-21, Property 10: determinism same seed and clock yields identical
#   schedule and strata
# Validates: Requirements 13.1, 13.2, 13.3
# --------------------------------------------------------------------------- #


def _all_strata_run_spec(seed: int) -> RunSpec:
    """A ``RunSpec`` with every stratum enabled, used by the all-strata example."""
    return RunSpec(
        offered_rate_per_s=100.0,
        arrival_mode=ArrivalMode.POISSON,
        seed=seed,
        strata=StrataConfig(
            multi_key=True,
            long_prompt=True,
            long_prompt_share=0.05,
            retry=True,
            retry_share=0.3,
            aggressive_retry_share=0.1,
            secret_output=True,
            secret_output_share=0.25,
            tenant_scale=True,
        ),
        tolerances=default_tolerances(),
        ladder=default_ladder_config(),
    )


def test_all_strata_enabled_is_deterministic_by_value() -> None:
    """With every stratum enabled, two independent runs are equal by frozen-dataclass value.

    Exercises the full fixed draw order (keys → tenants → prompt → retry → secret) over a shared
    ``rng`` and confirms the nested strata values on each ``PlannedRequest`` compare equal.
    """
    run_spec = _all_strata_run_spec(0xABCD)
    populations = _build_populations(run_spec.strata)
    schedule_a, plans_a = _generate(run_spec, 8, populations)
    schedule_b, plans_b = _generate(run_spec, 8, populations)

    assert schedule_a == schedule_b
    assert plans_a == plans_b
    assert len(plans_a) == 8
    # Every stratum field is populated (all enabled), proving the nested values round-trip equal.
    first = plans_a[0]
    assert first.key_assignment is not None
    assert first.tenant_assignment is not None
    assert first.prompt_pick is not None
    assert first.client_is_retrying is not None
    assert first.secret_output_plan is not None


def test_empty_schedule_is_deterministic_and_empty() -> None:
    """A zero-count run yields an empty schedule and an empty plan tuple, identically twice."""
    run_spec = _all_strata_run_spec(0x7)
    populations = _build_populations(run_spec.strata)
    schedule_a, plans_a = _generate(run_spec, 0, populations)
    schedule_b, plans_b = _generate(run_spec, 0, populations)
    assert schedule_a == schedule_b
    assert plans_a == plans_b == ()
    assert schedule_a.arrivals == ()


def test_disabled_strata_leave_fields_none_deterministically() -> None:
    """A no-strata run leaves every per-request stratum field ``None``, identically twice.

    A disabled stratum consumes ZERO draws (determinism-spine contract), so the whole plan is driven
    only by the arrival schedule and remains reproducible.
    """
    run_spec = RunSpec(
        offered_rate_per_s=50.0,
        arrival_mode=ArrivalMode.CONSTANT,
        seed=0x33,
        strata=StrataConfig(
            multi_key=False,
            long_prompt=False,
            long_prompt_share=0.0,
            retry=False,
            retry_share=0.0,
            aggressive_retry_share=0.0,
            secret_output=False,
            secret_output_share=0.0,
            tenant_scale=False,
        ),
        tolerances=default_tolerances(),
        ladder=default_ladder_config(),
    )
    populations = Populations(
        key_population=None,
        tenant_population=None,
        prompt_corpus=None,
        retry_policy=None,
    )
    _schedule_a, plans_a = _generate(run_spec, 5, populations)
    _schedule_b, plans_b = _generate(run_spec, 5, populations)
    assert plans_a == plans_b
    for planned in plans_a:
        assert planned.key_assignment is None
        assert planned.tenant_assignment is None
        assert planned.prompt_pick is None
        assert planned.client_is_retrying is None
        assert planned.secret_output_plan is None
