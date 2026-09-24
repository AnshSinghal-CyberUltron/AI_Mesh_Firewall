"""Resolver: action matrix for the two tenant plans + the runbook §10.5.3 properties."""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rvproto.domain.decision import DispatchAuthorization, Disposition
from rvproto.domain.findings import DETECTORS, Finding, FindingStatus, Span, skipped, unavailable
from rvproto.domain.plan import Phase
from rvproto.plan.compiler import PlanInvalid, compile_plan
from rvproto.plan.fixtures import org_a, org_b
from rvproto.resolve.resolver import authorize, resolve

A = compile_plan(org_a())
B = compile_plan(org_b())
V = "t"


def hit(det: str, conf: float = 1.0, seg: int = 0) -> Finding:
    return Finding(det, V, DETECTORS[det], FindingStatus.EXECUTED, conf, (Span(seg, 0, 4),), "x")


def clean(det: str) -> Finding:
    return Finding(det, V, DETECTORS[det], FindingStatus.EXECUTED, 0.0, (), None)


def sem(p: float) -> Finding:
    return Finding("injection.pg2", V, DETECTORS["injection.pg2"], FindingStatus.EXECUTED, p, (), "w")


@pytest.mark.parametrize(
    ("findings", "a", "b"),
    [
        ([clean("pii.email"), sem(0.01)], Disposition.ALLOW, Disposition.ALLOW),
        ([hit("pii.email"), sem(0.01)], Disposition.REDACT, Disposition.ALLOW),
        ([hit("secret.aws"), sem(0.01)], Disposition.BLOCK, Disposition.ALLOW),
        ([sem(0.99)], Disposition.BLOCK, Disposition.FLAG),
        ([hit("pii.email"), hit("secret.aws")], Disposition.BLOCK, Disposition.ALLOW),  # LGW07-6
        ([unavailable("injection.pg2", V, "down")], Disposition.BLOCK, Disposition.ALLOW),
        ([hit("injection.heuristic"), sem(0.01)], Disposition.ALLOW, Disposition.ALLOW),  # signal only
    ],
)
def test_matrix(findings: list[Finding], a: Disposition, b: Disposition) -> None:
    assert resolve(findings, A, Phase.INPUT).disposition is a
    assert resolve(findings, B, Phase.INPUT).disposition is b


def test_unavailable_is_recorded_never_clean() -> None:
    f = [unavailable("injection.pg2", V, "down")]
    da, db = resolve(f, A, Phase.INPUT), resolve(f, B, Phase.INPUT)
    assert da.disposition is Disposition.BLOCK and da.deciding_rules == ("A.inj.sem",)
    assert db.disposition is Disposition.ALLOW and db.unavailable_detectors == ("injection.pg2",)


def test_block_mints_no_authorization_and_capability_is_unforgeable() -> None:
    d = resolve([hit("secret.aws")], A, Phase.INPUT)
    assert authorize(d, "r1") is None
    ok = authorize(resolve([clean("pii.email")], A, Phase.INPUT), "r1")
    assert isinstance(ok, DispatchAuthorization)
    with pytest.raises(PermissionError):
        DispatchAuthorization("r1", "a-1", Disposition.ALLOW, 0, object())
    with pytest.raises(PermissionError):
        dataclasses.replace(ok, disposition=Disposition.BLOCK)


def test_output_monitor_does_not_enforce() -> None:
    assert resolve([hit("pii.email")], B, Phase.OUTPUT).disposition is Disposition.ALLOW
    d = resolve([hit("pii.email")], A, Phase.OUTPUT)
    assert d.disposition is Disposition.REDACT and len(d.transformations) == 1


def test_compiler_rejects_bad_plans() -> None:
    bad = org_a()
    bad["rules"][0]["threshold"] = None
    with pytest.raises(PlanInvalid):
        compile_plan(bad)
    dup = org_a()
    dup["rules"].append(dict(dup["rules"][1], rule_id="dup"))
    with pytest.raises(PlanInvalid):
        compile_plan(dup)


def test_finding_category_must_match_catalogue() -> None:
    with pytest.raises(ValueError):
        Finding("pii.email", V, DETECTORS["secret.aws"], FindingStatus.EXECUTED, 1.0, (), None)


# ---- properties (runbook §10.5.3 / GW07) -----------------------------------------

DET = sorted(DETECTORS)


@st.composite
def findings_st(draw: st.DrawFn) -> list[Finding]:
    out = []
    for det in draw(st.lists(st.sampled_from(DET), max_size=8, unique=True)):
        status = draw(st.sampled_from(list(FindingStatus)))
        if status is FindingStatus.EXECUTED:
            conf = draw(st.floats(0, 1))
            spans = (Span(0, 0, 1),) if draw(st.booleans()) else ()
            out.append(Finding(det, V, DETECTORS[det], status, conf, spans, None))
        elif status is FindingStatus.SKIPPED:
            out.append(skipped(det, V))
        else:
            out.append(unavailable(det, V, "x"))
    return out


PLANS = st.sampled_from([A, B])
PHASES = st.sampled_from([Phase.INPUT, Phase.OUTPUT])


@settings(max_examples=2500, deadline=None)
@given(findings_st(), PLANS, PHASES)
def test_deterministic(f: list[Finding], plan: object, phase: Phase) -> None:
    assert resolve(f, plan, phase) == resolve(list(f), plan, phase)  # type: ignore[arg-type]


@settings(max_examples=2500, deadline=None)
@given(findings_st(), PLANS, PHASES)
def test_disposition_derivable_from_enforce_rule(f: list[Finding], plan: object, phase: Phase) -> None:
    d = resolve(f, plan, phase)  # type: ignore[arg-type]
    if d.disposition is Disposition.ALLOW and not d.deciding_rules:
        return
    rules = {r.rule_id: r for r in plan.rules}  # type: ignore[attr-defined]
    assert d.deciding_rules and all(rules[r].mode.value == "enforce" for r in d.deciding_rules)


@settings(max_examples=2500, deadline=None)
@given(findings_st(), PLANS, PHASES)
def test_skipped_never_changes_disposition(f: list[Finding], plan: object, phase: Phase) -> None:
    base = resolve(f, plan, phase)  # type: ignore[arg-type]
    extra = [skipped(d, V) for d in DET if d not in {x.detector for x in f}]
    assert resolve(f + extra, plan, phase).disposition is base.disposition  # type: ignore[arg-type]


@settings(max_examples=2500, deadline=None)
@given(findings_st(), PHASES)
def test_monitor_never_changes_disposition(f: list[Finding], phase: Phase) -> None:
    """Turning any ENFORCE rule into MONITOR can only remove its contribution."""
    doc = org_a()
    for i, r in enumerate(doc["rules"]):
        if r["mode"] != "enforce":
            continue
        m = org_a()
        m["rules"][i]["mode"] = "monitor"
        plan_m = compile_plan(m)
        d = resolve(f, plan_m, phase)
        assert r["rule_id"] not in d.deciding_rules
