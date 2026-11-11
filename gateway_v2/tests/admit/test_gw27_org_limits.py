"""GW27 Task 5: per-org rate / burst / TPM limits (``admit/org_limits.py``)."""
from __future__ import annotations

import dataclasses
import random

import pytest

from gateway_v2.admit import org_limits as org_limits_module
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.org_limits import OrgLimits
from gateway_v2.domain.plan import OrgControls, RateControl
from gateway_v2.domain.posture import MIN_RETRY_AFTER_S, RATE_LIMITED
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_org import derive_org_bounds


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _contract(workers: int = 1) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0, memory_limit=2 * 1024**3, fd_limit=65_536, guard_capacity=None,
        target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024**2,
        worker_override=workers, cpu_source="t", mem_source="t", fd_source="t",
    )


def _limits(workers: int = 1, clock: Clock | None = None, **kw: object) -> tuple[OrgLimits, Clock]:
    clk = clock or Clock()
    return OrgLimits(_contract(workers), clock=clk, **kw), clk  # type: ignore[arg-type]


def _rate(rpm: int, burst: int, *, enabled: bool = True, tpm: int = 0) -> OrgControls:
    return OrgControls(rate=RateControl(enabled, rpm, burst), org_tpm_limit=tpm)


def _admitted(limits: OrgLimits, org: str, controls: OrgControls, n: int, cost: int = 1) -> int:
    return sum(limits.check(org, controls, cost, request_id="r") is None for _ in range(n))


def test_w1_burst_5_admits_five_then_refuses_the_sixth() -> None:
    limits, _ = _limits(1)
    controls = _rate(6000, 5)
    assert _admitted(limits, "o", controls, 5) == 5
    verdict = limits.check("o", controls, 1, request_id="req-6")
    assert verdict is not None and verdict.code == RATE_LIMITED
    assert verdict.retry_after_s >= MIN_RETRY_AFTER_S and verdict.should_retry is False
    assert verdict.request_id == "req-6"


def test_the_allowance_returns_with_time() -> None:
    limits, clock = _limits(1)
    controls = _rate(6000, 5)  # one request every 10 ms
    assert _admitted(limits, "o", controls, 6) == 5
    clock.now += 0.011
    assert _admitted(limits, "o", controls, 1) == 1


def test_simulated_36_workers_aggregate_is_bounded_by_the_spread_share() -> None:
    bounds = derive_org_bounds(_contract(36))
    workers = [_limits(36)[0] for _ in range(36)]
    controls = _rate(36_000, 150)
    total = sum(_admitted(w, "o", controls, 200) for w in workers)
    assert total == 36 * bounds.burst_per_worker(150) >= 150


def test_uncontrolled_rows_cost_nothing_even_with_a_full_table() -> None:
    limits, _ = _limits(1)
    limits._bounds = dataclasses.replace(limits._bounds, slots=1)  # noqa: SLF001
    assert _admitted(limits, "a", _rate(60, 1), 1) == 1
    off = _rate(60, 1, enabled=False)
    assert _admitted(limits, "b", off, 50) == 50
    assert _admitted(limits, "c", OrgControls(), 50) == 50
    assert limits.slots_used == 1


def test_tpm_is_enforced_when_the_rate_switch_is_off() -> None:
    limits, _ = _limits(1)
    controls = _rate(60, 1, enabled=False, tpm=1000)
    state_orgs = limits._orgs  # noqa: SLF001
    assert limits.check("o", controls, 10, request_id="r") is None
    assert state_orgs["o"].request is None and state_orgs["o"].tokens is not None
    assert _admitted(limits, "o", controls, 1000, cost=100_000) < 1000  # the bucket refuses


def test_tpm_refund_restores_the_allowance() -> None:
    limits, _ = _limits(1)
    controls = OrgControls(org_tpm_limit=1000)
    bucket_cost = 900
    assert limits.check("o", controls, bucket_cost, request_id="r") is None
    tat = limits._orgs["o"].tokens.tat  # type: ignore[union-attr]  # noqa: SLF001
    limits.refund_tokens("o", bucket_cost)
    assert limits._orgs["o"].tokens.tat < tat  # type: ignore[union-attr]  # noqa: SLF001


def test_a_tiny_tpm_limit_still_admits_an_idle_request() -> None:
    limits, _ = _limits(36)
    ceiling = _contract(36).default_response_token_ceiling()
    controls = OrgControls(org_tpm_limit=max(1, ceiling // 4))
    assert limits.check("o", controls, ceiling // 8, request_id="r") is None


def test_full_table_refuses_a_controlled_org_and_never_evicts() -> None:
    metrics = AdmissionMetrics()
    limits, _ = _limits(1, metrics=metrics)
    limits._bounds = dataclasses.replace(limits._bounds, slots=2)  # noqa: SLF001
    assert _admitted(limits, "a", _rate(60, 1), 1) == 1
    assert _admitted(limits, "b", _rate(60, 1), 1) == 1
    verdict = limits.check("c", _rate(60, 1), 1, request_id="r")
    assert verdict is not None and verdict.code == RATE_LIMITED
    assert limits.check("a", _rate(60, 1), 1, request_id="r") is not None  # a's bucket survived
    reading = metrics.snapshot()
    assert reading.org_limits_table_full_total == 1 and reading.org_limits_slots_used == 2
    limits.invalidate("a")
    assert limits.check("c", _rate(60, 1), 1, request_id="r") is None


def test_fail_closed_when_the_bucket_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    limits, _ = _limits(1)

    def boom(*_a: object, **_k: object) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(org_limits_module._Bucket, "take", boom)  # noqa: SLF001
    verdict = limits.check("o", _rate(60, 1), 1, request_id="r")
    assert verdict is not None and verdict.code == RATE_LIMITED


def test_a_republish_does_not_refill_the_bucket() -> None:
    limits, _ = _limits(1)
    assert _admitted(limits, "o", _rate(6000, 5), 5) == 5
    assert _admitted(limits, "o", _rate(6001, 5), 1) == 0  # republished: debt carried forward


# Feature: gw27-firewall-config-fields, Property 6: admitted <= burst_worker + rate*horizon + 1
# Validates: Requirements 3.1
def test_property_rate_bound() -> None:
    seed = 2706
    rng = random.Random(seed)
    for i in range(10_000):
        workers = rng.choice((1, 2, 8, 36))
        rpm = rng.randint(workers, 20_000)
        burst = rng.randint(1, 200)
        limits, clock = _limits(workers)
        bounds = derive_org_bounds(_contract(workers))
        interval = bounds.request_interval_s(rpm)
        horizon = rng.uniform(0.0, 40 * interval)
        times = sorted(rng.uniform(0.0, horizon) for _ in range(rng.randint(1, 60)))
        start = clock.now
        admitted = 0
        for t in times:
            clock.now = start + t
            admitted += limits.check("o", _rate(rpm, burst), 1, request_id="r") is None
        bound = bounds.burst_per_worker(burst) + horizon / interval + 1
        assert admitted <= bound + 1e-6, (seed, i, workers, rpm, burst, admitted, bound)


# Feature: gw27-firewall-config-fields, Property 6b: 0.5*burst spread is almost never refused
# Validates: Requirements 3.1
@pytest.mark.parametrize("workers", [2, 8, 36])
def test_property_spread_slack(workers: int) -> None:
    seed = 2760 + workers
    rng = random.Random(seed)
    pool = [_limits(workers)[0] for _ in range(workers)]
    controls = _rate(60_000, 100)
    refused_trials = 0
    trials = 10_000
    for _ in range(trials):
        for _ in range(50):
            target = pool[rng.randrange(workers)]
            if target.check("o", controls, 1, request_id="r") is not None:
                refused_trials += 1
                break
        for limit in pool:
            limit.invalidate("o")
    assert refused_trials < trials / 1000, (seed, workers, refused_trials)


# Feature: gw27-firewall-config-fields, Property 7: TPM conservation and refund
# Validates: Requirements 3.3
def test_property_tpm_conservation_and_refund() -> None:
    seed = 2707
    rng = random.Random(seed)
    for i in range(10_000):
        workers = rng.choice((1, 4, 36))
        tpm = rng.randint(1, 2_000_000)
        limits, clock = _limits(workers)
        bounds = derive_org_bounds(_contract(workers))
        horizon = rng.uniform(0.0, 600.0)
        start = clock.now
        admitted_tokens = 0
        controls = OrgControls(org_tpm_limit=tpm)
        for t in sorted(rng.uniform(0.0, horizon) for _ in range(rng.randint(1, 40))):
            clock.now = start + t
            cost = rng.randint(1, 200_000)
            if limits.check("o", controls, cost, request_id="r") is None:
                admitted_tokens += cost
        t_tok = bounds.token_interval_s(tpm)
        slack = bounds.token_tolerance_s(tpm) / t_tok
        assert admitted_tokens <= horizon / t_tok + slack + 1e-6, (seed, i, tpm, workers)
        # Refund: charge then refund restores the debt (never below "now").
        bucket = limits._orgs["o"].tokens  # noqa: SLF001
        assert bucket is not None
        before = bucket.tat
        if limits.check("o", controls, 7, request_id="r") is None:
            limits.refund_tokens("o", 7)
            assert bucket.tat <= before + 1e-6, (seed, i)


# Feature: gw27-firewall-config-fields, Property 8: any injected exception yields a 429, never admit
# Validates: Requirements 3.5
def test_property_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    seed = 2708
    rng = random.Random(seed)
    limits, _ = _limits(1)

    def boom(*_a: object, **_k: object) -> bool:
        raise ValueError("injected")

    monkeypatch.setattr(org_limits_module._Bucket, "take", boom)  # noqa: SLF001
    for i in range(10_000):
        controls = _rate(rng.randint(1, 5000), rng.randint(1, 100), tpm=rng.choice((0, 500)))
        verdict = limits.check(f"o{i % 5}", controls, rng.randint(0, 100), request_id=f"r{i}")
        assert verdict is not None and verdict.code == RATE_LIMITED, (seed, i)


# Feature: gw27-firewall-config-fields, Property 12: republish never resets; a full table refuses
# Validates: Requirements 3.1
def test_property_no_reset_on_republish() -> None:
    seed = 2712
    rng = random.Random(seed)
    slots = 4
    limits, clock = _limits(1)
    limits._bounds = dataclasses.replace(limits._bounds, slots=slots)  # noqa: SLF001
    orgs = [f"o{n}" for n in range(slots)]
    debts: dict[str, tuple[float, float]] = {}
    for i in range(10_000):
        org = rng.choice(orgs)
        controls = _rate(rng.choice((600, 6000)), rng.randint(1, 20), tpm=rng.choice((0, 5000)))
        state = limits._orgs.get(org)  # noqa: SLF001
        before = None if state is None or state.request is None else state.request.tat - clock.now
        limits.check(org, controls, rng.randint(1, 50), request_id="r")
        if rng.random() < 0.3:
            clock.now += rng.uniform(0.0, 0.05)
        state = limits._orgs.get(org)  # noqa: SLF001
        assert state is not None, (seed, i)  # at most `slots` orgs: nothing is ever evicted
        if before is not None and state.request is not None and state.controls == controls:
            after = state.request.tat - clock.now
            assert after >= min(before, state.request.tau) - 0.06 - 1e-6, (seed, i, before, after)
        debts[org] = (clock.now, 0.0)
    assert limits.slots_used <= slots
