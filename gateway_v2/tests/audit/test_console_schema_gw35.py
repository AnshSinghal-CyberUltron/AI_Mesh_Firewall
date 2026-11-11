"""GW35: the console vocabulary gained ``error``; stages, ordering and honesty are validated."""

from __future__ import annotations

import copy

import pytest

from gateway_v2.audit.console_schema import (
    ACTION_SEVERITY,
    MCP_STAGE_NAMES,
    STAGE_ACTION_VOCAB,
    STAGE_NAMES,
    SchemaError,
    final_action,
    validate_console,
)
from gateway_v2.edge.mcp_console import mcp_console


def _console(
    stages: list[dict[str, object]], final: str, plan: str | None = "p1",
) -> dict[str, object]:
    trace: dict[str, object] = {"final_action": final, "stages": stages}
    if plan is not None:
        trace["plan_version"] = plan
    return {
        "source": "gateway_v2",
        "zeroshield": {"action": final, "request_id": "zs-1"},
        "pipeline_trace": trace,
    }


def _stage(name: str, action: str, ran: bool, rule: str | None = None) -> dict[str, object]:
    out: dict[str, object] = {"name": name, "action": action, "ran": ran}
    if rule:
        out["deciding_rule"] = rule
    return out


def test_vocabulary_literal_matches_the_validator() -> None:
    assert set(STAGE_ACTION_VOCAB) == set(ACTION_SEVERITY) | {"skip"}
    assert STAGE_NAMES[0] == "auth" and len(STAGE_NAMES) == 6


def test_error_is_accepted_and_ranks_between_redact_and_block() -> None:
    assert final_action(["allow", "redact", "error", "skip"]) == "error"
    assert final_action(["error", "block"]) == "block"
    assert final_action(["skip"]) == "allow" and final_action([]) == "allow"
    stage = _stage("auth", "error", True, "shared_state_unavailable")
    validate_console(_console([stage], "error", None))


@pytest.mark.parametrize(
    "stages",
    [
        [_stage("kill_switch", "allow", True)],
        [_stage("input_scan", "allow", False)],
        [_stage("auth", "skip", True)],
        [_stage("auth", "block", False, "x")],
        [_stage("auth", "allow", True, "x")],
        [_stage("input_scan", "allow", True), _stage("auth", "allow", True)],
        [_stage("auth", "allow", True), _stage("auth", "allow", True)],
    ],
)
def test_dishonest_or_misordered_stages_are_rejected(stages: list[dict[str, object]]) -> None:
    with pytest.raises(SchemaError):
        validate_console(_console(stages, "allow"))


def test_only_input_scan_may_be_an_error_that_did_not_run() -> None:
    stage = _stage("input_scan", "error", False, "plan_unavailable")
    validate_console(_console([stage], "error", None))
    with pytest.raises(SchemaError):
        validate_console(_console([_stage("model_output", "error", False, "x")], "error"))


def test_final_action_must_match_the_stages() -> None:
    with pytest.raises(SchemaError):
        validate_console(_console([_stage("auth", "block", True, "r")], "allow"))


def test_plan_version_is_required_only_when_the_scan_ran_and_never_empty() -> None:
    validate_console(_console([_stage("auth", "allow", True)], "allow", None))
    with pytest.raises(SchemaError):
        validate_console(_console([_stage("input_scan", "allow", True)], "allow", None))
    with pytest.raises(SchemaError):
        validate_console(_console([_stage("auth", "allow", True)], "allow", ""))


def test_request_id_must_be_non_empty() -> None:
    console = _console([_stage("auth", "allow", True)], "allow")
    console["zeroshield"]["request_id"] = ""  # type: ignore[index]
    with pytest.raises(SchemaError):
        validate_console(console)


def test_mutating_one_field_of_a_valid_console_fails() -> None:
    good = _console(
        [_stage("auth", "allow", True), _stage("input_scan", "block", True, "r")], "block"
    )
    validate_console(good)
    for path, value in (("final", "allow"), ("name", "nope"), ("ran", False)):
        bad = copy.deepcopy(good)
        stages = bad["pipeline_trace"]["stages"]  # type: ignore[index]
        if path == "final":
            bad["pipeline_trace"]["final_action"] = value  # type: ignore[index]
        elif path == "name":
            stages[0]["name"] = value
        else:
            stages[1]["ran"] = value
        with pytest.raises(SchemaError):
            validate_console(bad)


def test_mcp_console_still_validates_and_a_chat_console_rejects_tool_call() -> None:
    console = mcp_console(
        request_id="r", plan_version="p", input_action="allow", tool_called=True,
        output_action="allow",
    )
    validate_console(console, stage_names=MCP_STAGE_NAMES)
    with pytest.raises(SchemaError):
        validate_console(console)
