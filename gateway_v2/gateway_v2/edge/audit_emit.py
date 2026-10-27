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
    AuditPhase,
    AuditRecord,
    admission,
    from_decision,
)
from gateway_v2.domain.decision import Decision, DecisionRecord, Disposition
from gateway_v2.domain.posture import OVERLOAD_SHED
from gateway_v2.edge import errors
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.request_holds import RequestHolds

_CHAT_ROUTE = "chat"


def note_stream_output(holds: RequestHolds, stats: object, plan_version: str) -> None:
    """Record the stream's output decision. An allow with no decision is still output."""
    decision = getattr(stats, "output_decision", None)
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
) -> None:
    """Enqueue the records this request can honestly name. Never raises.

    ``route`` names the surface. A non-chat surface passes its own ``console`` (chat's
    is built here) and may add plain-string ``detail`` such as the MCP server and tool.
    """
    if emit is None or not holds.request_id or not holds.org_id:
        return
    try:
        if console is None and route == _CHAT_ROUTE:
            output = holds.output_decision
            console = console_from_holds(
                holds,
                output_action=None if output is None else output.disposition.value,
                provider_called=holds.dispatched,
            )
        extra = {"route": route, **(detail or {})}
        for record in _records(holds, refuse_code, time.time_ns(), console, extra):
            emit(record)
    except Exception:
        return


def _records(
    holds: RequestHolds,
    refuse_code: str | None,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
    extra: Mapping[str, str],
) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    output = holds.output_decision
    if holds.input_decision is not None:
        records.append(
            _phase(holds, holds.input_decision, AuditPhase.INPUT, recorded_at_ns, console, extra),
        )
    if output is not None:
        records.append(
            _phase(holds, output, AuditPhase.OUTPUT, recorded_at_ns, console, extra),
        )
    if refuse_code and holds.input_decision is None and output is None:
        admitted = _admission(holds, refuse_code, recorded_at_ns, extra)
        if admitted is not None:
            records.append(admitted)
    return records


def _phase(
    holds: RequestHolds,
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
        org_id=holds.org_id,
        phase=phase,
        recorded_at_ns=recorded_at_ns,
    )
    return _annotate(built, console, extra)


def _admission(
    holds: RequestHolds,
    refuse_code: str,
    recorded_at_ns: int,
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
            org_id=holds.org_id,
            outcome=outcome,
            reason=refuse_code,
            recorded_at_ns=recorded_at_ns,
            status=status,
        ),
        None,
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
