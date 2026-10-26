"""Two replicas report the plan they have loaded, and a stale one reports none."""

from __future__ import annotations

from collections.abc import Callable

from gateway_v2.domain.category import Category
from gateway_v2.domain.plan import Action, FailurePosture, Mode, RuleScope, Surface
from gateway_v2.plan.applied import report_applied
from gateway_v2.plan.compiler import RuleDraft, compile_plan
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

_FRESH_S = 5.0


def test_both_replicas_report_the_published_version() -> None:
    clocks = {"a": 0.0, "b": 0.0}
    store = PlanStore()
    first = compile_plan("org", (_draft(0.5),), compiled_at=1.0)
    second = compile_plan("org", (_draft(0.9),), compiled_at=2.0, previous=first)
    store.put(first)
    snaps = _snaps(store, clocks)
    store.put(second)
    reports = [
        report_applied(
            replica_id=name,
            snapshot=snap,
            org_id="org",
            now=clocks[name],
            fresh_s=_FRESH_S,
        )
        for name, snap in snaps.items()
    ]
    assert [row["version"] for row in reports] == [second.version, second.version]
    assert all(row["fresh"] for row in reports)


def test_a_replica_past_the_freshness_bound_reports_no_version() -> None:
    clocks = {"a": 0.0, "b": 0.0}
    store = PlanStore()
    plan = compile_plan("org", (_draft(0.5),), compiled_at=1.0)
    store.put(plan)
    snaps = _snaps(store, clocks)
    for name, snap in snaps.items():
        report_applied(
            replica_id=name,
            snapshot=snap,
            org_id="org",
            now=clocks[name],
            fresh_s=_FRESH_S,
        )
    store.mark_unreachable("org")
    clocks["b"] = 6.0
    stale = report_applied(
        replica_id="b",
        snapshot=snaps["b"],
        org_id="org",
        now=clocks["b"],
        fresh_s=_FRESH_S,
    )
    live = report_applied(
        replica_id="a",
        snapshot=snaps["a"],
        org_id="org",
        now=clocks["a"],
        fresh_s=_FRESH_S,
    )
    assert stale == {"replica_id": "b", "version": None, "fresh": False}
    assert live["version"] == plan.version
    assert live["fresh"] is True


def _snaps(store: PlanStore, clocks: dict[str, float]) -> dict[str, ReplicaSnapshot]:
    return {
        name: ReplicaSnapshot(
            store,
            fresh_ms=5000,
            reconcile_period_s=1.0,
            clock=_clock(clocks, name),
        )
        for name in ("a", "b")
    }


def _clock(clocks: dict[str, float], name: str) -> Callable[[], float]:
    def read() -> float:
        return clocks[name]

    return read


def _draft(threshold: float) -> RuleDraft:
    return RuleDraft(
        rule_id="fw-pii-input",
        category=Category.PII,
        mode=Mode.ENFORCE,
        action=Action.REDACT,
        threshold=threshold,
        priority=100,
        scope=RuleScope.INPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )
