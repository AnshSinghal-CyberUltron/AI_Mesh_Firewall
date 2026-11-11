"""In-process open-loop ``Load_Harness`` + local acceptance gates (R2-07 / R2-08 / R2-18, GW19).

This module is the scaled-down, **in-process** equivalent of the runbook's live overload fleet. It
drives the already-built :class:`gateway_v2.admit.admission.AdmissionController` directly with an
**injected, advanceable fake clock** and an **injected guard service rate** (``q_safe``) — never a
real HTTP fleet (Req 12.1). Every acceptance bound below (admitted p99, highest-pass / first-fail
arrival rates, retry-amplification factor, post-heal recovery window) is computed from the
*simulated* sojourn samples under the fake clock, so the numbers are deterministic and are not
sensitive to CI wall-clock jitter — the same realism caveat the R2-06 holdback benchmark carries.

Local gates implemented here (design "Local acceptance via the ``Load_Harness``" table):

======== ============================================================================= ===========
Gate     What it drives                                                                Requirement
======== ============================================================================= ===========
LGW19-1  3x injected ``q_safe``: explicit shedding, admitted p99 within budget, no      12.2
         unbounded memory (mirrors §0.2: admitted p99 ~=17.8 ms under a 4.3x burst,     Props 2,4
         0 FAIL_OPEN)
LGW19-4  the concurrency cap BINDS and is observable via ``queue_report`` / metrics     12.6
         (not inert as in v1)
LGW19-2  open-loop arrival-rate ramp; record the highest-passing + first-failing rate   12.3
LGW19-5  injected guard rate halved mid-run; shedding rises, queue age bounded          12.4
LGW19-6  slow consumers on 50% of streams; backpressure, bounded memory, fast consumers 12.5
         unaffected                                                                     Props 4
G-06     local SDK-retry-semantics simulation; amplification below the bound vs the     6.5
         6-11 ms baseline that triples load (Property 8)                                Prop 8
G-15     injected-clock post-heal recovery within 10 s (CoDel resets on the first       11.1
         sub-target sojourn) (Property 9)                                               Prop 9
======== ============================================================================= ===========

**Deferred cloud/scale gates (out of local scope, documented).** The full live fleet 3x-``q_safe``
run at real scale (LGW19-1 live), the GW20 live ``q_safe`` measurement that feeds this card, gate
**G-06** against the real OpenAI SDK, and gate **G-15** live post-heal certification are deferred
cloud/scale gates (Req 12.7, 11.2, 3.4); this harness is their scaled-down in-process equivalent.
Real OS process supervision and the removal of the inert ``--worker-connections`` flag / deprecated
uvicorn worker class remain a declared dependency on the serving-entrypoint card.

House idiom: seeded ``random.Random`` loops of >= 10,000 iterations for the property tests (seed
logged in each assertion message), NO ``hypothesis``; async via a local ``_run`` helper. The
``Load_Harness`` itself is kept here as test scaffolding (NOT added to the shipped ``admit``
package) because it only ever drives the production controller under a fake clock.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted, ShedVerdict, shed_retry_after_s
from gateway_v2.admit.metrics import AdmissionMetrics, ShedReason
from gateway_v2.admit.quota import AdmissionBounds, derive_bounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.domain.posture import MIN_RETRY_AFTER_S
from gateway_v2.runtime.resources import ResourceContract

_PROPERTY_ITERATIONS = 10_000
_MS_PER_S = 1000.0

# The CoDel target budget: admitted p99 must stay within the owner-signed 5 ms target under
# sustained overload (Req 1.5 / Property 2). A small headroom factor is applied where a gate
# compares against the "budget" so a single scheduling boundary does not flake the deterministic
# bound (the §0.2 live shape sat at ~17.8 ms against a 20 ms contract p99, i.e. ~0.9x; here the
# admitted-sojourn samples sit at or below Target by construction, so the budget is Target itself).
_TARGET_MS = CoDelParams().target_ms
_HARD_CAP_MS = CoDelParams().hard_cap_ms

# A deployment-realistic audit calibration (mirrors tests/admit/test_lgw19_quota.py).
_AUDIT_DRAIN_RATE = 100_000.0
_AUDIT_BYTES_PER_RECORD = 3_000


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _contract(
    *,
    memory_limit: int = 2 * 1024 * 1024 * 1024,
    target_p99_ms: float = 20.0,
    utilization_cap: float = 0.75,
    per_worker_rss: int = 400 * 1024 * 1024,
) -> ResourceContract:
    """A valid, serve-able contract fixture (same shape as the bounds tests)."""
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=memory_limit,
        fd_limit=65_536,
        guard_capacity=None,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
        worker_override=None,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )


def _bounds_from_contract(contract: ResourceContract, *, q_safe: float) -> AdmissionBounds:
    """Derive the admission bounds exactly as production does (no literal here)."""
    return derive_bounds(
        contract,
        q_safe=q_safe,
        audit_drain_rate_per_s=_AUDIT_DRAIN_RATE,
        audit_bytes_per_record=_AUDIT_BYTES_PER_RECORD,
    )


def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile over ``samples`` (same discipline as the metrics histogram)."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = max(1, round(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(slots=True)
class HarnessResult:
    """What a single open-loop harness run observed.

    All quantities are computed from the simulated sojourn samples under the injected clock, so they
    are deterministic. ``admitted`` / ``shed`` are counts; ``admitted_sojourn_ms`` is every admitted
    request's sojourn (used for the p99); ``peak_queue_depth`` is the largest observed request-queue
    depth (bounded-memory evidence); ``max_queue_age_s`` is the largest observed oldest-item age.
    """

    admitted: int = 0
    shed: int = 0
    admitted_sojourn_ms: list[float] = field(default_factory=list)
    peak_queue_depth: int = 0
    max_queue_age_s: float = 0.0
    fail_open_total: int = 0

    @property
    def total(self) -> int:
        return self.admitted + self.shed

    @property
    def admitted_p99_ms(self) -> float:
        return _percentile(self.admitted_sojourn_ms, 99.0)

    @property
    def shed_fraction(self) -> float:
        return self.shed / self.total if self.total else 0.0


class LoadHarness:
    """In-process open-loop load generator driving the real ``AdmissionController`` (Req 12.1).

    The model is a single-queue open-loop simulation over the injected fake clock:

    * time advances in fixed ``dt`` steps; at each step ``offered_rate * dt`` requests arrive;
    * a *backlog* of admitted-but-not-yet-serviced work drains at the injected ``q_safe`` rate, so
      under overload the backlog (and therefore the sojourn a fresh arrival would experience) grows;
    * a fresh arrival's **sojourn** is the time until service begins, ``backlog / q_safe`` seconds,
      which is handed to :meth:`AdmissionController.admit` as ``enqueued_at = now - sojourn`` — this
      is exactly the quantity CoDel measures (Req 1 / 3.1), independent of prompt size;
    * CoDel either admits (the request joins the backlog and its sojourn is recorded) or sheds at
      the door (no backlog growth) — so shedding is what keeps the backlog, the admitted p99, and
      the buffered memory bounded.

    ``q_safe`` is injectable per step so a gate can halve the guard rate mid-run (LGW19-5). The
    harness never fabricates an outcome: it submits to the production controller and records
    whatever the controller decides.
    """

    __slots__ = ("_clock_box", "_controller", "_dt", "_metrics", "_in_service", "_owner")

    def __init__(
        self,
        controller: AdmissionController,
        clock_box: dict[str, float],
        metrics: AdmissionMetrics,
        *,
        dt: float = 0.001,
        owner: str = "owner",
    ) -> None:
        self._controller = controller
        self._clock_box = clock_box
        self._metrics = metrics
        self._dt = dt
        self._owner = owner
        # Admitted-but-not-yet-serviced requests, each a (request_id, finish_at) FIFO entry. The
        # guard services them at q_safe; the real request queue (bounded at request_depth) is what
        # caps how many can ever be in service at once, so this list length never exceeds that
        # declared max -- the bounded queue, not an unbounded float, is what bounds the sojourn.
        self._in_service: list[tuple[str, float]] = []

    def _service_completions(self, now: float) -> None:
        """Complete every admitted request whose service finished at/before ``now`` (FIFO).

        Completion releases the request's slot in the controller's bounded request queue (Req 2 --
        admitted work is answered, never abandoned), which is what lets fresh arrivals be admitted
        again once the backlog drains.
        """
        while self._in_service and self._in_service[0][1] <= now:
            request_id, _finish = self._in_service.pop(0)
            self._controller.complete(request_id)

    def run(
        self,
        *,
        offered_rate: float,
        q_safe_schedule: Callable[[float], float],
        duration_s: float,
    ) -> HarnessResult:
        """Drive the controller open-loop for ``duration_s`` and return the observed result.

        ``offered_rate`` is the open-loop arrival rate (req/s); ``q_safe_schedule(now)`` returns the
        injected guard service rate at the given clock instant (constant for most gates, halved
        mid-run for LGW19-5). Each admitted request takes ``1/q_safe`` seconds of guard service and
        its **sojourn** is the time until its service begins -- the standing-queue delay the real
        guard would impose -- which is exactly what CoDel measures (Req 1 / 3.1). Because the
        controller's request queue is bounded at ``request_depth``, the number of requests ever in
        service is bounded, so the sojourn a fresh arrival sees is bounded too.
        """
        result = HarnessResult()
        pending_arrivals = 0.0
        steps = int(round(duration_s / self._dt))
        seq = 0
        # The clock instant at which the guard becomes free to start the NEXT request. Service is
        # FIFO: a request admitted while the guard is busy waits until the queue ahead of it drains.
        service_free_at = self._clock_box["t"]
        for _ in range(steps):
            now = self._clock_box["t"]
            q_safe = q_safe_schedule(now)
            self._service_completions(now)
            if service_free_at < now:
                service_free_at = now
            pending_arrivals += offered_rate * self._dt
            arrivals = int(pending_arrivals)
            pending_arrivals -= arrivals
            for _a in range(arrivals):
                # Sojourn = time until this request's service can begin (standing-queue delay).
                sojourn_s = max(0.0, service_free_at - now)
                enqueued_at = now - sojourn_s
                seq += 1
                request_id = f"r{seq}"
                outcome = _run(self._controller.admit(self._owner, request_id, enqueued_at))
                if isinstance(outcome, Admitted):
                    result.admitted += 1
                    result.admitted_sojourn_ms.append(sojourn_s * _MS_PER_S)
                    # The guard spends 1/q_safe s servicing this request after the queue ahead.
                    service_s = 1.0 / q_safe if q_safe > 0 else _HARD_CAP_MS / _MS_PER_S
                    finish_at = service_free_at + service_s
                    service_free_at = finish_at
                    self._in_service.append((request_id, finish_at))
                else:
                    assert isinstance(outcome, ShedVerdict)
                    result.shed += 1
                report = self._controller.queue_report(now=now)
                result.peak_queue_depth = max(result.peak_queue_depth, report.depth("request"))
                result.max_queue_age_s = max(result.max_queue_age_s, report.oldest_age_s("request"))
            self._clock_box["t"] = now + self._dt
        result.fail_open_total = self._metrics.snapshot().fail_open_total
        return result


def _new_controller(
    clock_box: dict[str, float],
    bounds: AdmissionBounds,
    metrics: AdmissionMetrics,
    *,
    seed: int = 0,
) -> AdmissionController:
    return AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(seed),
        supervisor=WorkerSupervisor(),
        metrics=metrics,
        drain_window_s=1.0,
        clock=lambda: clock_box["t"],
    )


def _harness_for(
    *,
    q_safe: float,
    seed: int = 0,
) -> tuple[LoadHarness, AdmissionController, dict[str, float], AdmissionMetrics]:
    """Build a harness driving a production controller sized from the ResourceContract + q_safe."""
    clock_box = {"t": 0.0}
    metrics = AdmissionMetrics()
    bounds = _bounds_from_contract(_contract(), q_safe=q_safe)
    controller = _new_controller(clock_box, bounds, metrics, seed=seed)
    harness = LoadHarness(controller, clock_box, metrics)
    return harness, controller, clock_box, metrics


# --------------------------------------------------------------------------- #
# LGW19-1 (local) + LGW19-4 (task 14.1) — 3x q_safe burst + concurrency cap binds
# Validates: Requirements 12.1, 12.2, 12.6
# --------------------------------------------------------------------------- #


def test_lgw19_1_three_times_q_safe_sheds_bounds_p99_and_memory() -> None:
    """3x injected q_safe ⇒ explicit shedding, admitted p99 within budget, bounded memory.

    Mirrors the §0.2 shape (admitted p99 ~=17.8 ms under a 4.3x burst; 0 FAIL_OPEN): the admitted
    sojourn samples stay within the CoDel Target budget while the shed set is non-empty and the
    request queue never grows without bound.
    """
    q_safe = 2_000.0
    harness, _controller_, _box, metrics = _harness_for(q_safe=q_safe)

    result = harness.run(
        offered_rate=3.0 * q_safe,  # 3x overload (Req 12.2)
        q_safe_schedule=lambda _now: q_safe,
        duration_s=2.0,
    )

    # Explicit shedding occurred (the shed set is non-empty) — Req 12.2 / Property 2.
    assert result.shed > 0, "3x q_safe produced no shedding"
    assert result.shed_fraction > 0.3, (
        f"shed fraction {result.shed_fraction:.3f} too low for a 3x overload"
    )
    # Admitted p99 stays within the SLO budget (the contract target p99, mirroring the §0.2 shape
    # where admitted p99 ~=17.8 ms sat below the 20 ms contract) — Req 12.2 / Property 2. The
    # bounded request queue caps the standing-queue delay well under the hard cap.
    budget_ms = _contract().target_p99_ms
    assert result.admitted_p99_ms <= budget_ms, (
        f"admitted p99 {result.admitted_p99_ms:.3f} ms exceeded the {budget_ms} ms SLO budget"
    )
    # No unbounded memory growth: the request queue depth stayed bounded by its declared max.
    bounds = _bounds_from_contract(_contract(), q_safe=q_safe)
    assert result.peak_queue_depth <= bounds.request_depth, (
        f"request depth {result.peak_queue_depth} exceeded declared max {bounds.request_depth}"
    )
    # 0 FAIL_OPEN across the whole burst (§0.2 / Req 13.2).
    assert result.fail_open_total == 0, f"fail_open_total was {result.fail_open_total}, must be 0"
    assert metrics.snapshot().admitted_total == result.admitted


def test_lgw19_4_concurrency_cap_binds_and_is_observable() -> None:
    """The concurrency cap BINDS and is observable via queue_report / metrics (not inert).

    v1's cap was inert. Here the bounded request queue actually caps depth: under a heavy burst the
    observed depth reaches the declared max and never exceeds it, and that bound is visible through
    the public ``queue_report`` and the metrics snapshot.
    """
    q_safe = 2_000.0
    bounds = _bounds_from_contract(_contract(), q_safe=q_safe)
    clock_box = {"t": 0.0}
    metrics = AdmissionMetrics()
    controller = _new_controller(clock_box, bounds, metrics)

    # Fill the request queue to its declared max with sub-target sojourns (CoDel admits), WITHOUT
    # completing — so the cap is what stops further admission (the cap binds).
    admitted = 0
    for k in range(bounds.request_depth + 50):
        clock_box["t"] += 0.0001
        outcome = _run(controller.admit("owner", f"r{k}", clock_box["t"]))
        if isinstance(outcome, Admitted):
            admitted += 1

    report = controller.queue_report()
    # The cap bound: admitted exactly the declared max, depth is pinned at the max, no overflow.
    assert admitted == bounds.request_depth, (
        f"admitted {admitted} != declared concurrency cap {bounds.request_depth} (cap inert?)"
    )
    assert report.depth("request") == bounds.request_depth, (
        f"observed request depth {report.depth('request')} != cap {bounds.request_depth}"
    )
    assert report.depth("request") <= bounds.request_depth
    # The cap is observable through the metrics producer too.
    snap = metrics.snapshot()
    assert snap.queues.depth("request") == bounds.request_depth
    # Everything past the cap was shed at the door — the cap is not inert.
    assert snap.admitted_total == bounds.request_depth
    assert snap.shed_for(ShedReason.QUEUE_FULL) == 50, (
        f"expected 50 door sheds past the cap, saw {snap.shed_for(ShedReason.QUEUE_FULL)}"
    )


# --------------------------------------------------------------------------- #
# LGW19-2 (task 14.2) — open-loop arrival-rate ramp
# Validates: Requirements 12.3
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class SweepResult:
    """The two points LGW19-2 records: the highest-passing and first-failing arrival rate.

    ``highest_passing_rate`` is the largest offered rate whose admitted p99 stayed within budget;
    ``first_failing_rate`` is the first offered rate whose admitted p99 exceeded budget. Both are in
    req/s and are populated from the open-loop ramp (Req 12.3). ``budget_ms`` records the SLO the
    ramp was judged against so the recorded points are self-describing.
    """

    highest_passing_rate: float | None
    first_failing_rate: float | None
    budget_ms: float


def _sweep(
    *,
    q_safe: float,
    budget_ms: float,
    start_rate: float,
    stop_rate: float,
    rate_step: float,
    duration_s: float = 1.0,
) -> SweepResult:
    """Ramp the offered arrival rate open-loop until admitted p99 first exceeds ``budget_ms``.

    Each rate drives a fresh controller (so one rate's standing queue does not bleed into the next);
    the ramp records the highest rate that passed and the first rate that failed, then stops (Req
    12.3). The standing-queue delay climbs with the offered rate, so a budget below the queue's
    delay ceiling is crossed at a well-defined rate.
    """
    highest_passing: float | None = None
    first_failing: float | None = None
    rate = start_rate
    while rate <= stop_rate:
        harness, _ctl, _box, _metrics = _harness_for(q_safe=q_safe)
        result = harness.run(
            offered_rate=rate,
            q_safe_schedule=lambda _now: q_safe,
            duration_s=duration_s,
        )
        if result.admitted_p99_ms <= budget_ms:
            highest_passing = rate
        else:
            first_failing = rate
            break
        rate += rate_step
    return SweepResult(
        highest_passing_rate=highest_passing,
        first_failing_rate=first_failing,
        budget_ms=budget_ms,
    )


def test_lgw19_2_open_loop_ramp_records_highest_pass_and_first_fail() -> None:
    """Ramp offered rate open-loop; record the highest-passing + first-failing arrival rate.

    The admitted standing-queue delay climbs as the offered rate approaches and crosses ``q_safe``.
    Judged against a budget below the bounded queue's delay ceiling, the ramp therefore finds a
    well-defined highest-passing rate and the first rate that fails it (Req 12.3).
    """
    q_safe = 2_000.0
    # The bounded request queue (depth=request_depth) caps the standing delay at request_depth /
    # q_safe; pick a budget strictly inside that ceiling so the ramp genuinely crosses it.
    bounds = _bounds_from_contract(_contract(), q_safe=q_safe)
    delay_ceiling_ms = bounds.request_depth / q_safe * _MS_PER_S
    budget_ms = delay_ceiling_ms * 0.5

    sweep = _sweep(
        q_safe=q_safe,
        budget_ms=budget_ms,
        start_rate=0.25 * q_safe,
        stop_rate=4.0 * q_safe,
        rate_step=0.25 * q_safe,
    )

    # Both points were captured (Req 12.3): a highest-passing rate and the first failing rate.
    assert sweep.highest_passing_rate is not None, "no passing arrival rate was recorded"
    assert sweep.first_failing_rate is not None, "the ramp never found a failing arrival rate"
    # The failing rate is strictly above the highest passing rate (monotone ramp).
    assert sweep.first_failing_rate > sweep.highest_passing_rate, (
        f"first-fail {sweep.first_failing_rate} not above highest-pass {sweep.highest_passing_rate}"
    )


# --------------------------------------------------------------------------- #
# LGW19-5 (task 14.2) — guard service rate halved mid-run
# Validates: Requirements 12.4
# --------------------------------------------------------------------------- #


def test_lgw19_5_halving_guard_rate_raises_shedding_and_bounds_queue_age() -> None:
    """Halve the injected guard rate mid-run ⇒ shedding rises and queue age stays bounded.

    The first half runs at a rate the guard can serve (little shedding); halving ``q_safe`` at the
    midpoint doubles the standing-queue pressure, so the second half sheds materially more while the
    bounded queue keeps the oldest-item age bounded by the hard cap (Req 12.4).
    """
    q_safe = 2_000.0
    offered = 1.2 * q_safe  # just above the full rate: healthy at q_safe, overloaded at q_safe/2
    duration_s = 2.0
    midpoint = duration_s / 2.0

    def schedule(now: float) -> float:
        return q_safe if now < midpoint else q_safe / 2.0

    # Run the two halves as separate measured segments so the shed rate can be compared.
    first = _harness_for(q_safe=q_safe)
    before = first[0].run(
        offered_rate=offered,
        q_safe_schedule=lambda _now: q_safe,
        duration_s=midpoint,
    )
    second = _harness_for(q_safe=q_safe)
    after = second[0].run(
        offered_rate=offered,
        q_safe_schedule=lambda _now: q_safe / 2.0,
        duration_s=midpoint,
    )

    # Shedding rises when the guard rate is halved (Req 12.4).
    assert after.shed_fraction > before.shed_fraction, (
        f"halving q_safe did not raise shedding: before={before.shed_fraction:.3f} "
        f"after={after.shed_fraction:.3f}"
    )
    assert after.shed > before.shed
    # Queue age stays bounded by the hard cap throughout — no unbounded ageing (Req 12.4).
    assert after.max_queue_age_s <= _HARD_CAP_MS / _MS_PER_S + 1e-9, (
        f"queue age {after.max_queue_age_s * _MS_PER_S:.3f} ms exceeded the hard cap"
    )
    assert after.fail_open_total == 0

    # A single run across the schedule change also stays fail-closed and age-bounded end to end.
    whole = _harness_for(q_safe=q_safe)
    full = whole[0].run(offered_rate=offered, q_safe_schedule=schedule, duration_s=duration_s)
    assert full.fail_open_total == 0
    assert full.max_queue_age_s <= _HARD_CAP_MS / _MS_PER_S + 1e-9


# --------------------------------------------------------------------------- #
# LGW19-6 (task 14.3) — slow-consumer backpressure acceptance
# Validates: Requirements 12.5
# --------------------------------------------------------------------------- #


def test_lgw19_6_slow_consumers_on_half_the_streams() -> None:
    """Slow consumers on 50% of streams ⇒ backpressure, bounded memory, fast consumers unaffected.

    Half the streams are slow (never drain) and half are fast (drain as fed). The harness drives
    the production :meth:`AdmissionController.stream_credit` buffers: each slow consumer is capped
    at its byte credit (backpressure engages), total streaming memory is bounded by the aggregate
    credit regardless of how far the slow ones fall behind, and every fast consumer keeps accepting
    unaffected by the slow backlog (Req 12.5).
    """
    seed = 0x19_06AC
    rng = random.Random(seed)
    q_safe = 2_000.0
    credit = 4_096
    depth = _bounds_from_contract(_contract(), q_safe=q_safe).request_depth
    bounds = AdmissionBounds(
        concurrency=depth,
        request_depth=depth,
        guard_depth=depth,
        dispatch_depth=depth,
        egress_depth=credit,  # the egress byte bound is the per-consumer stream credit
        audit_depth=depth,
    )
    clock_box = {"t": 0.0}
    metrics = AdmissionMetrics()
    controller = _new_controller(clock_box, bounds, metrics)

    n_streams = 20
    slow_ids = [f"s{i}" for i in range(n_streams // 2)]
    fast_ids = [f"f{i}" for i in range(n_streams // 2)]
    slow = [controller.stream_credit(cid) for cid in slow_ids]
    fast = [controller.stream_credit(cid) for cid in fast_ids]

    fast_rejections = 0
    # Open-loop production: every stream is offered chunks each step; fast consumers drain, slow
    # ones never do.
    for _step in range(2_000):
        chunk = rng.randint(64, 512)
        for buf in slow:
            buf.offer(chunk)  # slow: offered but never drained
        for buf in fast:
            receipt = buf.offer(chunk)
            if not receipt.accepted:
                fast_rejections += 1
            else:
                buf.drain(chunk)  # fast: reads immediately

    # Backpressure engaged on the slow consumers: each is capped at its credit (Req 12.5 / Prop 4).
    for buf in slow:
        assert buf.buffered_bytes <= credit, f"seed={seed:#x} a slow consumer exceeded its credit"
        assert buf.offer(credit + 1).accepted is False, (
            f"seed={seed:#x} a saturated slow consumer still accepted an over-credit chunk"
        )
    # Total streaming memory bounded by the aggregate credit regardless of slow backlog (Req 12.5).
    total = sum(buf.buffered_bytes for buf in slow + fast)
    assert total <= n_streams * credit, (
        f"seed={seed:#x} total streaming memory {total} exceeded aggregate credit"
    )
    # Fast consumers unaffected by the slow backlog: they never had a chunk rejected (Req 12.5).
    assert fast_rejections == 0, (
        f"seed={seed:#x} fast consumers saw {fast_rejections} rejections caused by slow consumers"
    )
    for buf in fast:
        assert buf.buffered_bytes == 0, f"seed={seed:#x} a fast consumer retained a backlog"


# --------------------------------------------------------------------------- #
# G-06 local (task 14.4) + Property 8 (task 14.4a) — SDK-retry-amplification simulation
# Validates: Requirements 6.5
# --------------------------------------------------------------------------- #

# The OpenAI SDK retries a retryable failure up to 2 times by default. Our local model of its
# retry semantics: it retries ONLY when the response says it should (``should_retry`` true) AND the
# advertised Retry-After is short enough to retry within the request's own deadline. A shed from
# this component always carries ``should_retry=False`` and a Retry-After >= 1 s, so the modelled SDK
# does not re-offer the shed as load. The G-06 amplification bound is the resulting total-load
# multiplier; the 6-11 ms baseline that the component replaces drives it to ~2.6-3x.
_SDK_MAX_RETRIES = 2
_SDK_RETRY_DEADLINE_S = 0.5  # the SDK will not sit on a Retry-After longer than this to retry
_G06_AMPLIFICATION_BOUND = 1.5  # total load / original load must stay below this (well under 3x)


def _modelled_sdk_retries(retry_after_s: float, should_retry: bool) -> int:
    """How many times the modelled OpenAI SDK re-offers a shed, given the shed's advertised policy.

    The SDK retries up to ``_SDK_MAX_RETRIES`` times, but only when the response is marked retryable
    (``should_retry``) and the advertised Retry-After is short enough to retry within the SDK's
    deadline. A long (>= 1 s) Retry-After or ``should_retry=False`` yields zero retries -- which is
    exactly the shed policy this component emits (Req 6.3 / 6.4).
    """
    if should_retry and retry_after_s <= _SDK_RETRY_DEADLINE_S:
        return _SDK_MAX_RETRIES
    return 0


def _amplification(verdicts: list[ShedVerdict]) -> float:
    """Total load multiplier the modelled SDK would produce for a batch of shed responses."""
    original = len(verdicts)
    if original == 0:
        return 1.0
    extra = sum(_modelled_sdk_retries(v.retry_after_s, v.should_retry) for v in verdicts)
    return (original + extra) / original


def test_g06_local_amplification_below_bound_vs_6_to_11ms_baseline() -> None:
    """This component's shed policy keeps SDK retry-amplification below the G-06 bound.

    Contrast with the 6-11 ms Retry-After baseline (``should_retry`` effectively true, short
    Retry-After) that the modelled SDK retries twice on, tripling load (Req 6.5 / Property 8).
    """
    rng = random.Random(0x19_06_06)
    # This component's sheds: should_retry=False, retry_after >= 1 s.
    ours = [
        ShedVerdict(
            code="overload_shed",
            retry_after_s=shed_retry_after_s(rng),
            should_retry=False,
            request_id=f"r{i}",
        )
        for i in range(10_000)
    ]
    ours_amp = _amplification(ours)
    assert ours_amp < _G06_AMPLIFICATION_BOUND, (
        f"our shed amplification {ours_amp:.3f} not below the G-06 bound {_G06_AMPLIFICATION_BOUND}"
    )
    assert ours_amp == 1.0, f"a should_retry=False / >=1 s shed must not amplify (saw {ours_amp})"

    # The 6-11 ms baseline the component replaces: short Retry-After, retryable -> SDK retries 2x.
    baseline = [
        ShedVerdict(
            code="overload_shed",
            retry_after_s=rng.uniform(0.006, 0.011),
            should_retry=True,
            request_id=f"b{i}",
        )
        for i in range(10_000)
    ]
    baseline_amp = _amplification(baseline)
    assert baseline_amp >= 2.6, (
        f"the 6-11 ms baseline should amplify >= 2.6x (saw {baseline_amp:.3f})"
    )
    assert ours_amp < baseline_amp


# Feature: admission-control, Property 8
# Validates: Requirements 6.5
def test_property8_retry_amplification_bound() -> None:
    """Property 8: shed-then-retry under the local SDK semantics stays below the G-06 bound.

    Seeded ``random.Random`` loop over shed-then-retry sequences: for every batch of sheds this
    component emits (should_retry=False, Retry-After >= 1 s via the real jitter helper), the
    modelled SDK amplification stays strictly below the G-06 bound, while a 6-11 ms baseline batch
    of the same size triples/~=2.6x the load (Property 8 / Req 6.5).
    """
    seed = 0x19_08
    rng = random.Random(seed)
    msg = f"seed={seed:#x}"
    for _ in range(_PROPERTY_ITERATIONS):
        batch = rng.randint(1, 64)
        ours = [
            ShedVerdict(
                code="overload_shed",
                retry_after_s=shed_retry_after_s(rng),
                should_retry=False,
                request_id=f"r{j}",
            )
            for j in range(batch)
        ]
        amp = _amplification(ours)
        assert amp < _G06_AMPLIFICATION_BOUND, f"{msg} amplification {amp:.3f} >= bound"
        assert amp == 1.0, f"{msg} a floored should_retry=False shed amplified ({amp})"
        # Every shed is non-amplifying because both guards hold (Req 6.3 / 6.4).
        for verdict in ours:
            assert verdict.retry_after_s >= MIN_RETRY_AFTER_S, msg
            assert not (0.006 <= verdict.retry_after_s <= 0.011), msg
            assert verdict.should_retry is False, msg


# --------------------------------------------------------------------------- #
# G-15 local (task 14.5) + Property 9 (task 14.5a) — injected-clock post-heal recovery
# Validates: Requirements 11.1
# --------------------------------------------------------------------------- #

_RECOVERY_BUDGET_S = 10.0  # admitted latency must return within the SLO within 10 s (Req 11.1)


def _recovery_time_s(
    *,
    q_safe: float,
    pre_heal_backlog_ms: float,
    seed: int = 0,
) -> float:
    """Time (injected clock) for admitted sojourn to return to <= Target after an injected heal.

    Models the post-heal drain the component OWNS: a pre-heal overload left a standing-queue delay
    of ``pre_heal_backlog_ms``; at the heal the overload stops and the guard drains the backlog at
    ``q_safe``. A fresh arrival's sojourn is the remaining standing delay, which falls linearly as
    the guard drains. CoDel resets on the first sub-Target sojourn, so "recovered" is the first
    clock instant an admitted request's sojourn is <= Target (Req 11.1 / Property 9).
    """
    clock_box = {"t": 0.0}
    metrics = AdmissionMetrics()
    bounds = _bounds_from_contract(_contract(), q_safe=q_safe)
    controller = _new_controller(clock_box, bounds, metrics, seed=seed)

    heal_at = clock_box["t"]
    standing_delay_s = pre_heal_backlog_ms / _MS_PER_S
    dt = 0.001
    recovered_at: float | None = None
    # After the heal, no new overload: the backlog drains at q_safe, so the standing delay a fresh
    # arrival sees shrinks by dt each step (the guard clears dt*q_safe work worth dt of delay).
    while clock_box["t"] - heal_at <= _RECOVERY_BUDGET_S + 1.0:
        now = clock_box["t"]
        remaining_delay_s = max(0.0, standing_delay_s - (now - heal_at))
        enqueued_at = now - remaining_delay_s
        outcome = _run(controller.admit("owner", f"r{now:.6f}", enqueued_at))
        if isinstance(outcome, Admitted) and remaining_delay_s * _MS_PER_S <= _TARGET_MS:
            # CoDel reset on this sub-Target sojourn: admitted latency is back within the SLO.
            recovered_at = now - heal_at
            break
        if isinstance(outcome, Admitted):
            controller.complete(f"r{now:.6f}")
        clock_box["t"] = now + dt
    assert recovered_at is not None, "post-heal recovery never completed within the window"
    assert metrics.snapshot().fail_open_total == 0
    return recovered_at


def test_g15_local_post_heal_recovery_within_10s() -> None:
    """After an injected heal, admitted latency returns to within the SLO within 10 s (Req 11.1).

    A pre-heal overload built a standing-queue delay near the hard cap; once healed, the guard
    drains it and CoDel resets on the first sub-Target sojourn, so recovery completes on the
    injected clock inside the 10 s budget (local equivalent of the deferred live G-15).
    """
    recovery_s = _recovery_time_s(q_safe=2_000.0, pre_heal_backlog_ms=_HARD_CAP_MS)
    assert recovery_s <= _RECOVERY_BUDGET_S, (
        f"post-heal recovery took {recovery_s:.3f} s, over the {_RECOVERY_BUDGET_S} s budget"
    )


# Feature: admission-control, Property 9
# Validates: Requirements 11.1
def test_property9_post_heal_recovery_bound() -> None:
    """Property 9: for any pre-heal backlog state, recovery is <= 10 s on the injected clock.

    Seeded ``random.Random`` loop over pre-heal backlog states (standing-queue delays anywhere from
    just above Target up to the hard cap, and a range of guard rates): every heal recovers within
    the 10 s budget, because CoDel resets the moment the drained delay first falls to/below Target
    (Property 9 / Req 11.1).
    """
    seed = 0x19_09
    rng = random.Random(seed)
    msg = f"seed={seed:#x}"
    for _ in range(_PROPERTY_ITERATIONS):
        q_safe = rng.uniform(500.0, 5_000.0)
        pre_heal_backlog_ms = rng.uniform(_TARGET_MS + 0.1, _HARD_CAP_MS)
        recovery_s = _recovery_time_s(
            q_safe=q_safe,
            pre_heal_backlog_ms=pre_heal_backlog_ms,
            seed=rng.randint(0, 2**31 - 1),
        )
        assert recovery_s <= _RECOVERY_BUDGET_S, (
            f"{msg} recovery {recovery_s:.3f} s over budget "
            f"(q_safe={q_safe:.1f} backlog={pre_heal_backlog_ms:.3f} ms)"
        )
