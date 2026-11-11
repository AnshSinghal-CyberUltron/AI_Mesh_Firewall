"""GW05b phase 8: the handoff to GW06, machine-checked instead of merely written down.

GW05b finishes with four things implemented, tested, and NOT CALLED, because they need a
start-up path and an HTTP surface that `gateway_v2/edge/` does not have yet. A handoff document
alone is not enough to make that safe, and this programme has the evidence: GW05c left
`require_bounded_client` uncalled with a docstring explaining where it belonged, and a whole card
went by without anyone wiring it. Prose does not fail a build.

So this module did two jobs.

1. It PINS the surface GW06 is told to consume. If `state_ready` changes shape or a reason code
   is respelled, the instruction in the handoff document stops matching the code and this fails,
   rather than the two drifting quietly apart (C37's defect, one level down). This job remains.
2. `test_the_edge_layer_is_still_stubs` WAS a one-time TRIPWIRE that failed the moment somebody
   started building the HTTP surface -- exactly the moment the uncalled guards have to be wired.
   Its failure message said what to do and told you to delete it. GW12 built the real edge layer,
   so the tripwire fired as designed and has been deleted per its own instruction (see the note
   below the imports). Failing once, at the right time, with instructions, was the cheapest
   mechanism available for this.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from gateway_v2.domain.posture import (
    BUDGET_UNAVAILABLE,
    KILL_SWITCH_UNAVAILABLE,
    MIN_RETRY_AFTER_S,
    PLAN_UNAVAILABLE,
    SHARED_STATE_UNAVAILABLE,
    gap_retry_after_s,
)
from gateway_v2.domain.state_knobs import StateKnobs
from gateway_v2.runtime.state_stamp import StampView, state_ready
from gateway_v2.runtime.state_task import StateSynchroniser
from gateway_v2.runtime.store_valkey import require_bounded_client

HANDOFF = "docs/plans/2026-10-08-gw05b-handoff.md"


# NOTE: the one-time `test_the_edge_layer_is_still_stubs` TRIPWIRE has been removed. Its own
# failure message instructed deletion once the edge layer stopped being stubs and the four
# uncalled GW05b pieces were wired. GW12 (the SSE egress pipeline) built the real edge layer
# (edge/app.py, edge/cancel.py, edge/wire/, ...), so the tripwire fired as designed; per its
# instruction it is deleted here. The surface it pinned (state_ready, require_bounded_client,
# next_delay_s, is_deep_round, the posture vocabulary) is STILL pinned by the sibling tests below.


# --- the surface GW06 is told to consume ---------------------------------------------------------


def test_state_ready_has_the_signature_the_handoff_documents() -> None:
    signature = inspect.signature(state_ready)

    assert list(signature.parameters) == ["view", "now"]
    assert signature.parameters["view"].annotation == "StampView | None"
    assert signature.return_annotation == "tuple[bool, str | None]"


def test_state_ready_tolerates_an_unwired_deployment() -> None:
    """A deployment that has not yet built a StampView must still be able to start."""
    assert state_ready(None) == (True, None)


def test_state_ready_reports_a_reason_when_it_refuses() -> None:
    """GW06 renders this string; an empty one would make a 503 undiagnosable."""
    view = StampView(b"secret", fresh_ms=5_000, started_at=100.0, clock=lambda: 100.0)

    ready, why = state_ready(view)

    assert ready is False
    assert why and len(why) > 20


def test_require_bounded_client_has_the_signature_the_handoff_documents() -> None:
    signature = inspect.signature(require_bounded_client)

    assert list(signature.parameters) == ["client", "below_s"]
    assert signature.parameters["below_s"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_store_timeout_ceiling_is_what_require_bounded_client_wants() -> None:
    """C36: a store op outliving half a refresh period defeats the staleness window.

    The two halves of that rule live in different modules, so this pins that they agree.
    """
    knobs = StateKnobs(refresh_period_ms=500)

    assert knobs.store_timeout_ceiling_s == 0.25

    class _Bounded:
        connection_kwargs = {"socket_timeout": 0.2}

    class _Client:
        connection_pool = _Bounded()

    assert require_bounded_client(_Client(), below_s=knobs.store_timeout_ceiling_s) == 0.2


def test_the_synchroniser_exposes_the_cadence_hook() -> None:
    signature = inspect.signature(StateSynchroniser.next_delay_s)

    assert list(signature.parameters) == ["self", "reports", "period_s"]
    assert signature.parameters["period_s"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_knobs_expose_the_deep_round_cadence() -> None:
    signature = inspect.signature(StateKnobs.is_deep_round)

    assert list(signature.parameters) == ["self", "round_number"]


# --- the vocabulary two planes must agree on -----------------------------------------------------


@pytest.mark.parametrize(
    ("code", "spelling"),
    [
        (KILL_SWITCH_UNAVAILABLE, "kill_switch_unavailable"),
        (SHARED_STATE_UNAVAILABLE, "shared_state_unavailable"),
        (PLAN_UNAVAILABLE, "plan_unavailable"),
        (BUDGET_UNAVAILABLE, "budget_unavailable"),
    ],
)
def test_a_reason_code_keeps_its_spelling(code: str, spelling: str) -> None:
    """C37 one level down: two planes agreeing on behaviour and differing on spelling."""
    assert code == spelling


def test_the_posture_module_is_codes_and_one_function() -> None:
    """Codes only: no response type, no renderer, nothing `edge` would have to reconcile.

    The "no HTTPException/JSONResponse" half is already enforced for the whole tree by
    `lint.check_http_outside_edge_resolve`, so it is NOT re-asserted here -- a second, cruder
    copy of a gate is worse than one good one. (The first draft of this test compared raw text
    and tripped over the docstring that explains the rule.) What this adds is the shape: a
    vocabulary that grows a class is a vocabulary turning into a response model.
    """
    import ast

    source = (
        Path(__file__).resolve().parents[2] / "gateway_v2" / "domain" / "posture.py"
    ).read_text()
    tree = ast.parse(source)

    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    functions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert classes == [], f"posture is a vocabulary, not a model: {classes}"
    assert functions == ["gap_retry_after_s"], functions


def test_retry_after_is_derived_and_floored() -> None:
    assert gap_retry_after_s(rehydrate_period_ms=1_000, refresh_ms=500) == 1.5
    assert gap_retry_after_s(rehydrate_period_ms=1, refresh_ms=1) == MIN_RETRY_AFTER_S


# --- the handoff document itself -----------------------------------------------------------------


def test_the_handoff_document_exists() -> None:
    """The tripwire's failure message points at it, so a missing file makes that advice useless."""
    path = Path(__file__).resolve().parents[3] / HANDOFF

    assert path.is_file(), f"{HANDOFF} is referenced by this gate's failure message"
    text = path.read_text()
    for name in (
        "state_ready",
        "require_bounded_client",
        "next_delay_s",
        "is_deep_round",
        "ok_publish_pending",
    ):
        assert name in text, f"the handoff must name {name}"
