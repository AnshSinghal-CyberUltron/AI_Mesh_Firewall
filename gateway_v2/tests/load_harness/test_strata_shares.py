"""Strata-share convergence property test (B6 / R2-21), task 4.7.

Covers the pure strata share-draw path (no I/O, no server, no event loop) along BOTH the direct
stratum functions and the ``request_plan.py`` composer:

* **Property 4 — strata shares converge to configured shares** (task 4.7): for any seed and any
  configured strata shares, the REALIZED shares over a run converge to the configured shares within
  the declared tolerance ``config.default_tolerances().share_convergence`` (read from config, never
  hardcoded). Three strata carry a configured share:

  - **long-prompt** at each of the sanctioned 1% / 5% / 25% shares — the realized long fraction
    (``PromptPick.is_long``) matches the configured share within tolerance, exercised BOTH directly
    via ``prompts.pick`` and THROUGH the composer ``request_plan.plan_requests`` (validating the
    composed path, Req 12.4);
  - **retry** — the realized retrying-client fraction (``retry.client_retries`` draws against
    ``RetryPolicy.combined_share``) matches the combined share within tolerance, exercised directly
    and through the composer (``PlannedRequest.client_is_retrying``);
  - **secret-shaped** — ``secret_output.realized_share(plans)`` matches the configured share within
    tolerance, exercised directly via ``secret_output.plan`` and through the composer
    (``PlannedRequest.secret_output_plan``).

House idiom: seeded ``random.Random`` loops totalling >= 10,000 iterations with the seed logged in
the assertion message; NO ``hypothesis``. Every draw is a pure function keyed by the injected
``random.Random``, so the machine is deterministic and offline-reproducible. Each configured run
uses a large per-config sample so the Bernoulli mean converges inside the declared tolerance. Test
files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

from load_harness.arrivals import build_schedule
from load_harness.config import (
    ArrivalMode,
    LadderConfig,
    RunSpec,
    StrataConfig,
    Tolerances,
    default_ladder_config,
    default_tolerances,
)
from load_harness.request_plan import Populations, plan_requests
from load_harness.strata import keys as keys_stratum
from load_harness.strata import prompts as prompts_stratum
from load_harness.strata import retry as retry_stratum
from load_harness.strata import secret_output as secret_output_stratum
from load_harness.strata import tenants as tenants_stratum

#: The declared share-convergence tolerance, read from config (never hardcoded here).
_TOLERANCE = default_tolerances().share_convergence

#: Per-config sample size. The standard error of a Bernoulli(p) mean over ``n`` draws is
#: ``sqrt(p(1-p)/n)``; worst case (p = 0.5) at ``n = 50,000`` is ~0.0022, comfortably inside the
#: 0.02 ``share_convergence`` tolerance with wide margin across many seeds/configs.
_SAMPLE = 50_000

#: Number of distinct seed/config outer iterations per sweep. Each drives ``_SAMPLE`` draws, so the
#: total iteration count is far above the 10,000-iteration house-idiom floor.
_OUTER = 24

#: The three GW20b-sanctioned long-prompt shares (Req 4.1); mirrors ``config._LONG_PROMPT_SHARES``.
_SANCTIONED_LONG_SHARES = (0.01, 0.05, 0.25)

#: A fixed injected clock start reference for the composed-path schedules (the share statistic is
#: independent of the absolute offset).
_CLOCK_START_S = 1_000.0


def _within(realized: float, configured: float) -> bool:
    """True iff the realized share is within the declared absolute tolerance of the configured."""
    return abs(realized - configured) <= _TOLERANCE


# --------------------------------------------------------------------------- #
# Task 4.7 — Property 4: strata shares converge to configured shares (DIRECT paths)
# Feature: load-harness-r2-21, Property 4: strata shares converge to configured shares
# Validates: Requirements 4.2, 5.3, 6.2, 12.4
# --------------------------------------------------------------------------- #


def test_property4_long_prompt_share_converges_direct() -> None:
    """``prompts.pick`` realizes each sanctioned long share within tolerance (Req 4.2)."""
    seed = 0x04_71
    rng = random.Random(seed)
    corpus = prompts_stratum.default_corpus()
    for i in range(_OUTER):
        share = _SANCTIONED_LONG_SHARES[i % len(_SANCTIONED_LONG_SHARES)]
        long_count = sum(
            1 for _ in range(_SAMPLE) if prompts_stratum.pick(corpus, share, rng).is_long
        )
        realized = long_count / _SAMPLE
        assert _within(realized, share), (
            f"seed={seed:#x} iter={i} long share realized={realized:.5f} "
            f"configured={share} tol={_TOLERANCE}"
        )


def test_property4_retry_combined_share_converges_direct() -> None:
    """``retry.client_retries`` realizes ``RetryPolicy.combined_share`` in tolerance (Req 5.3)."""
    seed = 0x04_72
    rng = random.Random(seed)
    for i in range(_OUTER):
        base = rng.uniform(0.0, 0.6)
        aggressive = rng.uniform(0.0, 0.4)
        policy = retry_stratum.RetryPolicy(base_share=base, aggressive_share=aggressive)
        combined = policy.combined_share
        retry_count = sum(
            1 for _ in range(_SAMPLE) if retry_stratum.client_retries(policy, rng)
        )
        realized = retry_count / _SAMPLE
        assert _within(realized, combined), (
            f"seed={seed:#x} iter={i} retry share realized={realized:.5f} "
            f"combined={combined:.5f} tol={_TOLERANCE}"
        )


def test_property4_secret_shaped_share_converges_direct() -> None:
    """``secret_output.realized_share`` matches the configured secret share in tolerance (6.2)."""
    seed = 0x04_73
    rng = random.Random(seed)
    for i in range(_OUTER):
        share = rng.uniform(0.0, 1.0)
        plans = [secret_output_stratum.plan(share, rng) for _ in range(_SAMPLE)]
        realized = secret_output_stratum.realized_share(plans)
        assert _within(realized, share), (
            f"seed={seed:#x} iter={i} secret share realized={realized:.5f} "
            f"configured={share} tol={_TOLERANCE}"
        )


# --------------------------------------------------------------------------- #
# Task 4.7 — Property 4: strata shares converge (COMPOSED path, request_plan)
# Feature: load-harness-r2-21, Property 4: strata shares converge to configured shares
# Validates: Requirements 4.2, 5.3, 6.2, 12.4
# --------------------------------------------------------------------------- #


def _run_spec(
    *,
    seed: int,
    long_share: float,
    retry_share: float,
    aggressive_share: float,
    secret_share: float,
) -> RunSpec:
    """A ``RunSpec`` enabling the three share-bearing strata (and multi-key/tenant for realism).

    Enabling multi-key and tenant-scale as well drives the composer's full fixed draw order
    (keys → tenants → prompt → retry → secret) so the share draws are exercised exactly as a real
    composed run consumes them from the shared ``rng``.
    """
    strata = StrataConfig(
        multi_key=True,
        long_prompt=True,
        long_prompt_share=long_share,
        retry=True,
        retry_share=retry_share,
        aggressive_retry_share=aggressive_share,
        secret_output=True,
        secret_output_share=secret_share,
        tenant_scale=True,
    )
    return RunSpec(
        offered_rate_per_s=100.0,
        arrival_mode=ArrivalMode.POISSON,
        seed=seed,
        strata=strata,
        tolerances=default_tolerances(),
        ladder=default_ladder_config(),
    )


def _populations() -> Populations:
    """A ``Populations`` bundle with at-floor key/tenant populations + default corpus/policy.

    The retry policy here is a placeholder; each composed sweep rebuilds the bundle's policy with
    the configured shares (see the test). Keeping the key/tenant populations at their floors keeps
    the draws cheap.
    """
    return Populations(
        key_population=keys_stratum.build_key_population(
            keys_stratum._MIN_KEYS, keys_stratum._MIN_ORGS
        ),
        tenant_population=tenants_stratum.build_tenant_population(tenants_stratum._MIN_TENANTS),
        prompt_corpus=prompts_stratum.default_corpus(),
        retry_policy=retry_stratum.RetryPolicy(base_share=0.0, aggressive_share=0.0),
    )


def test_property4_shares_converge_through_composer() -> None:
    """All three share-bearing strata converge when driven THROUGH ``plan_requests`` (Req 12.4).

    Build a ``RunSpec`` enabling the strata + a ``Populations`` bundle, generate a Poisson schedule
    of ``_SAMPLE`` arrivals, compose the ``PlannedRequest`` tuple, and assert the realized
    long-prompt / retrying-client / secret-shaped shares each match their configured share within
    the declared tolerance — validating the composed path (Req 12.4) as well as the raw draws.
    """
    seed = 0x04_7C
    outer_rng = random.Random(seed)
    key_pop = keys_stratum.build_key_population(keys_stratum._MIN_KEYS, keys_stratum._MIN_ORGS)
    tenant_pop = tenants_stratum.build_tenant_population(tenants_stratum._MIN_TENANTS)
    corpus = prompts_stratum.default_corpus()
    for i in range(_OUTER):
        long_share = _SANCTIONED_LONG_SHARES[i % len(_SANCTIONED_LONG_SHARES)]
        base = outer_rng.uniform(0.0, 0.6)
        aggressive = outer_rng.uniform(0.0, 0.4)
        secret_share = outer_rng.uniform(0.0, 1.0)
        policy = retry_stratum.RetryPolicy(base_share=base, aggressive_share=aggressive)
        combined = policy.combined_share

        run_spec = _run_spec(
            seed=seed + i,
            long_share=long_share,
            retry_share=base,
            aggressive_share=aggressive,
            secret_share=secret_share,
        )
        populations = Populations(
            key_population=key_pop,
            tenant_population=tenant_pop,
            prompt_corpus=corpus,
            retry_policy=policy,
        )
        plan_rng = random.Random(seed + i)
        schedule = build_schedule(
            mode=ArrivalMode.POISSON,
            offered_rate_per_s=run_spec.offered_rate_per_s,
            count=_SAMPLE,
            rng=plan_rng,
            clock=lambda: _CLOCK_START_S,
        )
        planned = plan_requests(run_spec, schedule, plan_rng, populations=populations)
        assert len(planned) == _SAMPLE

        long_realized = sum(
            1 for p in planned if p.prompt_pick is not None and p.prompt_pick.is_long
        ) / _SAMPLE
        retry_realized = sum(
            1 for p in planned if p.client_is_retrying
        ) / _SAMPLE
        secret_realized = secret_output_stratum.realized_share(
            p.secret_output_plan
            for p in planned
            if p.secret_output_plan is not None
        )

        assert _within(long_realized, long_share), (
            f"seed={seed:#x} iter={i} composed long share realized={long_realized:.5f} "
            f"configured={long_share} tol={_TOLERANCE}"
        )
        assert _within(retry_realized, combined), (
            f"seed={seed:#x} iter={i} composed retry share realized={retry_realized:.5f} "
            f"combined={combined:.5f} tol={_TOLERANCE}"
        )
        assert _within(secret_realized, secret_share), (
            f"seed={seed:#x} iter={i} composed secret share realized={secret_realized:.5f} "
            f"configured={secret_share} tol={_TOLERANCE}"
        )


def test_property4_total_iterations_cover_the_input_space() -> None:
    """Each sweep drives >= 10,000 total share draws (house idiom guard).

    Four sweeps (three direct + one composed) each run ``_OUTER`` configs of ``_SAMPLE`` draws, far
    above the 10,000-iteration floor; this guards the constants against being lowered.
    """
    assert _OUTER * _SAMPLE >= 10_000, (
        f"total share draws {_OUTER * _SAMPLE} < 10,000 house-idiom floor"
    )
    # Reference the imported value types so the house-convention imports stay meaningful.
    assert issubclass(Tolerances, object) and issubclass(LadderConfig, object)
