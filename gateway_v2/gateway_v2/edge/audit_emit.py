"""One audit record per phase, enqueued without touching the response already sent.

The callback is :meth:`gateway_v2.audit.sink.AuditSink.emit`: it does not await and
it does not raise when the queue is full. This module also swallows a broken
callback. A lost record is a counted drop. It is not a failed chat.

Streaming output does not yet keep a :class:`~gateway_v2.domain.decision.Decision`,
only an action string, so a stream emits the input record and not an output record.
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
from gateway_v2.domain.decision import Decision, DecisionRecord
from gateway_v2.domain.posture import OVERLOAD_SHED
from gateway_v2.edge import errors
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.request_holds import RequestHolds

_CHAT_ROUTE = "chat"


def emit_chat_audit(
    emit: Callable[[AuditRecord], bool] | None,
    holds: RequestHolds,
    refuse_code: str | None,
) -> None:
    """Enqueue the records this request can honestly name. Never raises."""
    if emit is None or not holds.request_id or not holds.org_id:
        return
    try:
        for record in _records(holds, refuse_code, time.time_ns()):
            emit(record)
    except Exception:
        return


def _records(
    holds: RequestHolds,
    refuse_code: str | None,
    recorded_at_ns: int,
) -> list[AuditRecord]:
    records: list[AuditRecord] = []
    output = holds.output_decision
    output_action = None if output is None else output.disposition.value
    console = console_from_holds(
        holds,
        output_action=output_action,
        provider_called=holds.dispatched,
    )
    if holds.input_decision is not None:
        records.append(
            _phase(holds, holds.input_decision, AuditPhase.INPUT, recorded_at_ns, console),
        )
    if output is not None:
        records.append(
            _phase(holds, output, AuditPhase.OUTPUT, recorded_at_ns, console),
        )
    if refuse_code and holds.input_decision is None and output is None:
        admitted = _admission(holds, refuse_code, recorded_at_ns)
        if admitted is not None:
            records.append(admitted)
    return records


def _phase(
    holds: RequestHolds,
    decision: Decision,
    phase: AuditPhase,
    recorded_at_ns: int,
    console: Mapping[str, object] | None,
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
    return _annotate(built, console)


def _admission(
    holds: RequestHolds,
    refuse_code: str,
    recorded_at_ns: int,
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
    )


def _annotate(record: AuditRecord, console: Mapping[str, object] | None) -> AuditRecord:
    detail = dict(record.detail)
    detail["route"] = _CHAT_ROUTE
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
