"""#117 reviews A-S3, B-N1, B-N3: what the projector logs about one tenant's plan.

* A-S3 (and #136's note on `projector.py:214-216`): a not-projectable note carried exception
  text, and an exception raised on one tenant's row can quote that row. The note now carries
  the org, the revision and a fixed reason with the exception's type only.
* B-N1: a refused customer condition is signed by design and showed only as the fleet-wide
  `refused_conditions` gauge. One deduplicated note now names the org and revision.
* B-N3: a round that raised logged "projector a is standing by", which read as a hand-over.
"""

from __future__ import annotations

import json
import logging

import pytest

from gateway_v2.domain.plan import StreamingMode
from gateway_v2.plan.compiler import CompileError
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body
from state_control import projector as projector_module
from state_control import projector_service
from state_control.projector import ProjectionSummary
from tests.plan.test_lgw05c_delta import _draft
from tests.state_control.test_gw36_s2_projector import Lab, _doc
from tests.state_control.test_gw36_s2_service import BASE

CANARY = "CANARY-tenant-data-7f3a"


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def _with_rule(slug: str, **condition: object) -> str:
    body = encode_plan_body(PlanDocument(slug, StreamingMode.INCREMENTAL, (_draft(),)))
    rules = body["rules"]
    assert isinstance(rules, list)
    rules.append(
        {
            **rules[0],
            "rule_id": f"pol-{CANARY}-1",
            "condition": {
                "kind": "regex", "field": "", "direction": rules[0]["scope"], "keywords": [],
                "detector_class": "", "pipeline_stage": "", **condition,
            },
        },
    )
    return json.dumps(body)


def test_no_note_carries_exception_text_or_a_value_from_the_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    lab = Lab()
    for slug in ("raises", "mismatch", "no-compile", "surrogate", "not-json", "healthy"):
        lab.org(slug, plan=False)
    lab.source.plan("raises", _doc("raises"))
    lab.source.plan("mismatch", _doc(CANARY))
    lab.source.plan("no-compile", _doc("no-compile").replace('"incremental"', f'"{CANARY}"'))
    lab.source.plan("surrogate", _doc("surrogate")[:-1] + ', "x": "' + CANARY + '\\ud800"}')
    lab.source.plan("not-json", "{" + CANARY)
    lab.source.plan("healthy", _doc("healthy"))
    real_compile = projector_module.compile_plan

    def compile_or_explode(org_id: str, *args: object, **kwargs: object) -> object:
        if org_id == "raises":
            raise TypeError(f"unhashable: {CANARY}")
        return real_compile(org_id, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(projector_module, "compile_plan", compile_or_explode)
    with caplog.at_level(logging.WARNING, logger="amf.state.projector"):
        summary = lab.projector.round_once()

    assert summary.heartbeat and summary.invalid_plans == 5
    messages = _messages(caplog)
    assert not [m for m in messages if CANARY in m], "no tenant value and no exception text"
    expected = {
        "raises": "TypeError: unanticipated fault while validating",
        "mismatch": "body claims another org",
        "no-compile": "CompileError: the plan does not compile",
        "surrogate": "UnicodeEncodeError: no canonical form (NaN, Infinity, lone surrogate)",
        "not-json": "JSONDecodeError: body is not valid JSON",
    }
    for slug, reason in expected.items():
        note = (
            f"plan document for {slug!r} revision 1 is not projectable ({reason}); "
            "the previous record, if any, stays"
        )
        assert messages.count(note) == 1, (slug, messages)


def test_a_compile_error_quoting_the_body_is_reduced_to_its_type() -> None:
    """`document.py` `_member` quotes the tenant's value; the reason must not."""
    raw = _doc("acme").replace('"incremental"', f'"{CANARY}"')
    with pytest.raises(CompileError, match=CANARY):
        decode_plan_body(raw.encode())
    body, _refused, why = projector_module.validate_plan("acme", raw)
    assert body is None and why == "CompileError: the plan does not compile"


def test_a_refused_condition_names_the_org_once_and_never_the_pattern(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pattern = "(" * 17 + CANARY + ")" * 17
    lab = Lab()
    lab.org("acme", plan=False)
    lab.org("other")
    lab.source.plan("acme", _with_rule("acme", pattern=pattern))
    with caplog.at_level(logging.WARNING, logger="amf.state.projector"):
        first = lab.projector.round_once()
        lab.projector.round_once()
    assert first.refused_conditions == 1 and first.invalid_plans == 0
    messages = _messages(caplog)
    note = (
        "plan document for 'acme' revision 1 is signed with 1 customer condition(s) the matcher "
        "refuses (ReDoS shape, groups nested deeper than 16, or does not compile); gateways "
        "refuse those rules per request (policy_regex_timeout)"
    )
    assert messages.count(note) == 1, messages
    assert not [m for m in messages if CANARY in m], "neither the pattern nor the rule id"
    assert not [m for m in messages if "'other'" in m], "a healthy org gets no note"

    lab.source.plan("acme", _with_rule("acme", pattern=pattern))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="amf.state.projector"):
        lab.projector.round_once()
    assert _messages(caplog) == [note.replace("revision 1", "revision 2")], "once per revision"


class _Script:
    """A projector whose rounds follow a script: a summary, or an exception to raise."""

    def __init__(self, *steps: ProjectionSummary | Exception) -> None:
        self._steps = list(steps)

    def round_once(self) -> ProjectionSummary:
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def release(self) -> None:
        return None


def test_a_round_that_raises_is_not_logged_as_a_hand_over(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = projector_service.from_env(BASE)
    lead = ProjectionSummary(leader=True, heartbeat=True, complete=True)
    script = _Script(
        lead, RecursionError("x"), RecursionError("x"), lead, ProjectionSummary(leader=False),
    )
    svc = projector_service.ProjectorService(config, script)  # type: ignore[arg-type]
    with caplog.at_level(logging.INFO, logger="amf.state.projector.service"):
        for _ in range(5):
            svc.round_once()
    transitions = [m for m in _messages(caplog) if m.startswith(f"projector {config.holder} is ")]
    assert transitions == [
        f"projector {config.holder} is the writer",
        f"projector {config.holder} is standing by",
    ], "the two failed rounds change nothing; the real lease loss is still logged"
    assert sum("projection round failed" in m for m in _messages(caplog)) == 2
