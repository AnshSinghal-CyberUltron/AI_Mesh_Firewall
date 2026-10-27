"""Project a chat decision into the console object GW14b kept.

The simulator reads ``zeroshield.action`` and ``pipeline_trace``. Those names
stay. The six stages are the work this gateway actually did. A stage that did
not run is ``ran: false`` and ``action: skip``. The body action and
``X-ZeroShield-Action`` are written by :func:`apply_projection`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from gateway_v2.audit.console_schema import SOURCE, validate_console
from gateway_v2.domain.decision import Disposition, most_restrictive
from gateway_v2.edge import errors
from gateway_v2.edge.wire.sse import SSEEncoder

if TYPE_CHECKING:
    from gateway_v2.edge.request_holds import RequestHolds

_ACTION_HEADER = "x-zeroshield-action"
_STAGES = (
    "auth",
    "rate_limit",
    "input_scan",
    "model_routing",
    "model_output",
    "output_guardrail",
)


def chat_console(
    *,
    request_id: str,
    plan_version: str,
    input_action: str,
    output_action: str | None,
    provider_called: bool,
    input_rule: str = "",
    output_rule: str = "",
) -> dict[str, object]:
    """The console object for one chat. ``output_action`` is None when output did not run."""
    final = input_action if output_action is None else _stricter(input_action, output_action)
    model_ran = provider_called
    output_shown = "skip" if output_action is None else output_action
    stages: list[dict[str, object]] = [
        _stage("auth", "allow", True, ""),
        _stage("rate_limit", "allow", True, ""),
        _stage("input_scan", input_action, True, input_rule if input_action != "allow" else ""),
        _stage("model_routing", "allow" if model_ran else "skip", model_ran, ""),
        _stage("model_output", "allow" if model_ran else "skip", model_ran, ""),
        _stage(
            "output_guardrail",
            output_shown,
            output_action is not None,
            output_rule if output_action not in (None, "allow") else "",
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


def console_from_holds(
    holds: RequestHolds | None,
    *,
    output_action: str | None,
    provider_called: bool,
) -> dict[str, object] | None:
    """Build the console object from the input decision recorded on ``holds``."""
    if holds is None or holds.input_decision is None:
        return None
    decision = holds.input_decision
    rule = decision.deciding_rules[0] if decision.deciding_rules else ""
    output = holds.output_decision
    output_rule = output.deciding_rules[0] if output is not None and output.deciding_rules else ""
    return chat_console(
        request_id=holds.request_id,
        plan_version=decision.plan_version,
        input_action=decision.disposition.value,
        output_action=output_action,
        provider_called=provider_called,
        input_rule=rule,
        output_rule=output_rule,
    )


def apply_projection(
    response: errors.HTTPResponse,
    console: Mapping[str, object] | None,
) -> errors.HTTPResponse:
    """Merge the projection into a JSON body and set ``X-ZeroShield-Action``."""
    if console is None:
        return response
    parsed = json.loads(response.body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("console projection requires a JSON object body")
    parsed["zeroshield"] = console["zeroshield"]
    parsed["pipeline_trace"] = console["pipeline_trace"]
    action = _action_of(console)
    body = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return errors.HTTPResponse(
        status=response.status,
        body=body,
        headers=response.headers + ((_ACTION_HEADER, action),),
    )


def stream_trace_payload(
    produced: object,
    stats: object,
    degenerated: bool,
) -> dict[str, object]:
    """The ``amf.trace`` object, with the console projection when the input decision is known."""
    mode = getattr(stats, "produced_mode", None)
    payload: dict[str, object] = {
        "produced_mode": getattr(mode, "value", mode),
        "degenerated_to_strict": degenerated,
    }
    output_action = getattr(stats, "output_action", None)
    if not isinstance(output_action, str):
        output_action = None
    console = console_from_holds(
        getattr(produced, "holds", None),
        output_action=output_action,
        provider_called=output_action is not None,
    )
    if console is None:
        return payload
    payload["zeroshield"] = console["zeroshield"]
    payload["pipeline_trace"] = console["pipeline_trace"]
    return payload


def refusal_trace_frame(holds: RequestHolds | None) -> bytes | None:
    """An ``amf.trace`` frame for an input refusal, or None when no decision was recorded."""
    console = console_from_holds(holds, output_action=None, provider_called=False)
    if console is None:
        return None
    return SSEEncoder().trace(
        {"zeroshield": console["zeroshield"], "pipeline_trace": console["pipeline_trace"]},
    )


def _stage(name: str, action: str, ran: bool, deciding_rule: str) -> dict[str, object]:
    if name not in _STAGES:
        raise ValueError(name)
    stage: dict[str, object] = {"name": name, "action": action, "ran": ran}
    if deciding_rule:
        stage["deciding_rule"] = deciding_rule
    return stage


def _stricter(left: str, right: str) -> str:
    winner = most_restrictive((Disposition(left), Disposition(right)))
    return winner.value


def _action_of(console: Mapping[str, object]) -> str:
    zeroshield = console["zeroshield"]
    if not isinstance(zeroshield, Mapping):
        raise ValueError("zeroshield must be an object")
    action = zeroshield["action"]
    if not isinstance(action, str):
        raise ValueError("zeroshield.action must be a string")
    return action
