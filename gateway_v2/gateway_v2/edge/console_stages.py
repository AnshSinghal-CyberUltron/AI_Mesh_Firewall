"""The six console stages as a pure function of facts the request recorded.

GW35. A stage is ``ran`` because a lifecycle fact says it ran. There is no latency input and no
neighbour inference, so an ``allow`` can never be drawn for work that was not done. Every surface
(HTTP body, SSE ``amf.trace`` frame, audit record) builds its console from the same
:class:`ChatFacts`, so they cannot disagree.

``build_stages`` is total over :class:`ChatFacts` and always returns the six stages in canonical
order. :func:`stages_to_console` decides whether a trace exists at all (parse and pre-check
refusals keep none) and fails closed to ``None`` rather than draw an ``allow`` for a refusal.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.audit.console_schema import (
    SOURCE,
    STAGE_NAMES,
    final_action,
    validate_console,
)
from gateway_v2.domain import posture
from gateway_v2.domain.failure_class import (
    INPUT_SIDE,
    NO_ACTION,
    is_operational_failure,
)

__all__ = ("TRACE_CODES", "ChatFacts", "StageOutcome", "build_stages", "stages_to_console")

_ALLOW = "allow"
_BLOCK = "block"
_ERROR = "error"
_SKIP = "skip"
_PASSED = frozenset({"allow", "flag", "redact"})
_DISPOSITIONS = frozenset({"allow", "flag", "redact", "block"})

#: Refusals that carry a trace even though no input decision exists. A parse or pre-check refusal
#: is deliberately absent (D-06): nothing ran that the console could honestly describe.
TRACE_CODES = frozenset(
    {
        posture.INVALID_API_KEY,
        posture.SHARED_STATE_UNAVAILABLE,
        posture.PLAN_UNAVAILABLE,
        posture.OVERLOAD_SHED,
        posture.RATE_LIMITED,
        posture.BUDGET_UNAVAILABLE,
        "scan_failure",
    },
)


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """One stage: what it did, whether it ran, and the rule that decided a non-allow."""

    name: str
    action: str
    ran: bool
    rule: str = ""


@dataclass(frozen=True, slots=True)
class ChatFacts:
    """Everything the console needs, copied from the request's ledger. No latency, no stats."""

    refuse_code: str = ""
    identity_resolved: bool = False
    plan_version: str = ""
    admitted: bool = False
    spent: bool = False
    input_action: str = ""
    input_rule: str = ""
    dispatched: bool = False
    failure_code: str = ""
    output_action: str = ""
    output_rule: str = ""


def build_stages(facts: ChatFacts) -> tuple[StageOutcome, ...]:
    """The six stages for ``facts``, in canonical order. Pure and total."""
    refuse = facts.refuse_code
    failure = "" if facts.failure_code in NO_ACTION else facts.failure_code
    auth = _auth(facts)
    rate = _rate_limit(facts)
    scan = _input_scan(facts)
    if refuse in INPUT_SIDE:
        # The input refusal wins over the recorded input action (an unmaskable redact did not
        # complete); nothing after it ran.
        scan = StageOutcome("input_scan", _BLOCK, True, refuse)
        routing = StageOutcome("model_routing", _SKIP, False)
        output = StageOutcome("model_output", _SKIP, False)
        guard = StageOutcome("output_guardrail", _SKIP, False)
    else:
        routing, output = _provider_stages(facts, refuse, failure)
        guard = _guardrail(facts, failure)
    return (auth, rate, scan, routing, output, guard)


def stages_to_console(request_id: str, facts: ChatFacts) -> dict[str, object] | None:
    """The validated console for ``facts``, or ``None`` when no honest trace exists."""
    code = facts.refuse_code or facts.failure_code
    if not (
        facts.identity_resolved or facts.refuse_code in TRACE_CODES or facts.input_action
    ):
        return None
    stages = build_stages(facts)
    final = final_action(stage.action for stage in stages)
    if code and code not in NO_ACTION and final == _ALLOW:
        return None  # a refused request must never read allow; an unknown code gets no trace
    trace: dict[str, object] = {
        "final_action": final,
        "stages": [_as_dict(stage) for stage in stages],
    }
    if facts.plan_version:
        trace["plan_version"] = facts.plan_version
    console: dict[str, object] = {
        "source": SOURCE,
        "zeroshield": {"action": final, "request_id": request_id},
        "pipeline_trace": trace,
    }
    validate_console(console)
    return console


def _auth(facts: ChatFacts) -> StageOutcome:
    if facts.identity_resolved:
        return StageOutcome("auth", _ALLOW, True)
    if facts.refuse_code == posture.INVALID_API_KEY:
        return StageOutcome("auth", _BLOCK, True, facts.refuse_code)
    if facts.refuse_code == posture.SHARED_STATE_UNAVAILABLE:
        return StageOutcome("auth", _ERROR, True, facts.refuse_code)
    return StageOutcome("auth", _SKIP, False)


def _rate_limit(facts: ChatFacts) -> StageOutcome:
    code = facts.refuse_code
    if code in (posture.OVERLOAD_SHED, posture.RATE_LIMITED):
        return StageOutcome("rate_limit", _BLOCK, True, code)
    if code == posture.BUDGET_UNAVAILABLE:
        return StageOutcome("rate_limit", _ERROR, True, code)
    if facts.spent:
        return StageOutcome("rate_limit", _ALLOW, True)
    return StageOutcome("rate_limit", _SKIP, False)


def _input_scan(facts: ChatFacts) -> StageOutcome:
    if facts.input_action:
        rule = facts.input_rule if facts.input_action != _ALLOW else ""
        return StageOutcome("input_scan", facts.input_action, True, rule)
    if facts.refuse_code == posture.PLAN_UNAVAILABLE:
        return StageOutcome("input_scan", _ERROR, False, facts.refuse_code)
    if facts.refuse_code == "scan_failure" and facts.spent:
        return StageOutcome("input_scan", _ERROR, True, facts.refuse_code)
    return StageOutcome("input_scan", _SKIP, False)


def _provider_stages(
    facts: ChatFacts, refuse: str, failure: str
) -> tuple[StageOutcome, StageOutcome]:
    skipped = (
        StageOutcome("model_routing", _SKIP, False),
        StageOutcome("model_output", _SKIP, False),
    )
    if not facts.dispatched:
        if not refuse or refuse in NO_ACTION or facts.input_action not in _PASSED:
            return skipped
        action = _ERROR if is_operational_failure(refuse) else _BLOCK
        return StageOutcome("model_routing", action, True, refuse), skipped[1]
    routing = StageOutcome("model_routing", _ALLOW, True)
    if failure and is_operational_failure(failure):
        return routing, StageOutcome("model_output", _ERROR, True, failure)
    return routing, StageOutcome("model_output", _ALLOW, True)


def _guardrail(facts: ChatFacts, failure: str) -> StageOutcome:
    if facts.output_action in _DISPOSITIONS:
        rule = facts.output_rule or failure
        shown = rule if facts.output_action != _ALLOW else ""
        return StageOutcome("output_guardrail", facts.output_action, True, shown)
    if facts.dispatched and failure and not is_operational_failure(failure):
        return StageOutcome("output_guardrail", _BLOCK, True, failure)
    return StageOutcome("output_guardrail", _SKIP, False)


def _as_dict(stage: StageOutcome) -> dict[str, object]:
    if stage.name not in STAGE_NAMES:
        raise ValueError(stage.name)
    out: dict[str, object] = {"name": stage.name, "action": stage.action, "ran": stage.ran}
    if stage.rule and stage.action not in (_ALLOW, _SKIP):
        out["deciding_rule"] = stage.rule
    return out
