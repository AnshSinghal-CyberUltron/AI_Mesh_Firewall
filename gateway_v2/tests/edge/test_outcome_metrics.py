"""What each terminal outcome counts as on ``/metrics``, matching the audit admission rule."""

from __future__ import annotations

from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.edge.outcome_metrics import observe_outcome, observe_refusal, observe_tenant
from gateway_v2.edge.request_holds import RequestHolds


class _Recorder:
    def __init__(self) -> None:
        self.tenants: list[str] = []
        self.rejections: list[tuple[int, str]] = []
        self.postures: list[str] = []

    def count_tenant(self, tenant_key: str) -> None:
        self.tenants.append(tenant_key)

    def observe_rejection(self, status: int, reason: str) -> None:
        self.rejections.append((status, reason))

    def observe_posture(self, code: str) -> None:
        self.postures.append(code)


class _Broken:
    def count_tenant(self, tenant_key: str) -> None:
        raise RuntimeError("boom")

    def observe_rejection(self, status: int, reason: str) -> None:
        raise RuntimeError("boom")

    def observe_posture(self, code: str) -> None:
        raise RuntimeError("boom")


def _allow() -> Decision:
    return Decision(
        disposition=Disposition.ALLOW,
        per_finding=(),
        transformations=(),
        findings=(),
        plan_version="v1",
        deciding_rules=(),
        unavailable_detectors=(),
    )


def _holds(org: str = "org-a", *, decided: bool = False) -> RequestHolds:
    holds = RequestHolds()
    holds.note_tenant(org)
    if decided:
        holds.note_input(_allow())
    return holds


def test_served_request_counts_only_the_tenant() -> None:
    seen = _Recorder()
    observe_outcome(seen, _holds(decided=True), None)
    assert (seen.tenants, seen.rejections, seen.postures) == (["org-a"], [], [])


def test_overload_shed_is_a_shed_rejection_and_a_posture() -> None:
    seen = _Recorder()
    observe_outcome(seen, _holds(), "overload_shed")
    assert seen.rejections == [(503, "shed")]
    assert seen.postures == ["overload_shed"]


def test_refusal_before_any_decision_is_a_rejection() -> None:
    seen = _Recorder()
    observe_outcome(seen, _holds(), "mcp_server_not_found")
    assert seen.rejections == [(404, "rejected")]
    assert seen.postures == []


def test_refusal_after_a_decision_is_not_a_rejection() -> None:
    seen = _Recorder()
    observe_outcome(seen, _holds(decided=True), "input_blocked")
    assert (seen.rejections, seen.postures) == ([], [])


def test_unresolved_identity_counts_no_tenant() -> None:
    seen = _Recorder()
    holds = RequestHolds()
    observe_outcome(seen, holds, "shared_state_unavailable")
    assert seen.tenants == []
    assert seen.postures == ["shared_state_unavailable"]
    assert seen.rejections == [(503, "rejected")]


def test_no_observer_and_a_broken_observer_never_raise() -> None:
    observe_outcome(None, _holds(), "overload_shed")
    observe_outcome(_Broken(), _holds(), "overload_shed")
    observe_refusal(_Broken(), "plan_unavailable", decided=False)
    observe_tenant(_Broken(), "org-a")
