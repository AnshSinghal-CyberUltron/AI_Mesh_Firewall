"""The console object for one MCP request (GW17, task 5.3).

Five stages, the work the MCP surface actually does: ``auth``, ``rate_limit``,
``input_scan``, ``tool_call`` (the broker call), ``output_guardrail``. A stage that did not
run is ``ran: false`` and ``action: skip``. No chat stage name is used and no stage is
filled in. Validated against the GW14b console schema before it is returned.
"""

from __future__ import annotations

from gateway_v2.audit.console_schema import SOURCE, validate_console
from gateway_v2.domain.decision import Disposition, most_restrictive

__all__ = ("MCP_STAGES", "mcp_console")

MCP_STAGES = ("auth", "rate_limit", "input_scan", "tool_call", "output_guardrail")
_SKIP = "skip"
_ALLOW = "allow"


def mcp_console(
    *,
    request_id: str,
    plan_version: str,
    input_action: str | None,
    input_rule: str = "",
    tool_called: bool,
    output_action: str | None,
    output_rule: str = "",
) -> dict[str, object]:
    """``input_action`` is None when the scan did not run; ``output_action`` likewise."""
    actions = [action for action in (input_action, output_action) if action is not None]
    final = _ALLOW if not actions else most_restrictive(tuple(map(Disposition, actions))).value
    stages = [
        _stage("auth", _ALLOW, True),
        _stage("rate_limit", _ALLOW, True),
        _stage("input_scan", input_action or _SKIP, input_action is not None, input_rule),
        _stage("tool_call", _ALLOW if tool_called else _SKIP, tool_called),
        _stage(
            "output_guardrail",
            output_action or _SKIP,
            output_action is not None,
            output_rule,
        ),
    ]
    console: dict[str, object] = {
        "source": SOURCE,
        "zeroshield": {"action": final, "request_id": request_id},
        "pipeline_trace": {
            "final_action": final,
            "plan_version": plan_version,
            "stages": stages,
        },
    }
    validate_console(console)
    return console


def _stage(name: str, action: str, ran: bool, rule: str = "") -> dict[str, object]:
    stage: dict[str, object] = {"name": name, "action": action, "ran": ran}
    if rule and action not in (_ALLOW, _SKIP):
        stage["deciding_rule"] = rule
    return stage
