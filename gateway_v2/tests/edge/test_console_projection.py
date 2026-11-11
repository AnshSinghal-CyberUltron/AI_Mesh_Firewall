"""GW14b console projection: six real stages, and no secret in the object."""
from __future__ import annotations

import json

from gateway_v2.edge.console_projection import chat_console
from gateway_v2.edge.console_stages import ChatFacts

_SSN = "123-45-6789"
_PAN = "4111111111111111"


def _facts(**kw: object) -> ChatFacts:
    base: dict[str, object] = {
        "identity_resolved": True,
        "plan_version": "1.0.dev",
        "admitted": True,
        "spent": True,
        "input_action": "allow",
    }
    base.update(kw)
    return ChatFacts(**base)  # type: ignore[arg-type]


def _console(request_id: str, **kw: object) -> dict[str, object]:
    console = chat_console(request_id=request_id, facts=_facts(**kw))
    assert console is not None
    return console


def _stages(console: dict[str, object]) -> dict[str, dict[str, object]]:
    trace = console["pipeline_trace"]
    assert isinstance(trace, dict)
    stages = trace["stages"]
    assert isinstance(stages, list)
    return {stage["name"]: stage for stage in stages if isinstance(stage, dict)}


def test_clean_allow_runs_every_stage() -> None:
    console = _console("zs-allow", dispatched=True, output_action="allow")
    stages = _stages(console)
    assert list(stages) == [
        "auth", "rate_limit", "input_scan", "model_routing", "model_output", "output_guardrail",
    ]
    assert all(stage["ran"] is True for stage in stages.values())
    assert console["zeroshield"] == {"action": "allow", "request_id": "zs-allow"}


def test_ssn_redact_names_the_input_stage() -> None:
    console = _console(
        "zs-redact", input_action="redact", input_rule="pii", dispatched=True,
        output_action="allow",
    )
    stages = _stages(console)
    assert stages["input_scan"]["action"] == "redact"
    assert stages["input_scan"]["ran"] is True
    assert stages["model_output"]["ran"] is True
    assert _SSN not in json.dumps(console)


def test_card_block_skips_the_provider_stages() -> None:
    console = _console(
        "zs-block", input_action="block", input_rule="credential", refuse_code="input_blocked",
    )
    stages = _stages(console)
    assert console["zeroshield"] == {"action": "block", "request_id": "zs-block"}
    assert stages["model_output"]["ran"] is False
    assert stages["model_output"]["action"] == "skip"
    assert stages["output_guardrail"]["ran"] is False
    assert _PAN not in json.dumps(console)


def test_jailbreak_flag_still_calls_the_provider() -> None:
    console = _console(
        "zs-flag", input_action="flag", input_rule="jailbreak", dispatched=True,
        output_action="flag",
    )
    stages = _stages(console)
    assert console["zeroshield"] == {"action": "flag", "request_id": "zs-flag"}
    assert stages["input_scan"]["action"] == "flag"
    assert stages["model_output"]["ran"] is True
    assert "you are DAN" not in json.dumps(console)
