"""Local dry-run orchestration for the load harness (B6 / R2-21, task 10.1).

The dry-run is the harness's one end-to-end integration against a *real* gateway: it boots the ASGI
application assembled by B1's :func:`gateway_v2.edge.app.build_app`, drives the full open-loop
Poisson rate ladder against it over the **non-TLS** path (Req 15.3) with a **scripted**
:class:`~gateway_v2.dispatch.provider.StubProviderClient` (socket-free, deterministic), collects
client-side C4, and verifies that the realized arrival distribution matches the configured Poisson
process (Req 12.3), the realized strata shares match config (Req 12.4), and the C4 numbers are
consistent with the Req 8 client-side definition (Req 12.5). The run de-risks the eventual
fleet-scale one-shot: a correct distribution / strata / C4 proven locally first.

TOPOLOGY — design OPTION (a): scripted StubProviderClient, in-process ASGITransport (primary). The
gateway is booted with the shipped honest-boot fail-closed defaults EXCEPT a *scripted* provider is
injected through the same :func:`build_app` ``provider=`` keyword, so a non-empty deterministic SSE
stream flows and the client-visible serving path the harness measures (admission, input scan, the
SSE egress pipeline + bounded holdback, connection handling, and client-side C4) is fully exercised.
The boot + attach concern — the passing lifecycle deps, the scripted provider/stub, the httpx
``StreamClient`` seam, and the ASGITransport attach — lives in the sibling
:mod:`load_harness._dryrun_app` (split out to keep both modules under the ≤ 800-line size
discipline). The design offers two attach VARIANTS behind the ONE ``driver.StreamClient`` seam: the
in-process ``httpx.ASGITransport`` (the PRIMARY here — CI-cheap, deterministic, no socket, the GW15
conformance pattern) and the real-uvicorn loopback socket (B3's ``serve.py``, the fidelity target,
``importorskip``ed by task 10.2); the seam is variant-agnostic so the loopback variant drops in by
constructing the client against a base URL instead of a transport.

httpx is the ``conformance`` extra — imported LAZILY inside :mod:`load_harness._dryrun_app`
(``attach_asgi``), NEVER at module load. ``import load_harness.dryrun`` therefore succeeds on a tree
WITHOUT httpx; a call into the attach path on a tree missing httpx raises a clear ``install
gateway-v2[conformance]`` hint via :class:`~load_harness._dryrun_app.DryRunDependencyMissing`.

SECRET-SHAPED STRATUM (Req 6, honest limit). For the configured secret-shaped share, the scripted
provider's :class:`~gateway_v2.dispatch.provider.UpstreamEvent` ``text_deltas`` carry secret-SHAPED
bytes drawn from :data:`~load_harness.strata.secret_output.SHAPE_TEMPLATES`, so those bytes flow
through the REAL egress + GW12b bounded-holdback path. The dry-run records the realized
secret-shaped share and records in :class:`DryRunResult` the honest note that this drives the
holdback MECHANISM (keyed on output-byte shape), NOT the GW07/GW08 detector — classification is a
deferred seam, exactly as ``strata/secret_output.py`` documents.

PASS BOUND + house posture (Req 9.3, 14.2, 14.3). The ladder's SLO pass bound is read from
``gateway_v2.runtime.resources.ResourceContract.target_p99_ms`` (the one capacity authority) — this
module declares NO SLO/capacity-position literal and holds no module-level mutable. The one
``DryRunResult`` + ``VerificationOutcomes`` value types are ``@dataclass(frozen=True, slots=True)``;
helpers stay ≤ 120 lines. The allowed import edge is DOWNWARD into ``gateway_v2`` (via the sibling
boot module + ``runtime`` for the contract); ``gateway_v2`` never imports ``load_harness``, so
``lint-imports`` stays contracts-kept (load_harness is OUTSIDE ``root_packages=["gateway_v2"]``, so
the ``layers``/``forbidden`` contracts do not govern this edge).
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from gateway_v2.runtime.resources import ResourceContract, load_contract
from load_harness._dryrun_app import (
    AsgiStreamClient,
    DryRunDependencyMissing,
    attach_asgi,
    build_dry_run_app,
    build_provider,
)
from load_harness.arrivals import build_schedule
from load_harness.c4 import C4Summary, summarize
from load_harness.config import ArrivalMode, RunSpec
from load_harness.driver import OfferedRun, measure_headroom, run_offered
from load_harness.ladder import LadderResult, RunOutcome, evaluate_ladder, next_rate
from load_harness.report import GpuBusy, ReasonMix, RunReport
from load_harness.request_plan import PlannedRequest, Populations, plan_requests
from load_harness.strata import keys as keys_stratum
from load_harness.strata import prompts as prompts_stratum
from load_harness.strata import retry as retry_stratum
from load_harness.strata import tenants as tenants_stratum

__all__ = (
    "DryRunDependencyMissing",
    "DryRunResult",
    "VerificationOutcomes",
    "attach_asgi",
    "build_dry_run_app",
    "run_dry_run",
    "run_dry_run_async",
)

_LOG = logging.getLogger("amf.load_harness.dryrun")


def _zero_clock() -> float:
    """A zero-origin clock for schedule generation — ``scheduled_s`` are relative offsets.

    The schedule's absolute start reference is taken as ``0.0`` so each arrival's ``scheduled_s`` is
    just the accumulated inter-arrival gaps; the driver supplies the real monotonic/perf clock at
    dispatch time against its own captured origin, so the two share one relative timeline.
    """
    return 0.0

#: The honest holdback-mechanism-not-detector note recorded in every DryRunResult (Req 6 limit).
_HOLDBACK_NOTE = (
    "secret-shaped stratum drives the GW12b holdback MECHANISM (keyed on output-byte shape via "
    "scripted stub bytes), NOT the GW07/GW08 detector; detector-driven secret classification and a "
    "real-upstream secret-generation socket are a deferred seam (strata/secret_output.py)"
)


@dataclass(frozen=True, slots=True)
class VerificationOutcomes:
    """The three Req 12 dry-run verifications, each a pass/fail + the measured vs expected pair.

    ``arrival_matches_poisson`` is Req 12.3 (realized mean inter-arrival within the configured
    relative tolerance of ``1/offered_rate``); ``strata_shares_match`` is Req 12.4 (each enabled
    stratum's realized share within the absolute share-convergence tolerance of config);
    ``c4_consistent_with_req8`` is Req 12.5 (the C4 numbers obey the Req 8 client-side definition —
    finite p99 over all offered streams, errored streams at ``+inf``). The measured/expected floats
    are carried so the integration test (task 10.2) and the operator see WHY, not just the boolean.
    Frozen + slots (Req 14.3).
    """

    arrival_matches_poisson: bool
    arrival_realized_mean_s: float
    arrival_expected_mean_s: float
    strata_shares_match: bool
    realized_strata_shares: tuple[tuple[str, float], ...]
    c4_consistent_with_req8: bool
    c4_p99_ms: float

    @property
    def all_passed(self) -> bool:
        """True iff all three Req 12 verifications passed."""
        return (
            self.arrival_matches_poisson
            and self.strata_shares_match
            and self.c4_consistent_with_req8
        )


@dataclass(frozen=True, slots=True)
class DryRunResult:
    """The full result of a local dry-run (frozen + slots, Req 12).

    ``ladder`` is the knee decision over the Poisson rate ladder run end to end against the booted
    gateway (Req 12.2); ``reports`` / ``c4_summaries`` are the per-rate :class:`RunReport` /
    :class:`C4Summary` for every ladder rate (ordered by offered rate); ``verification`` carries the
    three Req 12 outcomes (arrival-matches-Poisson 12.3, strata-shares-match 12.4,
    C4-consistent-with-Req-8 12.5); ``realized_secret_shaped_share`` is the measured share of
    streams that produced secret-shaped output (Req 6.2); ``holdback_note`` is the honest
    holdback-mechanism-not-detector limit (Req 6). Frozen + slots (Req 14.3) — an immutable record.
    """

    ladder: LadderResult
    reports: tuple[RunReport, ...]
    c4_summaries: tuple[C4Summary, ...]
    verification: VerificationOutcomes
    realized_secret_shaped_share: float
    holdback_note: str


# ---- Populations + realized strata shares -------------------------------------------------------


def _build_populations(run_spec: RunSpec) -> Populations:
    """Build the :class:`Populations` bundle for the ENABLED strata at their GW20b floors.

    A member is built ONLY when its stratum is enabled (a disabled stratum stays ``None``, the
    composer's neutral default). Multi-key / tenant populations are built at the hard-builder floors
    (≥ 2k keys / ≥ 200 orgs, ≥ 10k tenants) so the dry-run exercises realistic identity cardinality
    (Req 3, 7); the prompt corpus is the GW20b default (Req 4); the retry policy carries the run's
    configured shares (Req 5). The CLI (``__main__``) owns population-size env knobs; the floors are
    the right default for the local de-risk.
    """
    strata = run_spec.strata
    key_population = keys_stratum.build_key_population(2_000, 200) if strata.multi_key else None
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


def _realized_strata_shares(
    run_spec: RunSpec, planned: Sequence[PlannedRequest]
) -> tuple[tuple[str, float], ...]:
    """The realized long-prompt / retry / secret-shaped shares over the planned requests (Req 3–7).

    A pure fold over the planned tuple, one ``(name, share)`` pair per ENABLED share-bearing
    stratum: ``long_prompt`` (fraction of long picks), ``retry`` (fraction of retrying clients),
    ``secret_shaped`` (fraction of secret-shaped plans, Req 6.2). Only enabled strata contribute, so
    a disabled stratum is absent rather than reported as 0. Sorted by name for a canonical tuple.
    """
    total = len(planned)
    if total == 0:
        return ()
    shares: list[tuple[str, float]] = []
    strata = run_spec.strata
    if strata.long_prompt:
        longs = sum(1 for r in planned if r.prompt_pick is not None and r.prompt_pick.is_long)
        shares.append(("long_prompt", longs / total))
    if strata.retry:
        retrying = sum(1 for r in planned if r.client_is_retrying)
        shares.append(("retry", retrying / total))
    if strata.secret_output:
        secret = sum(
            1
            for r in planned
            if r.secret_output_plan is not None and r.secret_output_plan.secret_shaped
        )
        shares.append(("secret_shaped", secret / total))
    return tuple(sorted(shares))


def _configured_share(run_spec: RunSpec, name: str) -> float:
    """The configured share for a realized-share ``name`` (long_prompt / retry / secret_shaped)."""
    strata = run_spec.strata
    if name == "long_prompt":
        return strata.long_prompt_share
    if name == "retry":
        return min(1.0, strata.retry_share + strata.aggressive_retry_share)
    if name == "secret_shaped":
        return strata.secret_output_share
    return 0.0


# ---- The three Req 12 verifications -------------------------------------------------------------


def _verify_arrival(
    run_spec: RunSpec, planned: Sequence[PlannedRequest]
) -> tuple[bool, float, float]:
    """Verify the realized Poisson mean inter-arrival vs ``1/offered_rate`` (Req 12.3).

    Computes the realized mean of the consecutive inter-arrival gaps of the planned schedule and
    compares it to the expected exponential mean ``1/offered_rate`` within the configured RELATIVE
    tolerance (``Tolerances.arrival_mean_relative``). Returns ``(matches, realized_mean,
    expected)``. Fewer than two arrivals yields a trivially-passing degenerate case (no gap).
    """
    gaps = [
        b.arrival.scheduled_s - a.arrival.scheduled_s
        for a, b in zip(planned, planned[1:], strict=False)
    ]
    expected = 1.0 / run_spec.offered_rate_per_s if run_spec.offered_rate_per_s > 0 else math.inf
    if not gaps:
        return True, 0.0, expected
    realized = statistics.fmean(gaps)
    tol = run_spec.tolerances.arrival_mean_relative
    matches = abs(realized - expected) <= tol * expected if math.isfinite(expected) else False
    return matches, realized, expected


def _verify_strata_shares(run_spec: RunSpec, realized: Sequence[tuple[str, float]]) -> bool:
    """Verify each realized strata share is within the absolute convergence tolerance (Req 12.4)."""
    tol = run_spec.tolerances.share_convergence
    return all(abs(share - _configured_share(run_spec, name)) <= tol for name, share in realized)


def _verify_c4(c4: C4Summary) -> bool:
    """Verify the C4 numbers obey the Req 8 client-side definition (Req 12.5).

    The headline ``c4_p99_ms`` must be a finite p99 (clean run) OR ``+inf`` (errored streams pushed
    the rank onto an infinity entry, Req 8.4/8.5) — never negative or NaN. The long/short finite
    sample sets must carry no negative/non-finite sample (a C4 is a non-negative latency floored at
    0 by the collector). This checks the DEFINITION holds, not a specific latency figure.
    """
    if math.isnan(c4.c4_p99_ms) or c4.c4_p99_ms < 0.0:
        return False
    for sample in (*c4.long_prompt.samples_ms, *c4.short_prompt.samples_ms):
        if math.isnan(sample) or sample < 0.0 or not math.isfinite(sample):
            return False
    return True


def _build_verification(
    run_spec: RunSpec, planned: Sequence[PlannedRequest], c4: C4Summary | None
) -> VerificationOutcomes:
    """Assemble the three Req 12 verification outcomes over the first rate's plan + C4."""
    arrival_ok, realized_mean, expected_mean = _verify_arrival(run_spec, planned)
    realized_shares = _realized_strata_shares(run_spec, planned)
    strata_ok = _verify_strata_shares(run_spec, realized_shares)
    c4_ok = _verify_c4(c4) if c4 is not None else False
    c4_p99 = c4.c4_p99_ms if c4 is not None else math.inf
    return VerificationOutcomes(
        arrival_matches_poisson=arrival_ok,
        arrival_realized_mean_s=realized_mean,
        arrival_expected_mean_s=expected_mean,
        strata_shares_match=strata_ok,
        realized_strata_shares=realized_shares,
        c4_consistent_with_req8=c4_ok,
        c4_p99_ms=c4_p99,
    )


# ---- Per-rate run + report ----------------------------------------------------------------------


async def _run_one_rate(
    *,
    run_spec: RunSpec,
    offered_rate_per_s: float,
    count: int,
    client: AsgiStreamClient,
    populations: Populations,
    seed: int,
) -> tuple[OfferedRun, tuple[PlannedRequest, ...], C4Summary]:
    """Run ONE offered rate against the booted gateway and reduce it to (run, plan, C4).

    Builds the Poisson arrival schedule at ``offered_rate_per_s`` + the planned requests under a
    per-rate seeded ``random.Random`` and the real monotonic clock, measures loadgen headroom
    (dry-fire), runs the open-loop offered run against ``client`` over the booted gateway, and
    summarizes client-side C4. All three are returned so the caller builds the per-rate report and
    feeds the ladder. Pure of any ladder decision — just one rate end to end.
    """
    import time

    rng = random.Random(seed)
    # Build the schedule against a ZERO-ORIGIN clock so each ``scheduled_s`` is a RELATIVE offset
    # (just the accumulated inter-arrival gaps), matching the driver's own relative timeline: the
    # driver captures its ``start_reference_s`` once at run start and measures each dispatch's
    # sleep as ``scheduled_s - (clock() - start_reference_s)``. Seeding the schedule's start with a
    # live monotonic/perf absolute instead would make ``scheduled_s`` a huge absolute value the
    # driver would then try to sleep until, stalling the run — the two must share one origin.
    schedule = build_schedule(
        mode=ArrivalMode.POISSON,
        offered_rate_per_s=offered_rate_per_s,
        count=count,
        rng=rng,
        clock=_zero_clock,
    )
    planned = plan_requests(run_spec, schedule, rng, populations=populations)
    headroom = await measure_headroom(
        planned, client, offered_rate_per_s=offered_rate_per_s, clock=time.perf_counter
    )
    offered = await run_offered(
        planned,
        client,
        offered_rate_per_s=offered_rate_per_s,
        schedule_drop_s=run_spec.tolerances.schedule_drop_s,
        min_headroom_margin=run_spec.tolerances.min_headroom_margin,
        clock=time.perf_counter,
        headroom=headroom,
    )
    c4 = summarize(offered.observations)
    return offered, planned, c4


def _run_outcome(offered: OfferedRun, c4: C4Summary) -> RunOutcome:
    """Project one offered run + its C4 into a ladder :class:`RunOutcome` (Req 10.3).

    ``c4_p99_ms`` is the headline C4 (Req 8.5); ``infra_error_share`` is the errored-stream share
    over the offered run; ``schedule_drops`` and ``generator_bound`` come off the open-loop run (Req
    1.4, 2.2).
    """
    total = len(offered.records)
    errored = sum(1 for r in offered.records if r.outcome.errored)
    infra_error_share = (errored / total) if total > 0 else 0.0
    return RunOutcome(
        c4_p99_ms=c4.c4_p99_ms,
        infra_error_share=infra_error_share,
        schedule_drops=offered.schedule_drop_count,
        generator_bound=offered.generator_bound,
    )


def _build_report(
    *,
    run_spec: RunSpec,
    contract: ResourceContract,
    offered: OfferedRun,
    planned: Sequence[PlannedRequest],
    c4: C4Summary,
) -> RunReport:
    """Build the GW20b per-run :class:`RunReport` for one offered rate (Req 11, Req 3–7, Req 1.6).

    A pure carrier: ``c4_p99_ms`` + pass/fail vs ``contract.target_p99_ms`` (Req 11.1), the
    Schedule_Drop count + loadgen headroom + generator-bound flag (Req 1.4, 2.3, 2.2), the realized
    strata shares + distinct key/org/tenant counts (Req 3–7), and honest sentinels for the fields
    the dry-run has no wired telemetry source for. ``owner_queue_wait_s`` / ``event_loop_lag_s`` are
    read from the gateway's exposition in a wired deployment; the dry-run's unwired registry exposes
    none, so they are 0.0 (an honest zero, not fabricated), and GPU-busy is the explicit UNAVAILABLE
    sentinel (Req 11.6 / 15.2). ``reason_mix`` is built from the offered run's terminal outcomes
    (admitted vs errored, Req 11.4). ``arrival_mode_constant`` is False (Poisson ladder, Req 12.2).
    """
    total = len(offered.records)
    errored = sum(1 for r in offered.records if r.outcome.errored)
    reason_mix = ReasonMix.from_counts({"admitted": total - errored, "errored": errored})
    distinct_keys, distinct_orgs = keys_stratum.distinct_counts(
        r.key_assignment for r in planned if r.key_assignment is not None
    )
    distinct_tenants = tenants_stratum.distinct_tenants(
        r.tenant_assignment for r in planned if r.tenant_assignment is not None
    )
    bound_ms = contract.target_p99_ms
    return RunReport(
        c4_p99_ms=c4.c4_p99_ms,
        slo_bound_ms=bound_ms,
        c4_passes=c4.c4_p99_ms < bound_ms,
        owner_queue_wait_s=0.0,
        event_loop_lag_s=0.0,
        reason_mix=reason_mix,
        schedule_drop_count=offered.schedule_drop_count,
        loadgen_headroom=offered.loadgen_headroom,
        generator_bound=offered.generator_bound,
        realized_strata_shares=_realized_strata_shares(run_spec, planned),
        distinct_keys=distinct_keys,
        distinct_orgs=distinct_orgs,
        distinct_tenants=distinct_tenants,
        gpu_busy=GpuBusy.UNAVAILABLE,
        arrival_mode_constant=False,
    )


# ---- The two public entrypoints -----------------------------------------------------------------


async def run_dry_run_async(
    run_spec: RunSpec,
    planned: Sequence[PlannedRequest],
    *,
    contract: ResourceContract,
    max_steps: int = 3,
) -> DryRunResult:
    """Run the full Poisson rate ladder end to end against the booted gateway (Req 12).

    Boots ONE gateway via :func:`build_dry_run_app` with the scripted provider + passing deps,
    attaches an in-process ``httpx.ASGITransport`` client (:func:`attach_asgi`, option (a)), and
    climbs the Poisson rate ladder from ``run_spec.offered_rate_per_s`` in ≤ 10% steps for
    ``max_steps`` rates (Req 12.2), feeding each rate's :class:`RunOutcome` into
    ``ladder.evaluate_ladder`` with the pass bound read from ``contract.target_p99_ms`` (Req 9.3).
    Per-rate it builds the :class:`RunReport` + :class:`C4Summary`, and over the FIRST rate's plan
    it runs the three Req 12 verifications (12.3 / 12.4 / 12.5). The scripted provider flows
    secret-shaped bytes through the real holdback path at the configured share; the realized
    secret-shaped share + the honest holdback-mechanism-not-detector note are recorded (Req 6).

    ``max_steps`` bounds the ladder for the local de-risk (the real knee search is a cloud gate);
    the ``planned`` tuple's length sets the per-rate offered request count. The httpx client is
    closed on exit.
    """
    count = len(planned)
    rng = random.Random(run_spec.seed)
    app = build_dry_run_app(contract, build_provider(run_spec, rng))
    client_http = attach_asgi(app)
    populations = _build_populations(run_spec)
    stream_client = AsgiStreamClient(client_http)
    rate_outcomes: list[tuple[float, tuple[RunOutcome, ...]]] = []
    reports: list[RunReport] = []
    c4_summaries: list[C4Summary] = []
    first_plan: tuple[PlannedRequest, ...] = ()
    first_c4: C4Summary | None = None
    secret_marked = 0
    secret_total = 0
    try:
        rate = run_spec.offered_rate_per_s
        for step in range(max(max_steps, 0)):
            offered, step_plan, c4 = await _run_one_rate(
                run_spec=run_spec,
                offered_rate_per_s=rate,
                count=count,
                client=stream_client,
                populations=populations,
                seed=run_spec.seed + step,
            )
            rate_outcomes.append((rate, (_run_outcome(offered, c4),)))
            reports.append(
                _build_report(
                    run_spec=run_spec,
                    contract=contract,
                    offered=offered,
                    planned=step_plan,
                    c4=c4,
                )
            )
            c4_summaries.append(c4)
            secret_marked += sum(
                1
                for r in step_plan
                if r.secret_output_plan is not None and r.secret_output_plan.secret_shaped
            )
            secret_total += len(step_plan)
            if step == 0:
                first_plan, first_c4 = step_plan, c4
            rate = next_rate(rate, run_spec.ladder)
    finally:
        await client_http.aclose()

    ladder = evaluate_ladder(
        rate_outcomes,
        contract,
        run_spec.ladder,
        max_infra_error_share=run_spec.tolerances.max_infra_error_share,
    )
    verification = _build_verification(run_spec, first_plan, first_c4)
    realized_secret = (secret_marked / secret_total) if secret_total > 0 else 0.0
    return DryRunResult(
        ladder=ladder,
        reports=tuple(reports),
        c4_summaries=tuple(c4_summaries),
        verification=verification,
        realized_secret_shaped_share=realized_secret,
        holdback_note=_HOLDBACK_NOTE,
    )


def run_dry_run(run_spec: RunSpec, planned: tuple[PlannedRequest, ...]) -> int:
    """Synchronous ``python -m load_harness`` seam (``__main__._DryRunEntry``) → exit code.

    Drives :func:`run_dry_run_async` under ``asyncio.run`` against a gateway built from the detected
    :class:`ResourceContract` (``load_contract`` — the one capacity authority, which refuses to
    start on a capacity-starved host, surfacing the honest-boot refusal rather than measuring a
    half-built app, per the design's Error Handling). Returns ``0`` iff all three Req 12
    verifications passed, else ``1``. On a missing ``conformance`` extra it reports the install hint
    and returns ``1`` (task-10.2's ``importorskip`` test covers the live assertion separately).
    """
    contract, _logs = load_contract()
    try:
        result = asyncio.run(run_dry_run_async(run_spec, planned, contract=contract))
    except DryRunDependencyMissing as exc:
        _LOG.warning("%s", exc)
        return 1
    _LOG.info(
        "dry-run: ladder highest_pass=%s first_fail=%s; verification arrival=%s strata=%s c4=%s; "
        "secret_shaped_share=%.4f; %s",
        result.ladder.highest_repeatable_pass_rate,
        result.ladder.first_repeatable_fail_rate,
        result.verification.arrival_matches_poisson,
        result.verification.strata_shares_match,
        result.verification.c4_consistent_with_req8,
        result.realized_secret_shaped_share,
        result.holdback_note,
    )
    return 0 if result.verification.all_passed else 1
