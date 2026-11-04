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
from gateway_v2.domain.posture import INVALID_API_KEY, OVERLOAD_SHED, SHARED_STATE_UNAVAILABLE
from gateway_v2.edge import errors
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.outcome_metrics import OutcomeObserver, observe_outcome
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.unauth_audit import UnauthAuditGate

_CHAT_ROUTE = "chat"
_IDENTITY_CODES = frozenset({INVALID_API_KEY, SHARED_STATE_UNAVAILABLE})


def note_stream_output(holds: RequestHolds, stats: object, plan_version: str) -> None:
    """Record the stream's output decision and cause. An allow with no decision is still output.

    A cut with an error code records ``failure_code`` (the first cause wins). A policy cut with no
    decision also records a ``block`` output action; a fault never touches the output action, so
    ``error`` cannot paint the guardrail stage. A kill-switch cut records nothing.
    """
    error = getattr(stats, "error", None)
    decision = getattr(stats, "output_decision", None)
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
    observe_outcome(observer, holds, refuse_code)
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
        for record in _records(holds, org, refuse_code, time.time_ns(), console, extra):
            emit(record)
    except Exception:
        return


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
    extra: Mapping[str, str],
) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    output = holds.output_decision
    if holds.input_decision is not None:
        records.append(
            _phase(
                holds, org, holds.input_decision, AuditPhase.INPUT, recorded_at_ns, console, extra,
            ),
        )
    if output is not None:
        records.append(
            _phase(holds, org, output, AuditPhase.OUTPUT, recorded_at_ns, console, extra),
        )
    elif holds.dispatched and holds.failure_code:
        failed = _output_failure(holds, org, recorded_at_ns, console, extra)
        if failed is not None:
            records.append(failed)
    if refuse_code and holds.input_decision is None and output is None:
        admitted = _admission(holds, org, refuse_code, recorded_at_ns, console, extra)
        if admitted is not None:
            records.append(admitted)
    return records


def _output_failure(
    holds: RequestHolds,
    org: str,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, str],
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
    return _annotate(built, console, extra)


def _phase(
    holds: RequestHolds,
    org: str,
    decision: Decision,
    phase: AuditPhase,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, str],
) -> AuditRecord:
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
    return _annotate(built, console, extra)


def _admission(
    holds: RequestHolds,
    org: str,
    refuse_code: str,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, str],
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
    )


def _annotate(
    record: AuditRecord, console: Mapping[str, object] | None, extra: Mapping[str, str]
) -> AuditRecord:
    detail = dict(record.detail)
    detail.update(extra)
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
