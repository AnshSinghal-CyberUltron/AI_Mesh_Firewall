# Feature: chat-surface-gw15
# Validates: Requirements 11.2, 11.3, 15.1, 15.2
"""Shared in-process §10.4 lifecycle fixtures for the ASGI chat-surface tests.

The GW15 chat handler (``edge/routes.py::ChatRoute.handle``) runs the full §10.4
request lifecycle (pre-checks → parse → identity → the ONE plan round trip → admission
→ budget → input scan) BEFORE any provider is opened. ``build_app``'s DEFAULT lifecycle
deps are fail-CLOSED (``fail_closed_resolver`` / ``fail_closed_admission`` /
``unresolved_snapshot_source``), so an unwired app refuses every request with zero
provider calls — correct for production safety, but it means a test that wants to drive
the REAL streaming/non-streaming surface end-to-end must inject *passing* deps that
resolve an identity, resolve a plan, admit, and pass budget.

This module is the ONE place those passing in-process deps live, so the GW12 ASGI
harness (``test_lgw12_app.py`` / ``test_lgw12_asgi_conformance.py``) and the GW15
in-process conformance subset (``tests/openai_conformance/test_gw15_chat_surface_v2.py``)
share identical wiring rather than each re-deriving it. Everything here is an in-process,
socket-free stand-in: the ``StubProviderClient`` is the injected provider substitute
(R11.2) and the real HTTP/SSE socket stays a DEFERRED named seam (R11.3).

These are TEST fakes (``tests/`` is outside the import-linter layer contract and the
``gateway_v2`` AST/size gates), so they may import freely across layers and need not
carry capacity-literal / module-mutable discipline. They are intentionally minimal: a
canned resolving identity, a warmed plan snapshot, always-admit admission + budget, and
generous pre-check bounds that never trip the Pre_Identity_Clock gate — exactly the
shape the shipped ``test_lgw15_refusal.py`` fakes use for the phases BEFORE admission.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.identity_resolver import TenantIdentity
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

__all__ = (
    "WELL_FORMED_CHAT_BODY",
    "AdmittingAdmission",
    "AdmittingBudget",
    "ResolvingIdentity",
    "chat_body",
    "pass_through_plan",
    "warm_snapshot_source",
)

#: The default test tenant every passing fixture is scoped to.
DEFAULT_ORG_ID = "gw15-conformance-org"


def chat_body(
    *,
    content: str = "Say hello politely.",
    model: str = "gpt-4o-mini",
    stream: bool = False,
) -> bytes:
    """A well-formed OpenAI chat/completions body (with the required ``model`` field).

    The §10.4 parser (``edge/wire/openai.py::OpenAIRequestParser``) rejects a
    ``model``-less body with the declared ``malformed_request_missing_model`` code — the
    stock OpenAI SDK ALWAYS sends ``model``, so a model-less body is genuinely
    off-contract. This builds the minimal in-contract body the parser accepts.
    """
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }
    if stream:
        body["stream"] = True
    return json.dumps(body).encode("utf-8")


#: A ready-to-send well-formed non-streaming chat body (the common fixture shape).
WELL_FORMED_CHAT_BODY = chat_body()


class ResolvingIdentity:
    """An :class:`IdentityResolver` that resolves one canned tenant for every request.

    The identity phase must PASS (never fail closed) so the lifecycle reaches the plan,
    admission, budget, and input phases and ultimately the provider. Mirrors the shipped
    ``test_lgw15_refusal.py`` fake: a fixed :class:`TenantIdentity` whose ``org_id`` keys
    the plan round trip, the admission door, and the budget lease.
    """

    __slots__ = ("_identity",)

    def __init__(self, org_id: str = DEFAULT_ORG_ID) -> None:
        self._identity = TenantIdentity(
            org_id=org_id,
            principal=Principal(
                key_id="gw15-key",
                org_id=org_id,
                rate_per_s=1.0,
                burst=1.0,
                feed_seq=0,
            ),
        )

    async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
        del scope
        return self._identity


class AdmittingAdmission:
    """An admission decider that always admits — the lifecycle reaches budget + provider."""

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        del enqueued_at
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=0.0)

    def complete(self, request_id: str) -> None:
        del request_id


class AdmittingBudget:
    """A budget decider that always admits — the lifecycle reaches the input scan + provider."""

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return Admitted(owner_id=DEFAULT_ORG_ID, request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        del cost, request_id


def pass_through_plan(org_id: str = DEFAULT_ORG_ID) -> ExecutionPlan:
    """A minimal resolvable :class:`ExecutionPlan` (no rules → pass-through enforcement)."""
    return ExecutionPlan(
        org_id=org_id,
        epoch=1,
        sequence=1,
        content_hash="gw15" + "0" * 60,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def warm_snapshot_source(
    plan: ExecutionPlan | None = None,
    org_id: str = DEFAULT_ORG_ID,
) -> Callable[[], ReplicaSnapshot]:
    """A ``snapshot_source`` whose served snapshot resolves ``org_id`` to ``plan``.

    Warms the served last-known-good so ``lookup_plan`` resolves deterministically — the
    same pattern as ``test_lgw15_refusal.py::_p8_snapshot_source``.
    """
    resolved = plan if plan is not None else pass_through_plan(org_id)
    store = PlanStore()
    store.put(resolved)
    snapshot = ReplicaSnapshot(store)
    snapshot.absorb(org_id, now=0.0)

    def _source() -> ReplicaSnapshot:
        return snapshot

    return _source
