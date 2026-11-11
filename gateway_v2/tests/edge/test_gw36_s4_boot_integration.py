"""GW36 S4 production-boot integration: the real admission + quota + BUDGET readiness wiring.

This is the edge-level integration half of task 10.7 (the pure/property coverage lives in the
`admit`/`state_control` suites). It asserts three things end to end over the SAME boot harness the
S3 tests use (`serve.build_boot_dependencies(contract=..., env={"AMF_PROFILE": "production", ...})`)
and the `_call(app, ...)` readyz idiom from `test_gw36_s3_state_identity`:

* Req 2.1 / 2.2 — after `apply_production`, `ChatRoute.admission` is the real GW19
  `AdmissionController` and `ChatRoute.quota` is the composed `QuotaGate`, and NEITHER fail-closed
  default (`fail_closed_admission` / `fail_closed_budget`) remains in `deps`.
* Req 7.5 — `/readyz` gates on BUDGET: BUDGET is in `state_runtime.READY_KINDS`, and before the
  state lifespan has run (nothing caught up / the stamp is stale) readiness is `False` and `/readyz`
  returns 503, with BUDGET a valid unready reason the kind-gate can name.
* Req 8.4 — a committed budget change is readable through `SourceView.budgets` within one projector
  round: the `MemorySource` twin exposes the change immediately in its next `read()`, and one
  `projector.round_once()` over that source writes the org's signed BUDGET record in that single
  round. Placed here (not in `tests/state_control/`) so all four of 10.7's requirements stay in one
  traceable file; it drives the shipped S2/S4 projector `Lab` harness so nothing new is invented and
  the read-view path is exercised entirely in-memory (no store, no live stack).

Everything is construction-only / in-memory: `build_boot_dependencies` is the parent's throw-away
refuse-to-start probe (no store I/O), `_call` drives the ASGI app WITHOUT the lifespan so no state
cycle runs, and the projector half uses the `MemorySource`/`MemoryControlDB`/`MemoryStore` twins
with a `FakeSeeder` in place of the live pool counter.
"""

from __future__ import annotations

import asyncio

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.domain.state import StateKind
from gateway_v2.edge import serve
from gateway_v2.edge.chat_deps import fail_closed_admission, fail_closed_budget
from gateway_v2.edge.quota_gate import QuotaGate
from gateway_v2.edge.state_runtime import READY_KINDS, StateRuntime
from tests.edge.test_dev_profile_boot import _contract
from tests.edge.test_gw36_s3_boot import STATE
from tests.edge.test_gw36_s3_state_identity import _call
from tests.state_control.test_gw36_s4_projector_budget import Lab as ProjectorLab

_PROD_ENV = {"AMF_PROFILE": "production", **STATE}


# --- Req 2.1 / 2.2: the real admission controller and composed quota gate are wired ----------


def test_apply_production_wires_the_real_admission_controller_and_quota_gate() -> None:
    """Req 2.1, 2.2: `deps["admission"]` is an `AdmissionController` and `deps["quota"]` is a
    `QuotaGate`; neither fail-closed default remains."""
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=_PROD_ENV)

    assert isinstance(deps["admission"], AdmissionController), "the real GW19 controller is wired"
    assert isinstance(deps["quota"], QuotaGate), "the composed S4 quota gate is wired"


def test_neither_fail_closed_default_survives_the_production_overlay() -> None:
    """Req 2.2: the fail-closed shed/refuse defaults are REPLACED, not left behind."""
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=_PROD_ENV)

    assert deps["admission"] is not fail_closed_admission, "admission is no longer fail-closed"
    assert deps["quota"] is not fail_closed_budget, "quota is no longer fail-closed"


# --- Req 7.5: /readyz gates on BUDGET --------------------------------------------------------


def test_budget_is_a_readiness_kind() -> None:
    """Req 7.5: BUDGET participates in readiness gating alongside KEY/PLAN/KS."""
    assert StateKind.BUDGET in READY_KINDS


def test_a_cold_runtime_is_unready_and_budget_is_a_valid_unready_reason() -> None:
    """Req 7.5: before the lifespan has run, nothing is caught up, so `ready()` is `(False, ...)`
    and the BUDGET gate can name itself as the unavailable dependency."""
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=_PROD_ENV)
    runtime = deps["state_runtime"]
    assert isinstance(runtime, StateRuntime)

    ready, reason = runtime.ready()
    assert ready is False, "a worker that has not caught up is not ready"
    assert reason, "an unready runtime reports why"

    # BUDGET is gated exactly like the other kinds: its own kind-gate names it when it has not been
    # read to its head, which is the state `/readyz` returns before any cycle ran.
    budget_reason = runtime.gates.lag_reason(StateKind.BUDGET)
    assert budget_reason is not None and StateKind.BUDGET.value in budget_reason


def test_readyz_returns_503_before_the_state_lifespan_runs() -> None:
    """Req 7.5: driving `/readyz` through the assembled app WITHOUT the lifespan (so no kind is
    caught up / the stamp is stale) returns 503 — the budget phase cannot admit against stale
    state."""
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=_PROD_ENV)
    app = serve._assemble_app(deps)

    async def go() -> tuple[int, bytes]:
        status, _headers, body = await _call(app, "GET", "/readyz")
        return status, body

    status, body = asyncio.run(go())
    assert status == 503 and body, "a cold worker refuses readiness"


# --- Req 8.4: a committed budget change is readable through SourceView.budgets within one round --


def test_a_committed_budget_change_is_readable_through_the_source_view_immediately() -> None:
    """Req 8.4: the moment a budget is committed in control, the projector's read view carries it —
    `SourceView.budgets` contains the org's `SourceBudget` on the very next `read()`."""
    lab = ProjectorLab()
    lab.source.org("acme")
    lab.source.budget("acme", 250_000)

    view = lab.source.read()
    budgets = {budget.org_slug: budget.pool_tokens for budget in view.budgets}
    assert budgets["acme"] == 250_000, "the committed change is visible in the read view at once"


def test_one_projector_round_publishes_the_budget_record_for_the_changed_org() -> None:
    """Req 8.4: a committed budget change is reflected by the projector within ONE round — a single
    `round_once()` over the source writes the org's signed BUDGET record with its pool tokens."""
    lab = ProjectorLab()
    lab.source.org("acme")
    lab.source.budget("acme", 250_000)

    summary = lab.projector.round_once()

    assert summary.heartbeat and summary.complete, "the round completed"
    assert lab.budgets()["acme"] == {"org_id": "acme", "pool_tokens": 250_000}, (
        "the change is visible to the projector's write in one round"
    )
    # The live pool seed is driven off the same single round with the record's own generation.
    assert lab.seeder.calls == [("acme", 250_000, lab.feed_seq_of("acme"))]
