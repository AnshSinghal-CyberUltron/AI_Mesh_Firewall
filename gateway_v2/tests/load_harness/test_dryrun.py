# Feature: load-harness-r2-21
# Validates: Requirements 6.1, 6.2, 12.1, 12.2, 12.3, 12.4, 12.5
"""Local dry-run integration test: the full Poisson ladder against a booted gateway (task 10.2).

This is the Req 12 integration test the design's Testing Strategy calls for: drive one locally
booted ``gateway_v2`` end to end over the full open-loop Poisson rate ladder and assert the harness
produces (12.3) a realized arrival distribution matching the configured Poisson process, (12.4)
realized strata shares matching their configured shares, and (12.5) C4 numbers consistent with the
Req 8 client-side definition — plus the Req 6 secret-shaped share flowing through the REAL GW12b
holdback path with the honest holdback-mechanism-not-detector limit recorded.

ATTACH VARIANT — the design's PRIMARY (option (a)): the in-process ``httpx.ASGITransport`` against
``build_app(provider=scripted_stub, ...)`` (CI-cheap, deterministic, no socket), driven under
``asyncio.run``. The whole module ``pytest.importorskip``s ``httpx`` — the only extra the primary
variant needs — exactly as the GW15 conformance subset does, so a framework-free ``.[dev]`` tree
SKIPS cleanly at collection; with the ``conformance`` extra present (httpx) it RUNS. The
real-uvicorn loopback socket is the fidelity target the design names but it is NOT stood up here
(the deferred live gate); this subset needs only httpx, so uvicorn is intentionally not imported.

CONFIG CHOICE (why these numbers). The design flags the open-loop honesty tension: an aggressive
offered rate the in-process target cannot sustain registers ``Schedule_Drop``s, which correctly fail
the run's pass test (``highest_pass`` collapses to ``None``) — honest, but it means the generator,
not the server, was the bottleneck and the ladder knee is uninformative. So the config here is tuned
so the GENERATOR is NOT the bottleneck: a MODEST offered rate (the per-arrival inter-arrival gap is
far larger than one in-process ASGI round-trip) and a SMALL per-rate request count, over a short
``max_steps`` ladder, keep the whole run to a few seconds while leaving the deterministic
distribution / strata / C4 assertions clean. The strata enabled (long_prompt at a sanctioned 1%,
retry, secret_output, multi_key, tenant_scale) exercise the full strata path at modest populations.
The three Req 12 verifications and the secret-shaped share are DETERMINISTIC under the injected seed
and are the primary assertions; the ladder knee is treated as informational (the real knee search is
a Deferred_Cloud_Gate), but the config is picked to also yield a clean per-rate run (200 OK path,
finite C4 samples, zero drops) rather than a generator-bound one.
"""

from __future__ import annotations

import asyncio
import math
import random

import pytest

# The primary (and only) attach variant here is the in-process httpx.ASGITransport, so httpx is the
# one extra needed. A tree without the conformance extra skips the whole module cleanly at
# collection — the GW15 conformance subset idiom. (The real-uvicorn loopback variant is a deferred
# live gate and is not stood up here, so uvicorn is deliberately not importorskip'd.)
pytest.importorskip("httpx")

from gateway_v2.runtime.resources import load_contract  # noqa: E402
from load_harness.arrivals import build_schedule  # noqa: E402
from load_harness.config import (  # noqa: E402
    ArrivalMode,
    RunSpec,
    StrataConfig,
    default_ladder_config,
    default_tolerances,
)
from load_harness.dryrun import DryRunResult, run_dry_run_async  # noqa: E402
from load_harness.report import GpuBusy  # noqa: E402
from load_harness.request_plan import Populations, plan_requests  # noqa: E402
from load_harness.strata import keys as keys_stratum  # noqa: E402
from load_harness.strata import prompts as prompts_stratum  # noqa: E402
from load_harness.strata import retry as retry_stratum  # noqa: E402
from load_harness.strata import tenants as tenants_stratum  # noqa: E402

#: A MODEST offered rate (requests/second). The dry-run DISPATCHES open-loop — every request's task
#: is spawned up front and sleeps until its own ``scheduled_s`` — against the in-process
#: ASGITransport whose round-trip is sub-millisecond, so the generator keeps pace and the per-rate
#: wall time is ~ ``count / rate``. 800 req/s keeps a converging sample to ~2.5 s/rate.
_OFFERED_RATE_PER_S = 800.0

#: The per-rate request count. The dry-run's three Req 12 verifications are computed over ONE rate's
#: plan, so the sample must be large enough to converge within the (tight) config tolerances: the
#: Poisson mean within the 5% RELATIVE ``arrival_mean_relative`` (std of the mean ~ ``1/sqrt(n)``)
#: and each Bernoulli strata share within the 2% ABSOLUTE ``share_convergence`` (std ~
#: ``sqrt(p(1-p)/n)``; at p=0.25 that needs n in the low thousands for a 2-sigma margin inside 2%).
#: 2,000 gives arrival-mean std ~2.2% and share std ~1.0% — comfortably inside both tolerances —
#: while the ladder still runs in seconds (~2.5 s/rate at 800 req/s).
_COUNT = 2_000

#: A SHORT ladder for the local de-risk: 2 rates is enough to prove the ladder runs end to end
#: against the booted gateway (Req 12.2) and emits a per-rate report + C4 summary; the real
#: knee search is a Deferred_Cloud_Gate.
_MAX_STEPS = 2

#: A fixed seed so the whole run (arrivals + every stratum draw) is byte-reproducible (Req 13).
_SEED = 0x6B6


def _dry_run_spec() -> RunSpec:
    """A RunSpec enabling the strata to verify at modest populations/shares (Req 3-7, 12).

    long_prompt at the sanctioned 1% share, retry (base + a small aggressive share), secret_output
    at a positive share (so bytes flow through the real GW12b holdback path, Req 6.1), multi_key and
    tenant_scale enabled (identity cardinality). The default operational tolerances and ladder
    policy are used (the SLO pass bound is read from ``ResourceContract.target_p99_ms``, never
    declared here).
    """
    strata = StrataConfig(
        multi_key=True,
        long_prompt=True,
        long_prompt_share=0.01,
        retry=True,
        retry_share=0.20,
        aggressive_retry_share=0.05,
        secret_output=True,
        secret_output_share=0.25,
        tenant_scale=True,
    )
    return RunSpec(
        offered_rate_per_s=_OFFERED_RATE_PER_S,
        arrival_mode=ArrivalMode.POISSON,
        seed=_SEED,
        strata=strata,
        tolerances=default_tolerances(),
        ladder=default_ladder_config(),
    )


def _planned_requests(run_spec: RunSpec) -> tuple[object, ...]:
    """Build the first-rate planned-request tuple (its length sets the per-rate offered count).

    ``run_dry_run_async`` only reads ``len(planned)`` for the per-rate count and re-plans per rate
    internally against its own seeded rng/schedule; this builds a representative plan at the
    configured rate so the count is ``_COUNT``. The populations mirror the dry-run's own enabled
    strata floors.
    """
    rng = random.Random(run_spec.seed)
    schedule = build_schedule(
        mode=ArrivalMode.POISSON,
        offered_rate_per_s=run_spec.offered_rate_per_s,
        count=_COUNT,
        rng=rng,
        clock=lambda: 0.0,
    )
    populations = Populations(
        key_population=keys_stratum.build_key_population(2_000, 200),
        tenant_population=tenants_stratum.build_tenant_population(10_000),
        prompt_corpus=prompts_stratum.default_corpus(),
        retry_policy=retry_stratum.RetryPolicy(
            base_share=run_spec.strata.retry_share,
            aggressive_share=run_spec.strata.aggressive_retry_share,
        ),
    )
    return plan_requests(run_spec, schedule, rng, populations=populations)


def _run() -> DryRunResult:
    """Run the full Poisson ladder end to end against the booted gateway (Req 12.1, 12.2)."""
    run_spec = _dry_run_spec()
    planned = _planned_requests(run_spec)
    contract, _logs = load_contract()
    return asyncio.run(
        run_dry_run_async(run_spec, planned, contract=contract, max_steps=_MAX_STEPS)
    )


# --------------------------------------------------------------------------- #
# The one end-to-end dry-run, asserted across the Req 12 verifications + Req 6.
# A single booted-gateway run is shared by the assertions below via a module fixture so the
# (seconds-long) ladder is driven exactly once.
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def dry_run_result() -> DryRunResult:
    """The shared result of the one end-to-end dry-run against the booted gateway."""
    return _run()


def test_arrival_distribution_matches_poisson(dry_run_result: DryRunResult) -> None:
    """Req 12.3: the realized arrival distribution matches the configured Poisson process.

    The dry-run's own ``_verify_arrival`` compares the realized mean inter-arrival to
    ``1 / offered_rate`` within the configured RELATIVE tolerance; this asserts that verification
    passed and that the measured/expected means are both finite and close.
    """
    verification = dry_run_result.verification
    assert verification.arrival_matches_poisson, (
        "realized arrival distribution did not match the configured Poisson process: "
        f"realized_mean={verification.arrival_realized_mean_s:.6g} s "
        f"expected_mean={verification.arrival_expected_mean_s:.6g} s"
    )
    expected = 1.0 / _OFFERED_RATE_PER_S
    assert math.isfinite(verification.arrival_realized_mean_s)
    assert verification.arrival_expected_mean_s == pytest.approx(expected, rel=1e-9)


def test_strata_shares_match_config(dry_run_result: DryRunResult) -> None:
    """Req 12.4: the realized strata shares match their configured shares within tolerance.

    Asserts the dry-run's ``_verify_strata_shares`` passed and that every enabled share-bearing
    stratum (long_prompt, retry, secret_shaped) is present in the realized tuple — so the match is
    over the strata that were actually enabled, not an empty set.
    """
    verification = dry_run_result.verification
    assert verification.strata_shares_match, (
        f"realized strata shares out of tolerance: {verification.realized_strata_shares}"
    )
    realized_names = {name for name, _ in verification.realized_strata_shares}
    assert realized_names == {"long_prompt", "retry", "secret_shaped"}, (
        f"unexpected realized strata set: {realized_names}"
    )


def test_c4_consistent_with_req8(dry_run_result: DryRunResult) -> None:
    """Req 12.5: the C4 numbers obey the Req 8 client-side definition.

    The headline C4 p99 must be a non-negative finite value (a clean run) or ``+inf`` (errored
    streams), never negative/NaN, and the dry-run's own ``_verify_c4`` must have passed over the
    long/short finite sample sets.
    """
    verification = dry_run_result.verification
    assert verification.c4_consistent_with_req8, (
        f"C4 numbers inconsistent with the Req 8 definition: c4_p99_ms={verification.c4_p99_ms}"
    )
    assert not math.isnan(verification.c4_p99_ms)
    assert verification.c4_p99_ms >= 0.0


def test_ladder_produced_per_rate_reports(dry_run_result: DryRunResult) -> None:
    """Req 12.2 + Req 11: the ladder ran end to end, one report + C4 summary per rate.

    There is exactly one :class:`RunReport` and one :class:`C4Summary` per ladder rate (``len ==
    max_steps``), each report carries the explicit ``GpuBusy.UNAVAILABLE`` sentinel (Req 11.6 /
    15.2, no fabricated GPU number) and the realized distinct key/org/tenant counts drawn from the
    enabled identity strata (Req 3, 7). The ladder's step tuple also matches ``max_steps``.
    """
    assert len(dry_run_result.reports) == _MAX_STEPS
    assert len(dry_run_result.c4_summaries) == _MAX_STEPS
    assert len(dry_run_result.ladder.steps) == _MAX_STEPS
    for report in dry_run_result.reports:
        assert report.gpu_busy is GpuBusy.UNAVAILABLE
        assert report.gpu_busy.available is False
        # Identity strata were enabled, so distinct counts are drawn from the offered traffic and
        # bounded by the per-rate offered count.
        assert 0 < report.distinct_keys <= _COUNT
        assert 0 < report.distinct_orgs <= report.distinct_keys
        assert 0 < report.distinct_tenants <= _COUNT


def test_secret_shaped_share_through_real_holdback(dry_run_result: DryRunResult) -> None:
    """Req 6: realized secret-shaped share matches config + the honest holdback limit is recorded.

    The secret-shaped stratum scripts the provider to emit shape-matched bytes that flow through the
    REAL GW12b egress + bounded-holdback path; the realized share over the run must be within the
    configured ``secret_output_share`` by the share-convergence tolerance (Req 6.2), and the
    ``DryRunResult`` must carry the honest holdback-MECHANISM-not-detector note (Req 6 limit).
    """
    configured = _dry_run_spec().strata.secret_output_share
    tol = default_tolerances().share_convergence
    realized = dry_run_result.realized_secret_shaped_share
    assert abs(realized - configured) <= tol, (
        f"realized secret-shaped share {realized} not within {tol} of configured {configured}"
    )
    note = dry_run_result.holdback_note.lower()
    assert "mechanism" in note and "detector" in note, (
        f"holdback note missing the mechanism-not-detector limit: {dry_run_result.holdback_note!r}"
    )


def test_streams_went_through_the_booted_gateway(dry_run_result: DryRunResult) -> None:
    """Req 12.1/12.5: the streams actually traversed the booted gateway (the 200 OK path).

    A clean run through the booted gateway yields finite C4 samples (the scripted provider emits a
    real multi-chunk SSE stream the client times); an all-errored run would carry NO finite long or
    short sample and a ``+inf`` headline p99. Assert at least one finite C4 sample exists across the
    per-rate summaries and the headline p99 is finite — proof the 200 OK streaming path, not an
    all-errored path, was exercised.
    """
    total_finite = 0
    for summary in dry_run_result.c4_summaries:
        total_finite += len(summary.long_prompt.samples_ms) + len(summary.short_prompt.samples_ms)
        assert math.isfinite(summary.c4_p99_ms), (
            "headline C4 p99 is +inf — the run errored every stream rather than reaching the "
            "booted gateway's 200 OK streaming path"
        )
    assert total_finite > 0, "no finite C4 samples — no stream reached the booted gateway"
