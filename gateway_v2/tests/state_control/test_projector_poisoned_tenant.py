"""Hotfix: one tenant's regex or plan body can no longer stop the projector round.

Before: a customer regex of 499 nested groups (999 chars, inside the 1000-char limit and past
every ReDoS-shape check) made `compile_conditions` raise `RecursionError`, which escaped
`validate_plan`, `desired_state` and `Projector.round_once`. Every round failed while the row
existed, the heartbeat aged, the re-hydrator withheld the stamp, and every gateway in the fleet
failed closed. A deeply nested JSON body did the same through `json.loads`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body
from gateway_v2.runtime.state_sig import canonical_body
from state_control import projector as projector_module
from state_control.projector import validate_plan
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_gw36_s2_projector import KNOBS, Lab, _doc, _rehydrator
from tests.state_control.test_lgw05c_writer import Gateway

DEEP = "(" * 499 + "a" + ")" * 499
DEEP_JSON = "[" * 100_000 + "]" * 100_000


def _body_with(pattern: str, slug: str = "acme", **extra: Any) -> str:
    body = encode_plan_body(PlanDocument(slug, StreamingMode.INCREMENTAL, (_draft(),)))
    rules = body["rules"]
    assert isinstance(rules, list)
    rules.append(
        {
            **rules[0],
            "rule_id": "customer-regex",
            "condition": {
                "kind": "regex", "field": "", "direction": rules[0]["scope"], "keywords": [],
                "pattern": pattern, "detector_class": "", "pipeline_stage": "",
            },
            **extra,
        },
    )
    return json.dumps(body)


@pytest.mark.parametrize("pattern", [DEEP, "(" * 17 + "a" + ")" * 17, "a{4294967296}"])
def test_a_regex_re_cannot_compile_is_a_counted_refusal_not_an_exception(pattern: str) -> None:
    re.purge()
    projected, refused, why = validate_plan("acme", _body_with(pattern))
    assert projected is not None and refused == 1 and why == ""


def test_sixteen_deep_is_projected_and_not_refused() -> None:
    projected, refused, why = validate_plan("acme", _body_with("(" * 16 + "a" + ")" * 16))
    assert projected is not None and refused == 0 and why == ""


def _validate_at_depth(frames: int, raw: str) -> tuple[Any, int, str]:
    if frames:
        return _validate_at_depth(frames - 1, raw)
    return validate_plan("acme", raw)


@pytest.mark.parametrize("frames", [0, 300, 600])
def test_the_verdict_does_not_depend_on_the_projectors_stack(frames: int) -> None:
    """Before the fix a 400-deep rule validated at a shallow stack and raised after ~200."""
    re.purge()
    projected, refused, _why = _validate_at_depth(frames, _body_with("(" * 400 + "a" + ")" * 400))
    assert projected is not None and refused == 1


@pytest.mark.parametrize(
    "raw",
    [
        DEEP_JSON,
        '{"org_id": "acme", "streaming_mode": "incremental", "rules": [], "x": '
        + DEEP_JSON + "}",
    ],
)
def test_deeply_nested_json_is_not_projectable_and_does_not_raise(raw: str) -> None:
    body, refused, why = validate_plan("acme", raw)
    assert body is None and refused == 0
    assert why == "body nests deeper than 32 levels", "refused before json recurses"


def test_a_rewrite_rule_with_a_deep_condition_is_a_compile_error_not_an_exception() -> None:
    """`compile_plan` runs `check_template`, which compiles the rule's own condition."""
    raw = _body_with(DEEP, action="rewrite", rewrite_template="Removed by policy")
    body, _refused, why = validate_plan("acme", raw)
    assert body is None and why == "CompileError: the plan does not compile"
    document = decode_plan_body(canonical_body(json.loads(raw)))
    with pytest.raises(CompileError, match="condition pattern is refused"):
        compile_plan("acme", document.drafts, compiled_at=0.0)


def test_any_unanticipated_exception_is_not_projectable(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("compiler bug " + "x" * 400)

    monkeypatch.setattr(projector_module, "compile_plan", explode)
    body, refused, why = validate_plan("acme", _doc("acme"))
    assert body is None and refused == 0
    assert why == "RuntimeError: unanticipated fault while validating", "the type, never the text"


# --- the round: one poisoned tenant, everyone else keeps being projected ---------------------


def test_a_poisoned_tenant_does_not_stop_the_round_or_the_fleet_stamp() -> None:
    lab = Lab()
    lab.org("healthy")
    lab.org("deep-regex")
    lab.org("deep-json")
    lab.projector.round_once()
    rehydrator = _rehydrator(lab, KNOBS.lag_ms)
    assert rehydrator.round_once().stamped is not None
    previous = lab.live(StateKind.PLAN)["deep-json"]

    # Past the lag bound: only a heartbeat from THIS round can keep the stamp.
    lab.clock.now += KNOBS.lag_ms / 1000 + 1
    re.purge()
    lab.source.plan("deep-regex", _body_with(DEEP, "deep-regex"))
    lab.source.plan("deep-json", DEEP_JSON)
    lab.source.org("new-healthy")
    lab.source.plan("new-healthy", _doc("new-healthy"))

    summary = lab.projector.round_once()

    assert summary.complete and summary.heartbeat
    assert summary.refused_conditions == 1, "the deep regex is counted, not fatal"
    assert summary.invalid_plans == 1, "the deep JSON body is not projectable"
    plans = lab.live(StateKind.PLAN)
    assert "new-healthy" in plans, "the healthy tenant's plan is signed in the same round"
    assert plans["deep-json"] == previous, "the unprojectable tenant keeps its previous record"
    assert plans["deep-regex"] == json.loads(_body_with(DEEP, "deep-regex"))
    assert lab.db.source_lag_s() == pytest.approx(0.0), "the heartbeat advanced"
    stamp = rehydrator.round_once()
    assert stamp.stamped is not None and not stamp.source_lagging, "the fleet keeps its stamp"

    gateway = Gateway(lab.store)
    gateway.poll(StateKind.PLAN)
    for slug in ("healthy", "new-healthy", "deep-regex", "deep-json"):
        assert isinstance(gateway.plans.read(slug), ExecutionPlan), slug

    assert lab.projector.round_once().writes == 0, "a standing poisoned row is a steady state"
