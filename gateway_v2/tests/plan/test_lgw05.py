"""LGW05-1..7 in process. No production console and no GCP."""

from __future__ import annotations

import threading

import pytest

from gateway_v2.domain.category import Category
from gateway_v2.domain.locks import FRESH_MS
from gateway_v2.domain.plan import (
    Action,
    ExecutionPlan,
    FailurePosture,
    Mode,
    Phase,
    PlanUnavailable,
    PlanUnknownTenant,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.plan.compiler import CompileError, RuleDraft, compile_plan
from gateway_v2.plan.lookup import applicable_rules, lookup_plan
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

SURFACES = (
    Surface.CHAT,
    Surface.MCP,
    Surface.RAG,
    Surface.VECTOR,
    Surface.EMBEDDINGS,
)


def _draft(
    rule_id: str = "r1",
    *,
    mode: Mode = Mode.ENFORCE,
    action: Action = Action.BLOCK,
    category: Category = Category.PROMPT_INJECTION,
    surfaces: frozenset[Surface] | None = None,
) -> RuleDraft:
    return RuleDraft(
        rule_id=rule_id,
        category=category,
        mode=mode,
        action=action,
        threshold=0.8,
        priority=10,
        scope=RuleScope.BOTH,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=SURFACES if surfaces is None else surfaces,
    )


def _compile(
    org: str,
    drafts: tuple[RuleDraft, ...] = (),
    previous: ExecutionPlan | None = None,
    at: float = 1.0,
) -> ExecutionPlan:
    return compile_plan(org, drafts, previous=previous, compiled_at=at)


def test_lgw05_1_empty_selection_is_a_plan_not_unavailable() -> None:
    plan = _compile("org-a")
    assert plan.rules == ()
    assert plan.required_detectors == frozenset()
    assert plan.integrity_locked is True
    store = PlanStore()
    store.put(plan)
    assert isinstance(store.read("org-a"), ExecutionPlan)


def test_lgw05_2_unreachable_known_tenant_is_unavailable() -> None:
    store = PlanStore()
    store.put(_compile("org-a", (_draft(),)))
    store.mark_unreachable("org-a")
    state = store.read("org-a")
    assert isinstance(state, PlanUnavailable)
    assert state.reason == "store_unreachable"
    assert not isinstance(store.read("org-b"), PlanUnavailable)


def test_unknown_tenant_is_not_unavailable() -> None:
    assert isinstance(PlanStore().read("never"), PlanUnknownTenant)


def test_flush_keeps_the_tenant_known() -> None:
    store = PlanStore()
    store.put(_compile("org-a"))
    store.forget_plan("org-a")
    state = store.read("org-a")
    assert isinstance(state, PlanUnavailable)
    assert state.reason == "plan_missing"


def test_lgw05_3_same_rules_on_every_surface_and_phase() -> None:
    plan = _compile("org-a", (_draft(),))
    store = PlanStore()
    store.put(plan)
    snap = ReplicaSnapshot(store, clock=lambda: 10.0)
    snap.absorb("org-a", 10.0)
    seen = set()
    for surface in SURFACES:
        state = lookup_plan(snap, "org-a", surface)
        assert isinstance(state, ExecutionPlan)
        assert state.version == plan.version
        for phase in (Phase.INPUT, Phase.OUTPUT):
            rules = applicable_rules(state, surface, phase)
            assert tuple(rule.rule_id for rule in rules) == ("r1",)
            seen.add((surface, phase, rules[0].action))
    assert len(seen) == 10


def test_lgw05_4_replicas_converge_without_regressing() -> None:
    """GW05c: convergence is driven per tenant (absorb), not by an O(tenants) reconcile.

    The assertions are unchanged from GW05 — two replicas reach the newer version and neither
    regresses. Only the mechanism that feeds them changed.
    """
    store = PlanStore()
    first = _compile("org-a", (_draft(action=Action.FLAG),), at=1.0)
    store.put(first)
    left = ReplicaSnapshot(store, clock=lambda: 1.0)
    right = ReplicaSnapshot(store, clock=lambda: 1.0)
    left.absorb("org-a", 1.0)
    right.absorb("org-a", 1.0)
    second = _compile("org-a", (_draft(action=Action.BLOCK),), previous=first, at=2.0)
    store.put(second)
    left.absorb("org-a", 2.0)
    right.absorb("org-a", 2.0)
    assert left.lookup("org-a", 2.0).version == second.version  # type: ignore[union-attr]
    assert right.lookup("org-a", 2.0).version == second.version  # type: ignore[union-attr]
    assert left.age_seconds("org-a", 2.0) == 0.0
    assert (left.age_seconds("org-a", 2.0) or 0) < FRESH_MS / 1000


def test_lgw05_5_pin_ignores_a_later_push() -> None:
    store = PlanStore()
    first = _compile("org-a", (_draft(action=Action.FLAG),))
    store.put(first)
    snap = ReplicaSnapshot(store, clock=lambda: 5.0)
    pinned = snap.pin("req-1", "org-a", 5.0)
    assert isinstance(pinned, ExecutionPlan)
    second = _compile("org-a", (_draft(action=Action.BLOCK),), previous=first, at=6.0)
    store.put(second)
    snap.absorb("org-a", 6.0)
    assert snap.lookup("org-a", 6.0).version == second.version  # type: ignore[union-attr]
    held = snap.pinned("req-1")
    assert held is not None
    assert held.version == first.version


def test_lgw05_6_concurrent_tenants_do_not_leak() -> None:
    store = PlanStore()
    store.put(_compile("org-a", (_draft(rule_id="a", action=Action.BLOCK),)))
    store.put(_compile("org-b", (_draft(rule_id="b", action=Action.ALLOW),)))
    snap = ReplicaSnapshot(store, clock=lambda: 3.0)
    found: list[tuple[str, str]] = []
    errors: list[BaseException] = []

    def _read(org: str) -> None:
        try:
            for _ in range(50):
                state = snap.lookup(org)
                assert isinstance(state, ExecutionPlan)
                found.append((org, state.rules[0].rule_id))
        except BaseException as exc:  # noqa: BLE001 — collect worker failures
            errors.append(exc)

    threads = [
        threading.Thread(target=_read, args=(org,))
        for org in ("org-a", "org-b")
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert {rule for org, rule in found if org == "org-a"} == {"a"}
    assert {rule for org, rule in found if org == "org-b"} == {"b"}


def test_lgw05_7_unsupported_selection_is_rejected() -> None:
    with pytest.raises(CompileError, match="incremental"):
        compile_plan("org-a", (), streaming_mode=StreamingMode.STRICT_WITHHOLD, compiled_at=1.0)
    with pytest.raises(CompileError, match="not supported"):
        _compile("org-a", (_draft(action=Action.REWRITE),))


def test_version_cannot_regress() -> None:
    first = _compile("org-a", (_draft(),))
    store = PlanStore()
    store.put(first)
    with pytest.raises(ValueError, match="regressed"):
        store.put(first)


def test_last_known_good_expires() -> None:
    store = PlanStore()
    store.put(_compile("org-a", (_draft(),)))
    snap = ReplicaSnapshot(store, clock=lambda: 0.0)
    snap.absorb("org-a", 0.0)
    store.mark_unreachable("org-a")
    assert isinstance(snap.lookup("org-a", 1.0), ExecutionPlan)
    later = snap.lookup("org-a", 1.0 + (FRESH_MS / 1000) + 0.1)
    assert isinstance(later, PlanUnavailable)


def test_off_rule_does_not_select_a_detector() -> None:
    plan = _compile("org-a", (_draft(mode=Mode.OFF),))
    assert plan.rules == ()
    assert plan.required_detectors == frozenset()
