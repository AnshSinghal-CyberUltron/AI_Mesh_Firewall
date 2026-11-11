"""One audit record per phase, enqueued without touching the response already sent.

The callback is :meth:`gateway_v2.audit.sink.AuditSink.emit`: it does not await and
it does not raise when the queue is full. This module also swallows a broken
callback. A lost record is a counted drop. It is not a failed chat.

Streaming output keeps the output ``Decision`` on the stream stats. The edge
copies that decision onto the request before this function runs, so a stream
emits an output record as well as the input record. A pass-through allow has
no resolver decision; the edge records an allow decision for that case.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from gateway_v2.audit.record import (
    REJECTED,
    SHED,
    UNAUTHENTICATED_ORG,
    AuditPhase,
    AuditRecord,
    admission,
    from_decision,
    output_failure,
)
from gateway_v2.domain.decision import Decision, DecisionRecord, Disposition
from gateway_v2.domain.failure_class import is_no_action, is_operational_failure
from gateway_v2.domain.plan import UNSET_CONTROLS, AuditControl, OrgControls
from gateway_v2.domain.posture import INVALID_API_KEY, OVERLOAD_SHED, SHARED_STATE_UNAVAILABLE
from gateway_v2.edge import errors
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.incident_attempt import feed_attempt
from gateway_v2.edge.incident_audit import (
    effective_refuse_code,
    incident_is_cut,
    incident_is_refusal,
    incident_records,
    reroute_record,
    with_routing,
)
from gateway_v2.edge.outcome_metrics import (
    OutcomeObserver,
    observe_audit_suppressed,
    observe_outcome,
)
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.unauth_audit import UnauthAuditGate

_CHAT_ROUTE = "chat"
_IDENTITY_CODES = frozenset({INVALID_API_KEY, SHARED_STATE_UNAVAILABLE})


#: Decides whether a review-queue item is emitted for a REDACT/REWRITE disposition (requirement
#: 4.3; GW27 wires `output_incident_logging_enabled` into it). FLAG and BLOCK always queue.
ReviewQueuePolicy = Callable[[], bool]


def _always() -> bool:
    return True


def review_item_wanted(decision: Decision, policy: ReviewQueuePolicy = _always) -> bool:
    """Whether the review queue gets an item. The in-record flag is set regardless."""
    if not decision.review_required:
        return False
    if decision.disposition in (Disposition.REDACT, Disposition.REWRITE):
        return policy()
    return True


def output_extras(
    holds: RequestHolds,
    stats: object,
    decision: Decision | None,
    policy: ReviewQueuePolicy = _always,
) -> dict[str, object]:
    """The GW33 audit fields. Booleans, rule/code names and the bytes action: never content."""
    review = bool(getattr(stats, "review_required", False))
    forced = decision is None or not decision.review_required  # the stats-only flag (D-22)
    queued = forced or (decision is not None and review_item_wanted(decision, policy))
    return {
        "review_required": review,
        "review_queued": review and queued,
        "canary_checked": holds.canary_ticket is not None,
        "canary_leaked": bool(getattr(stats, "canary_leaked", False)),
        "bytes_action": str(getattr(stats, "bytes_action", "allow")),
        "codes": list(decision.codes) if decision is not None else [],
    }


def note_stream_output(
    holds: RequestHolds,
    stats: object,
    plan_version: str,
    policy: ReviewQueuePolicy = _always,
) -> None:
    """Record the stream's output decision and cause. An allow with no decision is still output.

    A cut with an error code records ``failure_code`` (the first cause wins). A policy cut with no
    decision also records a ``block`` output action; a fault never touches the output action, so
    ``error`` cannot paint the guardrail stage. A kill-switch cut records nothing.
    """
    error = getattr(stats, "error", None)
    decision = getattr(stats, "output_decision", None)
    holds.note_output_extras(
        output_extras(holds, stats, decision if isinstance(decision, Decision) else None, policy),
    )
    if isinstance(error, str) and error and not is_no_action(error):
        holds.note_failure(error)
        policy_cut = getattr(stats, "output_action", None) == "block"
        if not is_operational_failure(error) and policy_cut and not isinstance(decision, Decision):
            holds.note_output_action("block", error)
    if isinstance(decision, Decision):
        holds.note_output(decision)
        return
    if getattr(stats, "output_action", None) != "allow":
        return
    holds.note_output(
        Decision(
            disposition=Disposition.ALLOW,
            per_finding=(),
            transformations=(),
            findings=(),
            plan_version=plan_version,
            deciding_rules=(),
            unavailable_detectors=(),
        ),
    )


def emit_chat_audit(
    emit: Callable[[AuditRecord], bool] | None,
    holds: RequestHolds,
    refuse_code: str | None,
    *,
    route: str = _CHAT_ROUTE,
    console: Mapping[str, object] | None = None,
    detail: Mapping[str, str] | None = None,
    observer: OutcomeObserver | None = None,
    gate: UnauthAuditGate | None = None,
) -> None:
    """Enqueue the records this request can honestly name. Never raises.

    ``route`` names the surface. A non-chat surface passes its own ``console`` (chat's
    is built here) and may add plain-string ``detail`` such as the MCP server and tool.
    ``observer`` counts the same outcome for ``/metrics``, whether or not audit is wired.
    ``gate`` bounds the records written for a failed credential, which name no tenant.
    """
    refuse_code = effective_refuse_code(holds, refuse_code)
    observe_outcome(observer, holds, refuse_code)
    feed_attempt(emit, holds, refuse_code)  # GW29: the risk tracker sees this attempt (tap only)
    org = _audit_org(holds, refuse_code, route)
    if emit is None or not holds.request_id or not org:
        return
    try:
        extra = {"route": route, **(detail or {})}
        if org == UNAUTHENTICATED_ORG:
            if gate is None or not gate.admit():
                return
            if holds.key_fingerprint:
                extra["key_fingerprint"] = holds.key_fingerprint
        if console is None and route == _CHAT_ROUTE:
            console = console_from_holds(holds, refuse_code)
        controls = holds.controls
        stamp = _stamp(controls)
        for record in _records(holds, org, refuse_code, time.time_ns(), console, extra, stamp):
            if _suppressed(record, controls.audit):
                observe_audit_suppressed(observer)
                continue
            emit(record)
    except Exception:
        return


_MANDATORY_OUTCOMES = frozenset({Disposition.BLOCK.value, SHED, REJECTED})
_INCIDENT_OUTCOMES = frozenset({Disposition.REDACT.value, Disposition.FLAG.value})


def _is_mandatory(record: AuditRecord) -> bool:
    """A hard block, a shed or an admission rejection. Reads only the record, never a control."""
    return record.outcome in _MANDATORY_OUTCOMES


def _suppressed(record: AuditRecord, audit: AuditControl | None) -> bool:
    """Org audit gating (GW27). Classify first, then gate; no control means audit stays on."""
    if audit is None or _is_mandatory(record):
        return False
    if not audit.enabled:
        return True
    return (
        not audit.incident_logging
        and record.phase is AuditPhase.OUTPUT
        and record.outcome in _INCIDENT_OUTCOMES
    )


def _stamp(controls: OrgControls) -> Mapping[str, object]:
    """Retention and compliance values in force, stamped as int and list[str] (never strings).

    Nothing is stamped when the plan carried no controls, so a pre-plan refusal has no stamp.
    """
    if controls == UNSET_CONTROLS:
        return {}
    stamp: dict[str, object] = {}
    if controls.audit is not None:
        stamp["retention_days"] = controls.audit.retention_days
    if controls.compliance_frameworks:
        stamp["compliance_frameworks"] = list(controls.compliance_frameworks)
    return stamp


def _audit_org(holds: RequestHolds, refuse_code: str | None, route: str) -> str:
    """The tenant for the row: the resolved one, or the reserved sentinel for a failed credential.

    Never a guess. The sentinel applies to chat-shaped routes only; an unauthenticated MCP call
    stays unaudited (a recorded gap).
    """
    if holds.org_id:
        return holds.org_id
    if route == _CHAT_ROUTE and refuse_code in _IDENTITY_CODES:
        return UNAUTHENTICATED_ORG
    return ""


def _records(
    holds: RequestHolds,
    org: str,
    refuse_code: str | None,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, object],
    stamp: Mapping[str, object],
) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    if incident_is_refusal(holds):
        # GW29: an incident refusal is ONE record, whatever was decided before it (6.4a).
        return incident_records(holds, refuse_code, recorded_at_ns)
    output = None if incident_is_cut(holds) else holds.output_decision
    if holds.input_decision is not None:
        records.append(
            _phase(
                holds, org, holds.input_decision, AuditPhase.INPUT, recorded_at_ns, console, extra,
                stamp,
            ),
        )
    if output is not None:
        records.append(
            _phase(holds, org, output, AuditPhase.OUTPUT, recorded_at_ns, console, extra, stamp),
        )
    elif holds.dispatched and holds.failure_code:
        failed = _output_failure(holds, org, recorded_at_ns, console, extra, stamp)
        if failed is not None:
            records.append(failed)
    if refuse_code and holds.input_decision is None and output is None:
        admitted = _admission(holds, org, refuse_code, recorded_at_ns, console, extra, stamp)
        if admitted is not None:
            records.append(admitted)
    records = [with_routing(holds, record) for record in records]
    records.extend(incident_records(holds, refuse_code, recorded_at_ns))
    rerouted = reroute_record(holds, recorded_at_ns)
    if rerouted is not None:
        records.append(rerouted)
    return records


def _output_failure(
    holds: RequestHolds,
    org: str,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, object],
    stamp: Mapping[str, object],
) -> AuditRecord | None:
    code = holds.failure_code
    try:
        status = errors.render_http(code, request_id=holds.request_id).status
    except Exception:
        return None
    kind = "operational" if is_operational_failure(code) else "policy"
    built = output_failure(
        request_id=holds.request_id,
        org_id=org,
        reason=code,
        failure_class=kind,
        recorded_at_ns=recorded_at_ns,
        status=status,
    )
    return _annotate(built, console, extra, stamp)


def _phase(
    holds: RequestHolds,
    org: str,
    decision: Decision,
    phase: AuditPhase,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, object],
    stamp: Mapping[str, object],
) -> AuditRecord:
    if phase is AuditPhase.OUTPUT and holds.output_extras:
        extra = {**extra, **holds.output_extras}
    built = from_decision(
        DecisionRecord(
            request_id=holds.request_id,
            plan_version=decision.plan_version,
            decision=decision,
        ),
        org_id=org,
        phase=phase,
        recorded_at_ns=recorded_at_ns,
    )
    return _annotate(built, console, extra, stamp)


def _admission(
    holds: RequestHolds,
    org: str,
    refuse_code: str,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, object],
    stamp: Mapping[str, object],
) -> AuditRecord | None:
    try:
        status = errors.render_http(refuse_code, request_id=holds.request_id).status
    except Exception:
        return None
    outcome = SHED if refuse_code == OVERLOAD_SHED else REJECTED
    return _annotate(
        admission(
            request_id=holds.request_id,
            org_id=org,
            outcome=outcome,
            reason=refuse_code,
            recorded_at_ns=recorded_at_ns,
            status=status,
        ),
        console if extra.get("route") == _CHAT_ROUTE else None,
        extra,
        stamp,
    )


def _annotate(
    record: AuditRecord,
    console: Mapping[str, object] | None,
    extra: Mapping[str, object],
    stamp: Mapping[str, object],
) -> AuditRecord:
    detail = dict(record.detail)
    detail.update(extra)
    detail.update(stamp)
    if console is not None:
        detail["console"] = dict(console)
    return AuditRecord(
        request_id=record.request_id,
        org_id=record.org_id,
        phase=record.phase,
        outcome=record.outcome,
        recorded_at_ns=record.recorded_at_ns,
        detail=detail,
        schema_version=record.schema_version,
    )
