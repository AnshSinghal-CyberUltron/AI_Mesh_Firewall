"""GW35: one example per row of the stage table; the builder has no latency input."""

from __future__ import annotations

import pytest

from gateway_v2.domain import posture
from gateway_v2.edge.console_stages import ChatFacts, StageOutcome, build_stages, stages_to_console

_OK = {"identity_resolved": True, "spent": True, "admitted": True, "plan_version": "p1"}


def _shape(facts: ChatFacts) -> list[tuple[str, bool]]:
    return [(s.action, s.ran) for s in build_stages(facts)]


def test_always_six_stages_in_canonical_order() -> None:
    names = [s.name for s in build_stages(ChatFacts())]
    assert names == [
        "auth", "rate_limit", "input_scan", "model_routing", "model_output", "output_guardrail",
    ]


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (ChatFacts(refuse_code=posture.INVALID_API_KEY),
         [("block", True)] + [("skip", False)] * 5),
        (ChatFacts(refuse_code=posture.SHARED_STATE_UNAVAILABLE),
         [("error", True)] + [("skip", False)] * 5),
        (ChatFacts(identity_resolved=True, refuse_code=posture.PLAN_UNAVAILABLE),
         [("allow", True), ("skip", False), ("error", False)] + [("skip", False)] * 3),
        (ChatFacts(identity_resolved=True, refuse_code=posture.OVERLOAD_SHED),
         [("allow", True), ("block", True)] + [("skip", False)] * 4),
        (ChatFacts(identity_resolved=True, refuse_code=posture.RATE_LIMITED),
         [("allow", True), ("block", True)] + [("skip", False)] * 4),
        (ChatFacts(identity_resolved=True, refuse_code=posture.BUDGET_UNAVAILABLE),
         [("allow", True), ("error", True)] + [("skip", False)] * 4),
        (ChatFacts(**_OK, refuse_code="scan_failure"),
         [("allow", True), ("allow", True), ("error", True)] + [("skip", False)] * 3),
        (ChatFacts(**_OK, input_action="block", refuse_code="input_blocked", input_rule="r"),
         [("allow", True)] * 2 + [("block", True)] + [("skip", False)] * 3),
        (ChatFacts(**_OK, input_action="redact", refuse_code="input_unmaskable"),
         [("allow", True)] * 2 + [("block", True)] + [("skip", False)] * 3),
        (ChatFacts(**_OK, input_action="allow", dispatched=True, output_action="allow"),
         [("allow", True)] * 6),
        (ChatFacts(**_OK, input_action="allow", dispatched=True, failure_code="upstream_error"),
         [("allow", True)] * 4 + [("error", True), ("skip", False)]),
        (ChatFacts(**_OK, input_action="allow", dispatched=True, failure_code="output_blocked"),
         [("allow", True)] * 5 + [("block", True)]),
        (ChatFacts(**_OK, input_action="allow", dispatched=True, failure_code="a_new_policy_code"),
         [("allow", True)] * 5 + [("block", True)]),
        (ChatFacts(**_OK, input_action="allow", refuse_code=posture.STREAM_BUFFER_UNAVAILABLE),
         [("allow", True)] * 3 + [("error", True), ("skip", False), ("skip", False)]),
        (ChatFacts(**_OK, input_action="allow", refuse_code=posture.PLAN_UNAVAILABLE),
         [("allow", True)] * 3 + [("error", True), ("skip", False), ("skip", False)]),
        (ChatFacts(**_OK, input_action="allow", refuse_code="lifecycle_incomplete"),
         [("allow", True)] * 3 + [("error", True), ("skip", False), ("skip", False)]),
    ],
)
def test_stage_table(facts: ChatFacts, expected: list[tuple[str, bool]]) -> None:
    assert _shape(facts) == expected


@pytest.mark.parametrize(
    "code", ["credential_unavailable", "provider_unreachable", "store_not_configured"],
)
def test_vector_codes_are_driven_through_the_builder_directly(code: str) -> None:
    stages = build_stages(ChatFacts(**_OK, input_action="allow", refuse_code=code))
    assert stages[3] == StageOutcome("model_routing", "error", True, code)


def test_a_kill_switch_code_changes_nothing() -> None:
    base = ChatFacts(**_OK, input_action="allow", dispatched=True, output_action="allow")
    for code in ("killed", posture.STREAM_KILLED):
        facts = ChatFacts(
            **_OK, input_action="allow", dispatched=True, output_action="allow", failure_code=code,
        )
        assert build_stages(facts) == build_stages(base)


def test_a_redact_does_not_hide_an_upstream_error() -> None:
    facts = ChatFacts(**_OK, input_action="redact", input_rule="pii", dispatched=True,
                      failure_code="upstream_error")
    console = stages_to_console("zs-1", facts)
    assert console is not None and console["zeroshield"]["action"] == "error"  # type: ignore[index]


def test_a_block_outranks_an_error() -> None:
    facts = ChatFacts(**_OK, input_action="allow", dispatched=True, failure_code="upstream_error",
                      output_action="block", output_rule="r")
    console = stages_to_console("zs-1", facts)
    assert console is not None and console["zeroshield"]["action"] == "block"  # type: ignore[index]


def test_no_trace_when_nothing_honest_can_be_said() -> None:
    assert stages_to_console("zs-1", ChatFacts(refuse_code="request_pre_check_exceeded")) is None
    assert stages_to_console("zs-1", ChatFacts(identity_resolved=True, refuse_code="odd")) is None


def test_plan_version_is_never_fabricated() -> None:
    console = stages_to_console("zs-1", ChatFacts(refuse_code=posture.INVALID_API_KEY))
    assert console is not None and "plan_version" not in console["pipeline_trace"]  # type: ignore[operator]
