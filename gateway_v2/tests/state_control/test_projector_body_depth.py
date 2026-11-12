"""#117 review B1 / A-S2: the projector signs no plan body a gateway cannot decode.

`json` decodes recursively, and how deep it gets before `RecursionError` depends on the C stack
its caller already uses. The projector, on its own stack, signed an unknown-key array ~9,996
deep that the gateway applier, deeper in its stack, could not decode (`document.py:233` via
`delta.py:57`), and that applier catches only `CompileError`: one signed record aborted every
gateway's PLAN round. Now a body deeper than `MAX_PLAN_BODY_DEPTH` (32; real bodies are at most
5) is not projectable, decided by a linear scan before `json` sees it, and everything signed
decodes on any stack.

The gateway applier's own bound (`plan/document.py` `MAX_BODY_DEPTH` / `nesting_depth`, #136)
must agree with this one; the last test asserts that once both are on the same tree.
"""

from __future__ import annotations

import importlib
import json
import random
from collections.abc import Callable
from typing import Any

import pytest

from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.domain.state import StateKind
from gateway_v2.plan.delta import plan_from_record
from gateway_v2.plan.document import PlanDocument, encode_plan_body
from state_control.projector import MAX_PLAN_BODY_DEPTH, body_depth, validate_plan
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_gw36_s2_projector import KNOBS, Lab, _doc, _rehydrator
from tests.state_control.test_lgw05c_writer import Gateway

TOO_DEEP = "body nests deeper than 32 levels"


def _deep(slug: str, depth: int) -> str:
    """A valid plan whose TOTAL nesting is ``depth``: the body object plus an unknown key holding
    ``depth - 1`` nested arrays, which `decode_plan_body` ignores (the B1 shape)."""
    raw = _doc(slug)
    assert raw.endswith("}")
    inner = depth - 1
    return raw[:-1] + ', "extra": ' + "[" * inner + '"x"' + "]" * inner + "}"


def _through_c_frames(levels: int, call: Callable[[], Any]) -> Any:
    """Run ``call`` under ``levels`` nested C-to-Python transitions (``map`` callbacks).

    Each transition uses C stack as well as a Python frame, and `json`'s C recursion budget is
    shared with them: that is how the gateway's deeper stack decoded less than the projector's.
    """
    if levels == 0:
        return call()
    return next(map(lambda _: _through_c_frames(levels - 1, call), [None]))


BODY_DEPTH_VECTORS: tuple[tuple[str, int], ...] = (
    ("", 0),
    ("1", 0),
    ("{}", 1),
    ("[]", 1),
    ("[[]]", 2),
    ('{"a": [{"b": [1]}]}', 4),
    ('"[[[["', 0),  # brackets inside a string do not count
    ('["\\"[[[["]', 1),  # an escaped quote does not end the string
    ('["\\\\", [[]]]', 3),  # an escaped backslash does, so the brackets after it count
    ('["\\u005b[", []]', 2),  # an escaped `[` is a string character
    ("[[[", 3),  # malformed: at least the depth json reaches before it gives up
    ("]]][[[", 0),  # never negative; past json's first defect (`]`) it never recurses
    ('{"k": "unterminated [[[[', 5),  # an unterminated string over-counts: refuses more
)


@pytest.mark.parametrize(("raw", "expected"), BODY_DEPTH_VECTORS)
def test_body_depth_vectors(raw: str, expected: int) -> None:
    assert body_depth(raw) == expected


def test_real_bodies_are_far_below_the_limit() -> None:
    assert MAX_PLAN_BODY_DEPTH == 32
    assert body_depth(_doc("acme")) <= 5
    assert body_depth(_deep("acme", 32)) == 32 and body_depth(_deep("acme", 33)) == 33


def test_the_deepest_accepted_body_is_signed_and_one_more_level_is_not() -> None:
    signed, refused, why = validate_plan("acme", _deep("acme", 32))
    assert signed is not None and refused == 0 and why == ""
    for depth in (33, 9_993, 9_996, 10_000, 100_000):
        body, refused, why = validate_plan("acme", _deep("acme", depth))
        assert body is None and refused == 0 and why == TOO_DEEP, depth


@pytest.mark.parametrize("levels", [0, 100, 400])
def test_the_verdict_and_the_gateway_decode_do_not_depend_on_the_stack(levels: int) -> None:
    """32 is accepted and 33 refused under any stack; the signed 32 decodes on a deep one."""
    accepted = _through_c_frames(levels, lambda: validate_plan("acme", _deep("acme", 32)))
    refused = _through_c_frames(levels, lambda: validate_plan("acme", _deep("acme", 33)))
    assert accepted[0] is not None and refused[2] == TOO_DEEP

    lab = Lab()
    lab.org("acme", plan=False)
    lab.source.plan("acme", _deep("acme", 32))
    lab.projector.round_once()
    (record,) = (r for r in lab.db.snapshot(StateKind.PLAN)[1] if r.key == "acme")
    plan = _through_c_frames(levels, lambda: plan_from_record(record, compiled_at=1.0))
    assert isinstance(plan, ExecutionPlan)


def test_a_round_signs_the_deepest_body_refuses_deeper_and_every_other_tenant_proceeds() -> None:
    lab = Lab()
    healthy = [f"good-{n:02d}" for n in range(20)]
    for slug in (*healthy, "deep-33", "deep-10k"):
        lab.org(slug)
    lab.projector.round_once()
    rehydrator = _rehydrator(lab, KNOBS.lag_ms)
    assert rehydrator.round_once().stamped is not None
    previous = lab.live(StateKind.PLAN)

    # Past the lag bound: only a heartbeat from THIS round can keep the stamp.
    lab.clock.now += KNOBS.lag_ms / 1000 + 1
    lab.source.org("deepest")
    lab.source.plan("deepest", _deep("deepest", 32))
    lab.source.plan("deep-33", _deep("deep-33", 33))
    lab.source.plan("deep-10k", _deep("deep-10k", 10_000))
    lab.source.org("new-healthy")
    lab.source.plan("new-healthy", _doc("new-healthy"))

    summary = lab.projector.round_once()

    assert summary.complete and summary.heartbeat
    assert summary.invalid_plans == 2 and summary.refused_conditions == 0
    plans = lab.live(StateKind.PLAN)
    assert plans["deepest"] == json.loads(_deep("deepest", 32)), "the deepest accepted is signed"
    assert plans["deep-33"] == previous["deep-33"], "too deep: the previous record stays"
    assert plans["deep-10k"] == previous["deep-10k"]
    assert "new-healthy" in plans and all(slug in plans for slug in healthy)
    assert lab.db.source_lag_s() == pytest.approx(0.0), "the heartbeat advanced"
    stamp = rehydrator.round_once()
    assert stamp.stamped is not None and not stamp.source_lagging, "the fleet keeps its stamp"

    gateway = Gateway(lab.store)
    report = _through_c_frames(200, lambda: gateway.poll(StateKind.PLAN))
    assert report.ok
    for slug in (*healthy, "deepest", "deep-33", "deep-10k", "new-healthy"):
        assert isinstance(gateway.plans.read(slug), ExecutionPlan), slug
    assert lab.projector.round_once().writes == 0, "a standing too-deep row is a steady state"


def _random_json_ish(rng: random.Random) -> str:
    return "".join(rng.choice('[]{}"\\ ,:a1é') for _ in range(rng.randint(0, 60)))


def test_the_gateway_applier_bound_agrees_once_both_are_on_develop() -> None:
    """TWIN with #136 (`gateway_v2.plan.document.nesting_depth` / `MAX_BODY_DEPTH`).

    Skipped while the gateway side is not on this tree; it must pass, not skip, after both merge.
    """
    document = importlib.import_module("gateway_v2.plan.document")
    gateway_depth = getattr(document, "nesting_depth", None)
    gateway_max = getattr(document, "MAX_BODY_DEPTH", None)
    if gateway_depth is None or gateway_max is None:
        pytest.skip("the gateway applier's depth bound (#136) is not on this tree yet")
    assert gateway_max == MAX_PLAN_BODY_DEPTH
    rng = random.Random(0xB1)
    samples = [raw for raw, _ in BODY_DEPTH_VECTORS] + [_deep("acme", 32), _deep("acme", 33)]
    samples += [_random_json_ish(rng) for _ in range(5_000)]
    for raw in samples:
        assert gateway_depth(raw.encode("utf-8")) == body_depth(raw), repr(raw[:80])


def test_a_plan_with_every_section_stays_within_five_levels() -> None:
    """The headroom claim: controls, compliance lists, keywords and RAG settings included."""
    from gateway_v2.domain.plan import AuditControl, OrgControls, RateControl

    controls = OrgControls(
        rate=RateControl(enabled=True, requests_per_minute=60, burst=10),
        org_tpm_limit=1000,
        max_response_tokens=500,
        audit=AuditControl(enabled=True, incident_logging=True, retention_days=30),
        compliance_frameworks=("gdpr", "hipaa"),
    )
    document = PlanDocument(
        "acme", StreamingMode.INCREMENTAL, (_draft(),), controls=controls,
        rag_settings={"top_k": 4},
    )
    assert body_depth(json.dumps(encode_plan_body(document))) <= 5
