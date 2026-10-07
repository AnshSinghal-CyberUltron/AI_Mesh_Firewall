"""GW05c phase 3a — plan updates applied per record instead of by reconciling every tenant.

`test_applying_one_change_with_ten_thousand_tenants_touches_one` is the guard: it counts the
tenants the store is asked about, so a future reimplementation that iterates the estate fails
here rather than in a load test.
"""

from __future__ import annotations

import json

import pytest

from gateway_v2.domain.category import Category
from gateway_v2.domain.plan import (
    Action,
    ExecutionPlan,
    FailurePosture,
    Mode,
    PlanUnavailable,
    PlanUnknownTenant,
    RuleScope,
    StreamingMode,
    Surface,
)
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp, Version
from gateway_v2.plan.compiler import CompileError, RuleDraft
from gateway_v2.plan.delta import PlanDeltaApplier, plan_from_record
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_sig import make_record

SECRET = b"gw05c-delta-test-secret"
SURFACES = frozenset(
    {Surface.CHAT, Surface.MCP, Surface.RAG, Surface.VECTOR, Surface.EMBEDDINGS},
)


def _draft(
    rule_id: str = "r1",
    *,
    action: Action = Action.BLOCK,
    mode: Mode = Mode.ENFORCE,
) -> RuleDraft:
    return RuleDraft(
        rule_id=rule_id,
        category=Category.PROMPT_INJECTION,
        mode=mode,
        action=action,
        threshold=0.8,
        priority=10,
        scope=RuleScope.BOTH,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=SURFACES,
    )


def _plan_record(
    org_id: str,
    *,
    seq: int,
    feed_seq: int,
    epoch: int = 1,
    action: Action = Action.BLOCK,
    deleted: bool = False,
    body: dict[str, object] | None = None,
) -> SignedRecord:
    document = PlanDocument(org_id, StreamingMode.INCREMENTAL, (_draft(action=action),))
    return make_record(
        SECRET,
        StateKind.PLAN,
        org_id,
        encode_plan_body(document) if body is None else body,
        Version(epoch, seq),
        feed_seq,
        deleted=deleted,
        op=StateOp.OFFBOARD if deleted else StateOp.PUT,
    )


class CountingStore(PlanStore):
    """A PlanStore that counts which tenants it was asked about."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.known_calls: int = 0

    def read(self, org_id: str) -> ExecutionPlan | PlanUnavailable | PlanUnknownTenant:
        self.reads.append(org_id)
        return super().read(org_id)

    def put(self, plan: ExecutionPlan) -> None:
        self.writes.append(plan.org_id)
        super().put(plan)

    def known(self) -> tuple[str, ...]:
        self.known_calls += 1
        return super().known()

    def reset_counters(self) -> None:
        self.reads = []
        self.writes = []
        self.known_calls = 0


def _seed(store: PlanStore, tenants: int) -> None:
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    applier.apply(
        [
            _plan_record(f"org-{n}", seq=n, feed_seq=n, action=Action.FLAG)
            for n in range(1, tenants + 1)
        ],
    )


# --- the cost shape ---------------------------------------------------------------------------


def test_applying_one_change_with_ten_thousand_tenants_touches_one() -> None:
    """R2-02: the plan path must never enumerate the estate again."""
    store = CountingStore()
    _seed(store, 10_000)
    snapshot = ReplicaSnapshot(store, clock=lambda: 2.0)
    applier = PlanDeltaApplier(store, snapshot, clock=lambda: 2.0)
    store.reset_counters()

    outcome = applier.apply([_plan_record("org-77", seq=10_001, feed_seq=10_001)])

    assert outcome.applied == ("org-77",)
    assert outcome.touched == 1
    assert store.writes == ["org-77"]
    assert set(store.reads) == {"org-77"}, "no other tenant is read"
    assert store.known_calls == 0, "the estate is never enumerated"


def test_absorb_is_per_tenant_not_per_estate() -> None:
    store = CountingStore()
    _seed(store, 5_000)
    snapshot = ReplicaSnapshot(store, clock=lambda: 3.0)
    store.reset_counters()

    snapshot.absorb("org-42", 3.0)

    assert store.reads == ["org-42"]
    assert store.known_calls == 0


def test_a_reconcile_method_no_longer_exists() -> None:
    """The O(tenants) entry point is gone, not merely unused."""
    assert not hasattr(ReplicaSnapshot, "reconcile")


# --- idempotency and ordering ------------------------------------------------------------------


def test_a_replayed_record_is_skipped_not_an_error() -> None:
    """A nudge and a periodic round routinely deliver the same change twice."""
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    record = _plan_record("org-a", seq=1, feed_seq=1)

    first = applier.apply([record])
    second = applier.apply([record])

    assert first.applied == ("org-a",)
    assert second.skipped == ("org-a",)
    assert second.applied == ()


def test_an_older_record_does_not_regress_a_served_plan() -> None:
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    applier.apply([_plan_record("org-a", seq=5, feed_seq=5, action=Action.BLOCK)])

    outcome = applier.apply([_plan_record("org-a", seq=4, feed_seq=6, action=Action.FLAG)])

    assert outcome.skipped == ("org-a",)
    served = store.read("org-a")
    assert isinstance(served, ExecutionPlan)
    assert served.sequence == 5
    assert served.rules[0].action is Action.BLOCK


def test_an_epoch_bump_is_applied_even_though_sequence_fell() -> None:
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    applier.apply([_plan_record("org-a", seq=5, feed_seq=5, action=Action.BLOCK)])

    outcome = applier.apply(
        [_plan_record("org-a", epoch=2, seq=0, feed_seq=6, action=Action.FLAG)],
    )

    assert outcome.applied == ("org-a",)
    served = store.read("org-a")
    assert isinstance(served, ExecutionPlan)
    assert served.epoch == 2
    assert served.sequence == 0
    assert served.rules[0].action is Action.FLAG


def test_the_record_version_wins_over_the_compilers_own_counter() -> None:
    """Two workers must produce identical plans, so the version comes from Postgres."""
    record = _plan_record("org-a", epoch=3, seq=17, feed_seq=99)

    left = plan_from_record(record, compiled_at=1.0)
    right = plan_from_record(record, compiled_at=1.0)

    assert (left.epoch, left.sequence, left.feed_seq) == (3, 17, 99)
    assert left.version == right.version
    assert left.content_hash == right.content_hash


# --- failure is per tenant ---------------------------------------------------------------------


def test_a_rejected_body_leaves_the_previous_plan_serving() -> None:
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    applier.apply([_plan_record("org-a", seq=1, feed_seq=1, action=Action.FLAG)])

    outcome = applier.apply(
        [_plan_record("org-a", seq=2, feed_seq=2, body={"org_id": "org-a"})],
    )

    assert outcome.applied == ()
    assert [org for org, _ in outcome.rejected] == ["org-a"]
    served = store.read("org-a")
    assert isinstance(served, ExecutionPlan)
    assert served.sequence == 1, "the previous valid version keeps serving"


def test_a_rejected_body_for_a_new_tenant_is_unavailable_not_unknown() -> None:
    """The v2.1 defect: a tenant that cannot be compiled must never read as 'not onboarded'."""
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)

    outcome = applier.apply(
        [_plan_record("org-new", seq=1, feed_seq=1, body={"org_id": "org-new"})],
    )

    assert [org for org, _ in outcome.rejected] == ["org-new"]
    assert isinstance(store.read("org-new"), PlanUnavailable)


def test_one_bad_body_does_not_stop_the_round() -> None:
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)

    outcome = applier.apply(
        [
            _plan_record("org-a", seq=1, feed_seq=1),
            _plan_record("org-bad", seq=2, feed_seq=2, body={"org_id": "org-bad"}),
            _plan_record("org-c", seq=3, feed_seq=3),
        ],
    )

    assert outcome.applied == ("org-a", "org-c")
    assert [org for org, _ in outcome.rejected] == ["org-bad"]


def test_a_body_keyed_for_another_tenant_is_rejected() -> None:
    """Cross-tenant protection at the apply boundary, not only at the signature."""
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    document = PlanDocument("org-victim", StreamingMode.INCREMENTAL, (_draft(),))
    record = make_record(
        SECRET,
        StateKind.PLAN,
        "org-attacker",
        encode_plan_body(document),
        Version(1, 1),
        1,
    )

    outcome = applier.apply([record])

    assert [org for org, _ in outcome.rejected] == ["org-attacker"]
    assert isinstance(store.read("org-victim"), PlanUnknownTenant)


def test_a_non_plan_record_is_rejected() -> None:
    record = make_record(SECRET, StateKind.KEY, "hash-1", {"on": True}, Version(1, 1), 1)

    with pytest.raises(CompileError, match="cannot become a plan"):
        plan_from_record(record, compiled_at=1.0)


# --- offboarding ------------------------------------------------------------------------------


def test_an_explicit_off_record_offboards_the_tenant() -> None:
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    applier.apply([_plan_record("org-a", seq=1, feed_seq=1)])

    outcome = applier.apply([_plan_record("org-a", seq=2, feed_seq=2, deleted=True)])

    assert outcome.offboarded == ("org-a",)
    assert isinstance(store.read("org-a"), PlanUnknownTenant)


def test_offboarding_is_the_only_way_a_tenant_becomes_unknown() -> None:
    """forget_plan models a flush and must keep the tenant known; offboard is signed intent."""
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    applier.apply([_plan_record("org-a", seq=1, feed_seq=1)])

    store.forget_plan("org-a")
    assert isinstance(store.read("org-a"), PlanUnavailable)

    store.offboard("org-a")
    assert isinstance(store.read("org-a"), PlanUnknownTenant)


# --- the document format ------------------------------------------------------------------------


def test_a_plan_body_round_trips() -> None:
    document = PlanDocument(
        "org-a",
        StreamingMode.INCREMENTAL,
        (_draft("r1"), _draft("r2", action=Action.REDACT)),
    )

    decoded = decode_plan_body(json.dumps(encode_plan_body(document)).encode())

    assert decoded.org_id == "org-a"
    assert decoded.streaming_mode is StreamingMode.INCREMENTAL
    assert {draft.rule_id for draft in decoded.drafts} == {"r1", "r2"}


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"org_id":"org-a","streaming_mode":"incremental"}',
        b'{"org_id":"","streaming_mode":"incremental","rules":[]}',
        b'{"org_id":"org-a","streaming_mode":"dribble","rules":[]}',
        b'{"org_id":"org-a","streaming_mode":"incremental","rules":[{"rule_id":"r"}]}',
        b'{"org_id":"org-a","streaming_mode":"incremental","rules":["r"]}',
    ],
)
def test_a_malformed_body_is_a_compile_error(body: bytes) -> None:
    with pytest.raises(CompileError):
        decode_plan_body(body)


def test_strict_withhold_is_rejected_on_apply_not_served_as_incremental() -> None:
    """LGW05-7: a selection the resolver cannot represent must not be silently downgraded.

    `strict_withhold` is a real StreamingMode member, so the document decodes; the compiler is
    the authority that refuses it. The tenant is left PLAN_UNAVAILABLE, never served a different
    streaming mode than it asked for.
    """
    store = PlanStore()
    applier = PlanDeltaApplier(store, clock=lambda: 1.0)
    body = PlanDocument("org-a", StreamingMode.STRICT_WITHHOLD, (_draft(),))
    record = make_record(
        SECRET, StateKind.PLAN, "org-a", encode_plan_body(body), Version(1, 1), 1,
    )

    outcome = applier.apply([record])

    assert [org for org, _ in outcome.rejected] == ["org-a"]
    assert "not available yet" in outcome.rejected[0][1]
    assert isinstance(store.read("org-a"), PlanUnavailable)
