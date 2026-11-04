"""Project a chat decision into the console object GW14b kept.

The simulator reads ``zeroshield.action`` and ``pipeline_trace``. Those names
stay. The six stages are the work this gateway actually did, built by
:mod:`gateway_v2.edge.console_stages` from facts on the request's ledger. A stage
that did not run is ``ran: false`` and ``action: skip``. The body action,
``source`` and ``X-ZeroShield-Action`` are written by :func:`apply_projection`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from gateway_v2.edge import errors
from gateway_v2.edge.console_stages import ChatFacts, stages_to_console
from gateway_v2.edge.wire.sse import SSEEncoder

if TYPE_CHECKING:
    from gateway_v2.edge.request_holds import RequestHolds

_ACTION_HEADER = "x-zeroshield-action"
_LOG = logging.getLogger("amf.edge.console")


def chat_console(*, request_id: str, facts: ChatFacts) -> dict[str, object] | None:
    """The validated console for ``facts``, or ``None`` when no honest trace exists."""
    return stages_to_console(request_id, facts)


def facts_from_holds(holds: RequestHolds, refuse_code: str | None = None) -> ChatFacts:
    """Copy the request's ledger into :class:`ChatFacts`. Reads ``holds`` only, never stats.

    One code, three readers: the code is ``refuse_code`` or the first failure recorded on
    ``holds``. After dispatch it is a ``failure_code``; before, it is a ``refuse_code``.
    """
    code = refuse_code or holds.failure_code
    decision = holds.input_decision
    passed = decision is not None
    output = holds.output_decision
    out_action = holds.output_action or ("" if output is None else output.disposition.value)
    out_rule = holds.output_rule or (
        output.deciding_rules[0] if output is not None and output.deciding_rules else ""
    )
    return ChatFacts(
        refuse_code="" if holds.dispatched else code,
        identity_resolved=holds.identity_resolved or passed,
        plan_version=holds.plan_version or ("" if decision is None else decision.plan_version),
        admitted=holds.is_admitted or passed,
        spent=holds.is_spent or passed,
        input_action="" if decision is None else decision.disposition.value,
        input_rule=decision.deciding_rules[0] if decision and decision.deciding_rules else "",
        dispatched=holds.dispatched,
        failure_code=code if holds.dispatched else "",
        output_action=out_action,
        output_rule=out_rule,
    )


def console_from_holds(
    holds: RequestHolds | None, refuse_code: str | None = None
) -> dict[str, object] | None:
    """The one console entry. A failure returns ``None`` and logs once; the response stands."""
    if holds is None:
        return None
    try:
        return chat_console(
            request_id=holds.request_id, facts=facts_from_holds(holds, refuse_code)
        )
    except Exception as exc:  # noqa: BLE001 - a console failure must never change the response.
        _LOG.warning(
            "console build failed request_id=%s error=%s", holds.request_id, type(exc).__name__,
        )
        return None


def refusal_console(holds: RequestHolds | None, code: str) -> dict[str, object] | None:
    """The console for a refusal rendered by ``code``; records the code on ``holds`` first."""
    if holds is None:
        return None
    holds.note_failure(code)
    return console_from_holds(holds, code)


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
    parsed["source"] = console["source"]
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
    console = console_from_holds(getattr(produced, "holds", None))
    if console is None:
        return payload
    for key in ("zeroshield", "pipeline_trace", "source"):
        payload[key] = console[key]
    return payload


def refusal_trace_frame(holds: RequestHolds | None, code: str) -> bytes | None:
    """An ``amf.trace`` frame for a refusal, or None when no honest trace exists."""
    console = refusal_console(holds, code)
    if console is None:
        return None
    return SSEEncoder().trace(
        {key: console[key] for key in ("zeroshield", "pipeline_trace", "source")},
    )


def _action_of(console: Mapping[str, object]) -> str:
    zeroshield = console["zeroshield"]
    if not isinstance(zeroshield, Mapping):
        raise ValueError("zeroshield must be an object")
    action = zeroshield["action"]
    if not isinstance(action, str):
        raise ValueError("zeroshield.action must be a string")
    return action
